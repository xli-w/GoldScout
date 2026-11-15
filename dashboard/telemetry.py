"""Validate optional health observations without broker or accounting writes."""

import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dashboard.contracts import freshness
from dashboard.reader import decode, identity, utc_text


def process_matches(pid, created):
    try:
        import psutil
        process = psutil.Process(pid)
        return process.is_running() and math.isclose(process.create_time(), float(created), rel_tol=0, abs_tol=0.001)
    except Exception:
        return False


def unknown_health(snapshot):
    reasons = snapshot["overview"]["block_reasons"]
    return {
        "runtime": "unknown", "last_monitor_completed_at": None, "persistence_fault": None,
        "accounting_unresolved": True if "accounting_unresolved" in reasons else None,
        "daily_halted": True if "daily_halted" in reasons else None, "unmanaged_symbols": [],
        "activity_sync": {"checkpoint_at": snapshot.get("activity_checkpoint"), "status": "unknown",
                          "coverage": "bounded_overlap_not_full_history"},
        "task_failures": {}, "warnings": ["telemetry_missing"],
    }


class TelemetryReader:
    MAX_BYTES = 65536

    def __init__(self, path, process_check=None):
        self.path = Path(path)
        self.process_check = process_check or process_matches

    def augment(self, snapshot, now):
        health = unknown_health(snapshot)
        snapshot["health"] = health
        try:
            with self.path.open("rb") as source:
                encoded = source.read(self.MAX_BYTES + 1)
            if len(encoded) > self.MAX_BYTES:
                raise ValueError("oversized")
            payload = decode(encoded)
            self.validate(payload, snapshot, now)
            observed = utc_text(payload["observed_at"])
            status = freshness(observed, now, 90)
            snapshot["meta"]["telemetry"] = {"observed_at": observed, "freshness": status, "max_age_seconds": 90}
            snapshot["meta"]["telemetry_run_id"] = payload["run_id"]
            monitor = payload["last_monitor_completed_at"]
            health["last_monitor_completed_at"] = monitor
            health["warnings"] = []
            if status != "fresh":
                health["warnings"].append("telemetry_stale")
                if payload["persistence_fault"]:
                    health["warnings"].append("persistence_fault_last_reported")
                return snapshot
            if not self.process_check(payload["pid"], payload["process_created_at"]):
                snapshot["meta"]["telemetry"]["freshness"] = "invalid"
                health["warnings"].append("process_instance_unverified")
                if payload["persistence_fault"]:
                    health["warnings"].append("persistence_fault_last_reported")
                return snapshot
            for flag in ("accounting_unresolved", "daily_halted"):
                health[flag] = health[flag] is True or payload[flag]
            health.update({key: payload[key] for key in ("persistence_fault", "unmanaged_symbols", "task_failures")})
            monitor_fresh = freshness(monitor, now, 90) == "fresh"
            if payload["persistence_fault"]:
                health["runtime"] = "faulted"
                health["warnings"].append("software_monitoring_stopped")
            elif payload["runtime"] == "stopped":
                health["runtime"] = "stopped"
                health["warnings"].append("software_monitoring_stopped")
            elif payload["runtime"] == "running" and monitor_fresh and "monitor" not in payload["task_failures"]:
                health["runtime"] = "running"
            else:
                health["warnings"].append("monitor_observation_stale")
            health["warnings"].extend("task_failed_" + name for name in payload["task_failures"])
            sync_status = payload["activity_sync"]["status"]
            if sync_status == "incomplete" or health["activity_sync"]["checkpoint_at"] is not None:
                health["activity_sync"]["status"] = sync_status
            if sync_status == "incomplete":
                health["warnings"].append("activity_sync_incomplete")
            reasons = sorted(set(snapshot["overview"]["block_reasons"] + payload["block_reasons"]))
            reasons = sorted(set(reasons + [flag for flag in ("persistence_fault", "accounting_unresolved", "daily_halted") if health[flag]]))
            snapshot["overview"]["block_reasons"] = reasons
            snapshot["overview"]["entries"] = "blocked" if reasons else "unknown"
            if health["accounting_unresolved"]:
                snapshot["overview"]["realized_pnl"] = None
            if health["runtime"] == "running" and monitor_fresh:
                for position in snapshot["positions"]:
                    sample = payload["protection"].get(position["symbol"])
                    if (sample and sample["verified"] and sample["position_id"] == position["position_id"]
                            and Decimal(sample["quantity"]) == Decimal(position["quantity"])
                            and sample["broker_stop"] is not None and position["broker_stop"] is not None
                            and Decimal(sample["broker_stop"]) == Decimal(position["broker_stop"])
                            and freshness(sample["observed_at"], now, 90) == "fresh"
                            and position["protection"] != "unverified"
                            and not position["exit_pending"]):
                        position["protection"] = "last_verified"
                        position["protection_observed_at"] = sample["observed_at"]
            return snapshot
        except FileNotFoundError:
            return snapshot
        except Exception:
            snapshot["meta"]["telemetry"] = {"observed_at": None, "freshness": "invalid", "max_age_seconds": 90}
            snapshot["meta"]["telemetry_run_id"] = None
            snapshot["health"] = unknown_health(snapshot)
            snapshot["health"]["warnings"] = ["telemetry_invalid"]
            return snapshot

    @staticmethod
    def validate(payload, snapshot, now):
        if not isinstance(payload, dict) or isinstance(payload.get("version"), bool) or payload.get("version") != 1:
            raise ValueError("version")
        if payload.get("database_id") != snapshot["meta"]["database_id"] or payload.get("environment") != snapshot["meta"]["environment"]:
            raise ValueError("binding")
        identity(payload.get("run_id"))
        pid = payload.get("pid")
        created = payload.get("process_created_at")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid")
        if isinstance(created, bool) or not isinstance(created, (int, float, Decimal)) or not math.isfinite(float(created)) or created <= 0:
            raise ValueError("process")
        observed = utc_text(payload["observed_at"])
        observed_time = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if freshness(observed, now, 90) == "invalid":
            raise ValueError("time")
        monitor = payload["last_monitor_completed_at"]
        if monitor is not None and (datetime.fromisoformat(utc_text(monitor).replace("Z", "+00:00")) > observed_time or freshness(monitor, now, 90) == "invalid"):
            raise ValueError("monitor")
        if payload["runtime"] not in ("starting", "running", "stopped", "faulted"):
            raise ValueError("runtime")
        for flag in ("persistence_fault", "accounting_unresolved", "daily_halted"):
            if not isinstance(payload[flag], bool):
                raise ValueError("flag")
        for name in ("block_reasons", "unmanaged_symbols"):
            values = payload[name]
            if not isinstance(values, list) or len(values) > 32 or len(set(values)) != len(values):
                raise ValueError("list")
            for value in values:
                identity(value)
        failures = payload["task_failures"]
        if not isinstance(failures, dict) or len(failures) > 32:
            raise ValueError("failures")
        for name, count in failures.items():
            identity(name)
            if len(name) > 64:
                raise ValueError("task name")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("count")
        if payload["activity_sync"]["status"] not in ("unknown", "incomplete", "last_observed_complete"):
            raise ValueError("sync")
        samples = payload["protection"]
        if not isinstance(samples, dict) or len(samples) > 32:
            raise ValueError("samples")
        for symbol, sample in samples.items():
            identity(symbol)
            identity(sample["position_id"])
            if not isinstance(sample["verified"], bool) or datetime.fromisoformat(utc_text(sample["observed_at"]).replace("Z", "+00:00")) > observed_time:
                raise ValueError("protection")
            for field in ("quantity", "broker_stop"):
                value = sample[field]
                if field == "broker_stop" and value is None:
                    continue
                if not isinstance(value, str) or not Decimal(value).is_finite() or Decimal(value) <= 0:
                    raise ValueError("decimal")