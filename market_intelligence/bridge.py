"""Best-effort local bot observation; never modifies a signal or calls a broker."""

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import queue
import threading
import uuid

from market_intelligence.policy import evaluate_calendar
from market_intelligence.store import IntelligenceStore


_CLOSED_OBSERVERS = set()
_CLOSED_OBSERVERS_LOCK = threading.Lock()


def _remember_closed(observer_id):
    with _CLOSED_OBSERVERS_LOCK:
        _CLOSED_OBSERVERS.add(observer_id)


def _forget_closed(observer_id):
    with _CLOSED_OBSERVERS_LOCK:
        _CLOSED_OBSERVERS.discard(observer_id)


def _is_locally_closed(observer_id):
    with _CLOSED_OBSERVERS_LOCK:
        return observer_id in _CLOSED_OBSERVERS


class BotIntelligenceObserver:
    def __init__(self, path):
        self.path=Path(path)
        self.observer_id=uuid.uuid4().hex
        self.status_path=self.path.with_suffix(".bot-observer-status.json")
        self.closed_path=self.path.with_name(
            self.path.stem+f".bot-observer-{self.observer_id}.closed.json")
        self.stop=threading.Event()
        self.queue=queue.Queue(maxsize=1)
        self._write_status()
        self.thread=threading.Thread(target=self._run,name="bot-intelligence-observer",daemon=True)
        self.thread.start()

    @staticmethod
    def _write_json(path,payload):
        temporary=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            temporary.write_text(json.dumps(payload),encoding="utf-8")
            os.replace(temporary,path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except FileNotFoundError:
                pass

    def _write_status(self):
        self._write_json(self.status_path,{"observer_id":self.observer_id,"closed":False})

    def _write_closed_marker(self):
        self._write_json(self.closed_path,{"observer_id":self.observer_id})

    @staticmethod
    def _bounded_file_action(action, timeout=0.1):
        try:
            completed=threading.Event()
            errors=[]

            def run():
                try:
                    action()
                except Exception as exc:
                    errors.append(exc)
                finally:
                    completed.set()

            writer=threading.Thread(target=run,name="bot-observer-close-marker",daemon=True)
            writer.start()
            if not completed.wait(timeout=timeout):
                return TimeoutError("Observer sidecar operation exceeded its shutdown budget")
            return errors[0] if errors else None
        except Exception as exc:
            return exc

    def _remove_owned_status(self):
        try:
            if self.status_path.is_symlink() or self.status_path.stat().st_size>256:
                raise OSError("Observer status cannot be safely inspected")
            with self.status_path.open("rb") as handle:
                status=json.loads(handle.read(257))
        except FileNotFoundError:
            return
        except (OSError,ValueError,TypeError) as exc:
            raise OSError("Observer status could not be verified") from exc
        if not isinstance(status,dict):
            raise OSError("Observer status is not a JSON object")
        status_observer_id=status.get("observer_id")
        if status_observer_id==self.observer_id:
            try:
                self.status_path.unlink()
            except FileNotFoundError:
                pass
        elif not isinstance(status_observer_id,str) or not status_observer_id:
            raise OSError("Observer status ownership could not be verified")

    def record(self,symbol,action):
        try:
            self.queue.put_nowait((symbol,action.lower()))
        except queue.Full:
            pass

    def close(self):
        self.stop.set()
        _remember_closed(self.observer_id)
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        marker_error=self._bounded_file_action(self._write_closed_marker)
        close_error=None
        if marker_error is not None:
            invalidation_error=self._bounded_file_action(self._remove_owned_status, timeout=0.5)
            if invalidation_error is not None:
                close_error=OSError("Observer status could not be invalidated")
                logging.getLogger("bot.intelligence").critical(
                    "Observer close marker failed (%s); status invalidation also failed",
                    marker_error,
                    exc_info=(type(invalidation_error),invalidation_error,invalidation_error.__traceback__))
            else:
                _forget_closed(self.observer_id)
                logging.getLogger("bot.intelligence").warning(
                    "Observer close marker unavailable; observer status is no longer active")
        else:
            _forget_closed(self.observer_id)
        self.thread.join(timeout=0.5)
        if close_error is not None:
            raise close_error from invalidation_error

    def _run(self):
        try:
            import psutil
            started=psutil.Process().create_time()
        except Exception:
            logging.getLogger("bot.intelligence").warning("Intelligence observation unavailable; trading unchanged")
            return
        while not self.stop.is_set():
            try:
                item=self.queue.get(timeout=10)
            except queue.Empty:
                item=("SPY","hold")
            if self.stop.is_set():
                break
            symbol,action=item
            target=self.path.with_suffix(".bot-observation.json")
            temporary=target.with_name(target.name+"."+uuid.uuid4().hex+".tmp")
            try:
                store=IntelligenceStore(self.path)
                settings=store.settings()
                if self.stop.is_set():
                    break
                calendar=store.calendar()
                if self.stop.is_set():
                    break
                now=datetime.now(timezone.utc)
                decision=evaluate_calendar(settings,calendar,symbol,action,now)
                payload={"observer_id":self.observer_id,"database_id":store.database_id,
                         "settings_revision":settings.revision,
                         "observed_at":now.isoformat(),"pid":os.getpid(),"process_started_at":started,
                         "outcome":decision.outcome,"affects_trading":False}
                if settings.enabled and settings.mode=="shadow" and action!="hold":
                    logging.getLogger("bot.intelligence").info("Shadow %s %s: %s",symbol,action,decision.outcome)
                if self.stop.is_set():
                    break
                temporary.write_text(json.dumps(payload),encoding="utf-8")
                if self.stop.is_set():
                    temporary.unlink(missing_ok=True)
                    break
                os.replace(temporary,target)
            except Exception:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def _observer_is_active(status_path,closed_path,observer_id):
    if _is_locally_closed(observer_id):
        return False
    try:
        if status_path.is_symlink() or status_path.stat().st_size>256:
            return False
        with status_path.open("rb") as handle:
            status=json.loads(handle.read(257))
        if (not isinstance(status,dict) or status.get("observer_id")!=observer_id
                or status.get("closed") is not False):
            return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    try:
        if closed_path.is_symlink() or closed_path.stat().st_size>256:
            return False
        with closed_path.open("rb") as handle:
            closed=json.loads(handle.read(257))
        if closed.get("observer_id")==observer_id:
            return False
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return not _is_locally_closed(observer_id)


def observed_revision(path,database_id,now):
    target=Path(path).with_suffix(".bot-observation.json")
    try:
        if target.is_symlink() or target.stat().st_size>4096:
            return None
        with target.open("rb") as handle:
            payload=json.loads(handle.read(4097))
        observer_id=payload.get("observer_id")
        if (not isinstance(observer_id,str) or len(observer_id)!=32
                or any(char not in "0123456789abcdef" for char in observer_id)):
            return None
        status_path=Path(path).with_suffix(".bot-observer-status.json")
        closed_path=Path(path).with_name(
            Path(path).stem+f".bot-observer-{observer_id}.closed.json")
        if not _observer_is_active(status_path,closed_path,observer_id):
            return None
        stamp=datetime.fromisoformat(payload["observed_at"])
        import psutil
        process=psutil.Process(payload["pid"])
        if (payload["database_id"]==database_id and payload["affects_trading"] is False
                and type(payload["settings_revision"]) is int and payload["settings_revision"]>=0
                and 0<=(now-stamp).total_seconds()<=30 and process.is_running()
                and process.create_time()==payload["process_started_at"]
                and _observer_is_active(status_path,closed_path,observer_id)):
            return payload["settings_revision"]
    except Exception:
        pass
    return None