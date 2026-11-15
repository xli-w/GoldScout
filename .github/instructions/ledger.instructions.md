---
description: "Use when changing SQLite accounting, recovery validation, process locks, exports, or audit maintenance."
applyTo: "bot/ledger.py,bot/audit.py,bot/process_lock.py,bot/trade_logger.py,bot/portfolio.py,tests/test_ledger.py,tests/test_state_validation.py"
---
# Accounting and Recovery

Read [accounting invariants](../../docs/safety-invariants.md) and use `ledger-migration-review` when changing transactions or schemas.

- SQLite is authoritative; CSVs are derived exports and JSON is a compatibility snapshot. Do not reset/delete a ledger or clear quarantine to bypass an error.
- Commit exit accounting and resulting recovery state together before publishing quantities or fill counters in memory. Failure must leave published counters unchanged and stop unsafe scheduling.
- Never hold database transactions across broker requests. Broker/database atomicity is impossible; durable intent and reconciliation are required.
- Preserve append-only evidence, original payloads, account/environment binding, journal revision checks, and idempotency. Conflicting duplicate IDs are errors.
- Corrections require stored evidence and explicit operator review; an activity ID alone does not prove price, quantity, currency, or fee allocation. Never invent historical facts.
- Backups use SQLite's backup API and integrity/recovery checks; copying a live WAL database file is insufficient. Close connections explicitly where required, including Windows backup handles.
- Tests use temporary databases and cover rollback, restart, repeated migration/correction, conflicting evidence, and export failure separately from committed accounting.