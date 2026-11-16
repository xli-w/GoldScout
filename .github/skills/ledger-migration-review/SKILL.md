---
name: ledger-migration-review
description: "Use when changing SQLite schemas, migrations, transactional accounting, audit corrections, export recovery, or persistence failure handling."
---
# Ledger Migration Review

Read [accounting contracts](../../../docs/safety-invariants.md). Anchor at the transaction/schema owner in `bot/ledger.py` and its callers in portfolio/audit.

The GBP investment journal in `investment/store.py` is a separate store and
authority, not a migration or extension of `bot/ledger.py`. For changes to that
store, read `investment-system-development` as well and test its explicit-init,
append-only/replay, and no-reader-repair contract in temporary databases.

1. Identify the schema transition or transaction boundary, source-of-truth state, event revision, and idempotency keys.
2. Construct an old-schema or recovery fixture in a temporary database. Never open or copy the operational ledger for testing.
3. Verify upgrade/repeat/restart preserves original payloads, account binding, append-only evidence, and replay/snapshot consistency. Future/invalid schemas must be refused.
4. Inject commit/lock/export failures. Assert a failed commit publishes no quantities/fill counters, persistence quarantine prevents unsafe work, and export failure does not invalidate committed exits.
5. Exercise identical and conflicting IDs, evidence-backed correction retries, missing evidence, and prior-day accounting where affected. No guessed fee or historical price reconstruction.
6. Run `python scripts/offline_tests.py --pattern test_ledger.py`, relevant state tests, then full offline tests. Verify backup handle closure and temporary cleanup on Windows.

Reviewer mode uses source/test evidence only. Output: transaction/migration contract, evidence and check results, ranked defects, and residual recovery risks. No operational corrections or quarantine clearance without operator approval.