# Decision: GBP opening-evidence journal

Status: implemented; older formats retired without migration

Dates: original 2026-11-15; current boundary 2026-11-17

Portfolio accounting needs funded multi-account cash/quantity/valuation and
causal evidence, not relabelled short-horizon trading positions. Keep exact
GBP accounting, append-only original evidence, commit-before-publication,
revision/idempotency and restart replay.

Only opening-evidence wire `schema_version=2` remains supported. Preserve the
wire identifier, explicitly reject older journals and provide no migration,
compatibility aliases or implicit adoption of existing holdings. This retirement
does not read or alter operational journals.

Opening balances do not invent prior transactions or cost basis. Role
attribution, profile/scope evolution, observations, limited corrections and
local awareness are evidence contracts, not allocation approval. Proposals
remain blocked and no proposal export/execution runtime exists.

Dashboard reads are nonmutating; setup and later recording have separate
preview/confirmation/opt-in gates. Keep distinct journal, intelligence, monitoring
cache and review workflow authorities. See [investment](../investment.md),
[safety](../safety-invariants.md) and [portfolio direction](0004-core-dynamic-portfolio-manager.md).

Temporary synthetic fixtures, full offline regression and independent review
are required for shared accounting/data changes. These establish no product
eligibility, licensed historical returns, custody or broker semantics.
