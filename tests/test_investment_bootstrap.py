"""First-run bootstrap checks use synthetic evidence and temporary stores only."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import unittest
from unittest.mock import patch

from dashboard import workspace
from dashboard.workspace import resolve_workspace, workspace_status
from investment import InvestmentError, bootstrap, store
from investment.core import iso


NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def profile():
    return {"schema_version": 2, "portfolio_id": "bootstrap-test", "currency": "GBP",
            "created_at": iso(NOW), "policy": None, "products": [],
            "accounts": [{"id": "bank", "name": "Synthetic savings", "kind": "deposit",
                          "binding": "synthetic-bank", "currency": "GBP"}]}


def opening():
    return {"id": "opening", "type": "opening", "timestamp": iso(NOW),
            "available_at": iso(NOW), "evidence": "synthetic statement",
            "balances_gbp": {"bank": "1000.01"}, "holdings": []}


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "workspace"
        self.database = self.path / "investment.sqlite3"

    def preview(self):
        return bootstrap.preview_setup(self.path, profile(), opening(), NOW)

    def confirm(self, preview=None):
        return bootstrap.confirm_setup(self.path, preview or self.preview(), NOW)

    def test_absence_status_and_preview_create_nothing(self):
        self.assertEqual(workspace_status(self.path, NOW)["state"], "empty")
        self.assertEqual(bootstrap.workspace_status(self.path, NOW),
                         workspace_status(self.path, NOW))
        preview = self.preview()
        self.assertEqual(preview["report"]["equity_gbp"], "1000.01")
        self.assertFalse(self.path.exists())
        self.assertEqual(list(self.root.iterdir()), [])
        self.path.mkdir()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "empty")
        self.preview()
        self.assertEqual(list(self.path.iterdir()), [])

    def test_default_is_checkout_anchored_and_explicit_is_not_relocated(self):
        self.assertEqual(resolve_workspace(),
                         Path(workspace.__file__).absolute().parent.parent / "local-investment")
        self.assertEqual(resolve_workspace(self.path), self.path)
        with patch("dashboard.workspace.Path.is_file", return_value=False):
            with self.assertRaisesRegex(InvestmentError, "--workspace"):
                resolve_workspace()

    def test_etoro_state_is_separate_and_unexpected_entries_stay_blocked(self):
        state = self.path / ".etoro"
        state.mkdir(parents=True)
        (state / "connection.json").write_text("{}", encoding="utf-8")
        (state / "startup.lock").write_bytes(b"0")
        (state / "connection.lock").write_bytes(b"0")
        self.assertEqual(workspace_status(self.path, NOW)["state"], "empty")
        self.preview()
        self.assertEqual(
            {item.name for item in state.iterdir()}, {"connection.json", "startup.lock", "connection.lock"})

        (state / "unexpected.json").write_text("{}", encoding="utf-8")
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")

    def test_confirm_closed_validated_journal_exact_retry_and_restart(self):
        preview = self.preview()
        result = self.confirm(preview)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["committed"])
        self.assertEqual(store.read(self.database, NOW).cash["bank"].as_tuple().exponent, -2)
        self.assertEqual(store.read(self.database, NOW).revision, 1)
        original = self.database.read_bytes()
        receipt = (self.path / ".investment-setup" / "receipt.json").read_bytes()
        self.assertEqual(self.confirm(preview)["receipt"], result["receipt"])
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual((self.path / ".investment-setup" / "receipt.json").read_bytes(), receipt)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")
        recovered = bootstrap.recovery_preview(self.path, NOW)
        self.assertEqual(self.confirm(recovered)["receipt"], result["receipt"])
        self.assertFalse((self.path / ".investment-setup" / "journal.sqlite3").exists())
        bindings = []
        expired = bootstrap.confirm_setup(
            self.path, preview, NOW + timedelta(hours=1), on_completed=bindings.append)
        self.assertEqual(expired["receipt"], result["receipt"])
        self.assertEqual(bindings, [])

    def test_manual_unopened_and_opened_missing_profile_remain_authoritative(self):
        self.path.mkdir()
        store.initialize(self.database, profile(), NOW)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "manual_unopened")
        with self.assertRaises(InvestmentError):
            self.preview()
        store.import_events(self.database, [opening()], NOW)
        result = workspace_status(self.path, NOW)
        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["issues"])
        with self.assertRaisesRegex(InvestmentError, "not a wizard"):
            self.preview()

    def test_profile_only_corrupt_future_and_sidecar_states_block_without_writes(self):
        self.path.mkdir()
        exported = self.path / "profile.json"
        exported.write_text(store.canonical(profile()), encoding="utf-8")
        before = exported.read_bytes()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        self.assertEqual(exported.read_bytes(), before)
        exported.unlink()
        self.database.write_bytes(b"not sqlite")
        before = self.database.read_bytes()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        self.assertEqual(self.database.read_bytes(), before)
        self.database.unlink()
        store.initialize(self.database, profile(), NOW)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version=999")
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        self.database.unlink()
        (self.path / "investment.sqlite3-wal").touch()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")

    def test_preview_rejects_bad_evidence_without_creating_directory(self):
        for modify in (
                lambda p, e: e["balances_gbp"].clear(),
                lambda p, e: e["balances_gbp"].update(bank="NaN"),
                lambda p, e: e.update(timestamp=iso(NOW + timedelta(days=1))),
                lambda p, e: p["accounts"].append(deepcopy(p["accounts"][0])),
                lambda p, e: p.update(schema_version=1)):
            spec, evidence = profile(), opening()
            modify(spec, evidence)
            with self.assertRaises(InvestmentError):
                bootstrap.preview_setup(self.path, spec, evidence, NOW)
            self.assertFalse(self.path.exists())

    def test_tamper_expiry_and_target_substitution_write_nothing(self):
        preview = self.preview()
        changed = deepcopy(preview)
        changed["opening"]["balances_gbp"]["bank"] = "9"
        with self.assertRaisesRegex(InvestmentError, "binding"):
            self.confirm(changed)
        with self.assertRaisesRegex(InvestmentError, "expired"):
            bootstrap.confirm_setup(self.path, preview, NOW + timedelta(hours=1))
        self.assertFalse(self.path.exists())
        self.path.mkdir()
        (self.path / "occupied").write_bytes(b"preserve")
        with self.assertRaises(InvestmentError):
            self.confirm(preview)
        self.assertEqual((self.path / "occupied").read_bytes(), b"preserve")

    def test_competing_previews_cannot_clobber_completed_operation(self):
        first, second = self.preview(), self.preview()
        self.assertEqual(self.confirm(first)["status"], "completed")
        before = self.database.read_bytes()
        with self.assertRaisesRegex(InvestmentError, "Conflicting"):
            self.confirm(second)
        self.assertEqual(self.database.read_bytes(), before)

    def test_simultaneous_confirmation_serializes_or_fails_closed(self):
        preview = self.preview()
        barrier = Barrier(2)

        def attempt():
            barrier.wait(timeout=10)
            try:
                return self.confirm(preview)["status"]
            except InvestmentError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: attempt(), range(2)))
        self.assertIn("completed", results)
        self.assertEqual(store.read(self.database, NOW).revision, 1)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")

    def test_unknown_held_price_stays_incomplete(self):
        spec, evidence = profile(), opening()
        spec["accounts"][0]["kind"] = "broker"
        spec["products"] = [{"id": "fund", "name": "Synthetic fund", "sleeve": "equity",
                             "currency": "GBP", "quantity_increment": "0.01",
                             "minimum_notional_gbp": "1"}]
        evidence["holdings"] = [{"account_id": "bank", "product_id": "fund", "quantity": "1.25",
                                 "price_gbp": None}]
        preview = bootstrap.preview_setup(self.path, spec, evidence, NOW)
        self.assertIsNone(preview["report"]["equity_gbp"])
        self.assertFalse(self.path.exists())

    def test_failed_initialization_claim_and_revision_zero_recover_with_exact_evidence(self):
        preview = self.preview()
        with patch.object(store, "initialize", side_effect=InvestmentError("injected")):
            result = self.confirm(preview)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["committed"])
        status = workspace_status(self.path, NOW)
        self.assertEqual(status["state"], "recoverable")
        self.assertEqual(status["recovery"], {"can_preview": False, "requires_opening": True,
                                              "profile": profile(), "opening_id": "opening"})
        with self.assertRaisesRegex(InvestmentError, "re-entered"):
            bootstrap.recovery_preview(self.path, NOW)
        with patch.object(store, "import_events", side_effect=InvestmentError("injected")):
            result = self.confirm(bootstrap.preview_setup(self.path, profile(), opening(), NOW))
        self.assertFalse(result["committed"])
        stage = self.path / ".investment-setup" / "journal.sqlite3"
        self.assertEqual(store.read(stage, NOW).revision, 0)
        self.assertFalse(workspace_status(self.path, NOW)["recovery"]["can_preview"])
        bad = opening()
        bad["balances_gbp"]["bank"] = "9"
        with self.assertRaisesRegex(InvestmentError, "does not match"):
            bootstrap.preview_setup(self.path, profile(), bad, NOW)
        self.assertEqual(self.confirm(self.preview())["status"], "completed")
        self.assertEqual(store.read(self.database, NOW).revision, 1)

    def test_post_commit_close_failures_preserve_staged_setup_evidence(self):
        from test_investment_opening import ConnectionCloseFailure

        initialize = store.initialize
        connect = sqlite3.connect
        store_connect = store._connect

        def fail_initialization_close(database, spec, now):
            with patch.object(store.sqlite3, "connect",
                              side_effect=lambda *args, **kwargs: ConnectionCloseFailure(
                                  connect(*args, **kwargs))):
                return initialize(database, spec, now)

        def fail_import_close(database, readonly):
            connection = store_connect(database, readonly)
            return connection if readonly else ConnectionCloseFailure(connection)

        for operation, revision in (("initialize", 0), ("import", 1)):
            with self.subTest(operation=operation):
                self.path = self.root / operation
                self.database = self.path / "investment.sqlite3"
                if operation == "initialize":
                    failure = patch.object(store, "initialize", side_effect=fail_initialization_close)
                else:
                    failure = patch.object(store, "_connect", side_effect=fail_import_close)
                with failure:
                    result = self.confirm()
                stage = self.path / ".investment-setup" / "journal.sqlite3"
                self.assertEqual(result["status"], "partial")
                self.assertFalse(result["committed"])
                self.assertIn(f"revision {revision}", result["message"])
                self.assertFalse(self.database.exists())
                self.assertEqual(store.read(stage, NOW).revision, revision)
                status = workspace_status(self.path, NOW)
                self.assertEqual(status["state"], "recoverable")
                self.assertEqual(status["recovery"]["can_preview"], revision == 1)
                preview = bootstrap.recovery_preview(self.path, NOW) if revision else self.preview()
                self.assertEqual(self.confirm(preview)["status"], "completed")
                self.assertEqual(store.read(self.database, NOW).revision, 1)
                self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")

    def test_publication_failure_preserves_opened_stage_and_recovery(self):
        with patch.object(os, "link", side_effect=OSError("no primitive")):
            result = self.confirm()
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["committed"])
        self.assertFalse(self.database.exists())
        self.assertEqual(workspace_status(self.path, NOW)["state"], "recoverable")
        self.assertTrue(workspace_status(self.path, NOW)["recovery"]["can_preview"])
        preview = bootstrap.recovery_preview(self.path, NOW)
        self.assertEqual(self.confirm(preview)["status"], "completed")

    def test_published_journal_stays_committed_when_all_verification_reads_fail(self):
        original_read = bootstrap._read
        failures = []

        def unavailable_after_publication(path, now):
            if Path(path) == self.database and self.database.exists():
                failures.append(True)
                raise InvestmentError("temporarily unavailable after publication")
            return original_read(path, now)

        with patch.object(bootstrap, "_read", side_effect=unavailable_after_publication):
            result = self.confirm()
        self.assertGreaterEqual(len(failures), 2)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(result["revision"], 1)
        self.assertTrue(self.database.is_file())
        original = self.database.read_bytes()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "recoverable")
        preview = bootstrap.recovery_preview(self.path, NOW)
        self.assertEqual(self.confirm(preview)["status"], "completed")
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(store.read(self.database, NOW).revision, 1)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")

    def test_lost_publication_result_uses_stage_identity_when_reads_fail(self):
        original_link = os.link
        original_read = bootstrap._read
        stage = self.path / ".investment-setup" / "journal.sqlite3"

        def lose_link_result(source, destination):
            original_link(source, destination)
            raise OSError("synthetic lost publication result")

        def fail_published_read(path, now):
            if Path(path) == self.database and self.database.exists():
                raise InvestmentError("synthetic published journal read failure")
            return original_read(path, now)

        with patch.object(os, "link", side_effect=lose_link_result), \
                patch.object(bootstrap, "_read", side_effect=fail_published_read):
            result = self.confirm()
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(result["revision"], 1)
        self.assertTrue(os.path.samefile(stage, self.database))
        self.assertEqual(store.read(self.database, NOW).revision, 1)

    def test_unverifiable_lost_publication_result_is_not_reported_uncommitted(self):
        original_link = os.link
        original_read = bootstrap._read

        def lose_link_result(source, destination):
            original_link(source, destination)
            raise OSError("synthetic lost publication result")

        def fail_published_read(path, now):
            if Path(path) == self.database and self.database.exists():
                raise InvestmentError("synthetic published journal read failure")
            return original_read(path, now)

        with patch.object(os, "link", side_effect=lose_link_result), \
                patch.object(bootstrap, "_read", side_effect=fail_published_read), \
                patch.object(os.path, "samefile", side_effect=OSError("identity unavailable")):
            with self.assertRaisesRegex(InvestmentError, "uncertain"):
                self.confirm()
        self.assertTrue(self.database.exists())
        self.assertEqual(store.read(self.database, NOW).revision, 1)

    def test_recovery_preserves_known_published_journal_when_later_reads_fail(self):
        original_write = bootstrap._write_new

        def fail_receipt(path, value):
            if path.name == bootstrap.RECEIPT:
                raise OSError("synthetic receipt failure")
            return original_write(path, value)

        with patch("investment.bootstrap._write_new", side_effect=fail_receipt):
            partial = self.confirm()
        self.assertTrue(partial["committed"])
        recovery = bootstrap.recovery_preview(self.path, NOW)
        original = self.database.read_bytes()
        original_read = bootstrap._read
        reads = []

        def fail_after_initial(path, now):
            if Path(path) == self.database:
                reads.append(path)
                if len(reads) > 1:
                    raise InvestmentError("synthetic later read failure")
            return original_read(path, now)

        with patch("investment.bootstrap._read", side_effect=fail_after_initial):
            result = self.confirm(recovery)
        self.assertGreater(len(reads), 1)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "recoverable")
        self.assertEqual(self.confirm(bootstrap.recovery_preview(self.path, NOW))["status"],
                         "completed")

    def test_recovery_preserves_verified_commit_when_claim_reread_fails(self):
        for recoverable in (False, True):
            with self.subTest(recoverable=recoverable):
                self.path = self.root / f"claim-reread-{recoverable}"
                self.database = self.path / "investment.sqlite3"
                if recoverable:
                    original_write = bootstrap._write_new

                    def fail_receipt(path, value):
                        if path.name == bootstrap.RECEIPT:
                            raise OSError("synthetic receipt failure")
                        return original_write(path, value)

                    with patch.object(bootstrap, "_write_new", side_effect=fail_receipt):
                        result = self.confirm()
                    self.assertTrue(result["committed"])
                else:
                    self.assertEqual(self.confirm()["status"], "completed")
                preview = bootstrap.recovery_preview(self.path, NOW)
                original = self.database.read_bytes()
                original_claim = bootstrap._claim
                reads = []

                def fail_second_claim(path):
                    reads.append(path)
                    if len(reads) == 2:
                        raise InvestmentError("synthetic claim read failure")
                    return original_claim(path)

                with patch.object(bootstrap, "_claim", side_effect=fail_second_claim):
                    result = self.confirm(preview)
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["committed"])
                self.assertEqual(result["revision"], 1)
                self.assertEqual(self.database.read_bytes(), original)

    def test_recovery_preserves_verified_commit_when_claim_reread_is_undecodable(self):
        self.assertEqual(self.confirm()["status"], "completed")
        preview = bootstrap.recovery_preview(self.path, NOW)
        original = self.database.read_bytes()
        original_claim = bootstrap._claim
        reads = []

        def undecodable_second_claim(path):
            reads.append(path)
            if len(reads) == 2:
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic invalid byte")
            return original_claim(path)

        with patch.object(bootstrap, "_claim", side_effect=undecodable_second_claim):
            result = self.confirm(preview)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(result["revision"], 1)
        self.assertEqual(self.database.read_bytes(), original)

    def test_recovery_never_replaces_disappeared_or_substituted_published_journal(self):
        from test_investment_opening import event

        for change in ("missing", "replace-after-status", "replace-before-identity",
                       "missing-after-first-status"):
            with self.subTest(change=change):
                substitute = change.startswith("replace")
                self.path = self.root / f"recovery-{change}"
                self.database = self.path / "investment.sqlite3"
                original_write = bootstrap._write_new

                def fail_export(path, value):
                    if path.name == "profile.json":
                        raise OSError("synthetic export failure")
                    return original_write(path, value)

                with patch.object(bootstrap, "_write_new", side_effect=fail_export):
                    partial = self.confirm()
                self.assertTrue(partial["committed"])
                store.import_events(
                    self.database, [event("later-interest", "interest", NOW, "bank", amount_gbp="1")],
                    NOW)
                preview = bootstrap.recovery_preview(self.path, NOW)
                original = self.database.read_bytes()
                moved = self.root / f"preserved-{change}.sqlite3"
                replacement = self.root / f"replacement-{change}.sqlite3"
                if substitute:
                    store.initialize(replacement, profile(), NOW)
                    store.import_events(replacement, [opening()], NOW)
                    replacement_bytes = replacement.read_bytes()
                original_status = workspace_status
                original_identity = workspace.path_identity
                calls = []

                def replace_database():
                    self.database.replace(moved)
                    if substitute:
                        replacement.replace(self.database)

                def change_after_locked_status(path, now=None):
                    result = original_status(path, now)
                    calls.append(True)
                    if len(calls) == (1 if change == "missing-after-first-status" else 2):
                        replace_database()
                    return result

                def change_before_identity_snapshot(path):
                    if Path(path) == self.database:
                        calls.append(True)
                        if len(calls) == 2:
                            replace_database()
                    return original_identity(path)

                if change == "replace-before-identity":
                    failure = patch("dashboard.workspace.path_identity",
                                    side_effect=change_before_identity_snapshot)
                else:
                    failure = patch("dashboard.workspace.workspace_status",
                                    side_effect=change_after_locked_status)
                with failure:
                    result = self.confirm(preview)
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["committed"])
                self.assertEqual(moved.read_bytes(), original)
                if substitute:
                    self.assertEqual(self.database.read_bytes(), replacement_bytes)
                else:
                    self.assertFalse(self.database.exists())

    def test_claim_and_cleanup_failures_preserve_evidence(self):
        with patch.object(bootstrap, "_write_new", side_effect=OSError("claim failure")):
            result = self.confirm()
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["committed"])
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        self.assertFalse(self.database.exists())
        # A distinct temporary workspace exercises post-publication cleanup.
        self.path = self.root / "cleanup"
        self.database = self.path / "investment.sqlite3"
        with patch.object(bootstrap, "_cleanup_stage", side_effect=OSError("cleanup failure")):
            result = self.confirm()
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(store.read(self.database, NOW).revision, 1)
        original = self.database.read_bytes()
        status = workspace_status(self.path, NOW)
        self.assertEqual(status["state"], "recoverable")
        self.assertTrue(status["recovery"]["can_preview"])
        self.assertEqual(self.confirm(bootstrap.recovery_preview(self.path, NOW))["status"],
                         "completed")
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")

    def test_replaced_recovery_artifact_is_not_adopted(self):
        with patch.object(os, "link", side_effect=OSError("publication failure")):
            self.confirm()
        preview = bootstrap.recovery_preview(self.path, NOW)
        stage = self.path / ".investment-setup" / "journal.sqlite3"
        replacement = self.root / "replacement.sqlite3"
        store.initialize(replacement, profile(), NOW)
        store.import_events(replacement, [opening()], NOW)
        stage.unlink()
        replacement.rename(stage)
        with self.assertRaisesRegex(InvestmentError, "replaced"):
            self.confirm(preview)
        self.assertFalse(self.database.exists())

    def test_stage_substitution_during_final_publication_check_is_blocked(self):
        replacement = self.root / "replacement.sqlite3"
        store.initialize(replacement, profile(), NOW)
        store.import_events(replacement, [opening()], NOW)
        original_status = workspace_status
        substituted = []

        def substitute(path, now=None):
            result = original_status(path, now)
            stage = self.path / ".investment-setup" / "journal.sqlite3"
            if stage.exists() and not substituted:
                stage.unlink()
                replacement.rename(stage)
                substituted.append(True)
            return result

        with patch("dashboard.workspace.workspace_status", side_effect=substitute):
            result = self.confirm()
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["committed"])
        self.assertIn("identity changed", result["message"])
        self.assertFalse(self.database.exists())

    def test_receipt_type_substitution_blocks_without_rewriting(self):
        self.confirm()
        receipt_path = self.path / ".investment-setup" / "receipt.json"
        receipt = store.load_json(receipt_path)
        receipt["revision"] = True
        receipt_path.write_text(store.canonical(receipt), encoding="utf-8")
        before = receipt_path.read_bytes()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        self.assertEqual(receipt_path.read_bytes(), before)

    def test_profile_and_receipt_failures_report_committed_and_retry_safely(self):
        original_write = bootstrap._write_new
        for failed_name in ("profile.json", "receipt.json"):
            with self.subTest(failed_name=failed_name):
                self.path = self.root / failed_name
                self.database = self.path / "investment.sqlite3"
                preview = self.preview()

                def failing(path, value):
                    if path.name == failed_name:
                        raise OSError("injected ancillary failure")
                    return original_write(path, value)

                with patch.object(bootstrap, "_write_new", side_effect=failing):
                    result = self.confirm(preview)
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["committed"])
                status = workspace_status(self.path, NOW)
                self.assertEqual(status["state"], "recoverable")
                self.assertTrue(status["recovery"]["can_preview"])
                self.assertEqual(store.read(self.database, NOW).revision, 1)
                original = self.database.read_bytes()
                self.assertEqual(self.confirm(bootstrap.recovery_preview(self.path, NOW))["status"],
                                 "completed")
                self.assertEqual(store.read(self.database, NOW).revision, 1)
                self.assertEqual(self.database.read_bytes(), original)
                self.assertEqual(workspace_status(self.path, NOW)["state"], "ready")

    def test_expired_receipt_retry_cannot_hide_missing_profile_completion(self):
        preview = self.preview()
        self.assertEqual(self.confirm(preview)["status"], "completed")
        (self.path / "profile.json").unlink()
        later = NOW + timedelta(hours=1)
        original = self.database.read_bytes()
        self.assertEqual(workspace_status(self.path, later)["state"], "recoverable")
        with self.assertRaisesRegex(InvestmentError, "expired"):
            bootstrap.confirm_setup(self.path, preview, later)
        recovered = bootstrap.recovery_preview(self.path, later)
        self.assertEqual(bootstrap.confirm_setup(self.path, recovered, later)["status"],
                         "completed")
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(workspace_status(self.path, later)["state"], "ready")

    def test_expired_retry_preserves_verified_commit_when_receipt_read_fails(self):
        preview = self.preview()
        self.assertEqual(self.confirm(preview)["status"], "completed")
        original = self.database.read_bytes()
        original_json = bootstrap._json
        reads = []

        def undecodable_second_receipt(path):
            if Path(path).name == bootstrap.RECEIPT:
                reads.append(path)
                if len(reads) == 2:
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "synthetic invalid byte")
            return original_json(path)

        with patch.object(bootstrap, "_json", side_effect=undecodable_second_receipt):
            result = bootstrap.confirm_setup(self.path, preview, NOW + timedelta(hours=1))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(self.database.read_bytes(), original)

    def test_oversized_unexpected_and_symlink_paths_block(self):
        self.path.mkdir()
        for index in range(17):
            (self.path / str(index)).touch()
        self.assertEqual(workspace_status(self.path, NOW)["state"], "blocked")
        linked = self.root / "linked"
        try:
            linked.symlink_to(self.path, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit unprivileged symlinks")
        self.assertEqual(workspace_status(linked, NOW)["state"], "blocked")

    def test_wizard_profile_conflict_is_preserved_not_new_setup(self):
        self.confirm()
        exported = self.path / "profile.json"
        exported.write_text("{}", encoding="utf-8")
        result = workspace_status(self.path, NOW)
        self.assertEqual(result["state"], "recoverable")
        self.assertTrue(result["issues"])
        result = self.confirm(bootstrap.recovery_preview(self.path, NOW))
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["committed"])
        self.assertEqual(exported.read_text(encoding="utf-8"), "{}")


if __name__ == "__main__":
    unittest.main()
