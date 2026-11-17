---
description: "Use when changing journal schemas, transactional accounting, replay, backups or recovery."
applyTo: "investment/store.py,investment/core.py,investment/lifecycle.py,investment/bootstrap.py,market_intelligence/store.py,tests/test_investment*.py,tests/test_market_intelligence.py"
---
# Evidence accounting and recovery

Read [invariants](../../docs/safety-invariants.md), use `ledger-migration-review`
and, for investment, `investment-system-development`.

- For the investment journal only, opening-evidence wire `schema_version=2` is
  supported. Refuse older/foreign/altered schemas; no migration, compatibility
  mode or reader repair. Intelligence storage has its own schema lifecycle and
  explicit upgrade path; some older schemas remain readable without upgrade.
  Follow its scoped instructions rather than applying the investment journal's
  version rule.
- Validate schema objects before integrity checks/data access. Readers never
  initialize or create sidecars for absent/unsupported stores.
- Commit evidence/accounting atomically before memory publication. Preserve
  original payloads, binding, revision/idempotency and downstream replay.
  Conflicts fail; never invent fees, cost basis or historical facts.
- Effective and available clocks differ. Preserve exact cash/quantity/role and
  linked external-flow unitization. Corrections invalidate unsupported history.
- Backup uses pinned validated SQLite copy/replay to a fresh no-overwrite path;
  active-WAL file copying and internal hashes alone cannot establish recovery,
  authenticity or latestness. Maintenance requires explicit operator approval.
- Tests use temporary stores and cover rollback/restart/conflicting IDs,
  unsupported-schema rejection, post-commit failures and no-reader mutation.
