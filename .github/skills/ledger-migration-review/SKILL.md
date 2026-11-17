---
name: ledger-migration-review
description: "Use for journal schemas, transactional evidence, replay, backups and persistence failure handling."
---
# Journal integrity review

Read [invariants](../../../docs/safety-invariants.md) and, for investment,
[investment-system-development](../investment-system-development/SKILL.md).
For the investment journal, only opening-evidence wire `schema_version=2` is
supported; reject older journals explicitly without migration. Intelligence
storage is a separate authority with its own schema/upgrade lifecycle; do not
apply the investment journal's version rule to it; follow its
[scoped instructions](../../instructions/market-intelligence.instructions.md).
The historical skill name grants no migration capability for investment journals.

1. Identify transaction owner, binding, revision/idempotency and evidence authority.
2. Use temporary synthetic databases, including altered/older/future schemas;
   never read/copy operational stores. Investment readers refuse unsupported
   versions and never repair/migrate. Verify intelligence readers/upgrades against
   supported older schemas and its explicit upgrade contract; readers may continue
   to support an older schema without upgrading it.
3. Validate schema objects before integrity/data queries. Preserve append-only
   payloads, availability, commit boundaries and replay/restart consistency.
4. Inject commit/copy/publication failures. Failed commits publish no state;
   post-commit/report/export failure must not imply rollback or delete evidence.
5. Cover identical/conflicting IDs, supported corrections/downstream replay,
   missing facts and exact cash/quantity/role conservation.
6. Check pinned SQLite backup/replay, no-overwrite destination, handles/sidecars,
   independent receipt/currentness caveats and temporary cleanup on Windows.
7. Run focused `test_investment*.py` checks then full offline regression for
   shared contracts; request independent read-only review.

Reviewer mode reads only. Report confirmed findings separately from unexecuted
hypotheses and gaps. Maintenance needs task-specific approval.
