# Safety invariants

These are development contracts, not certification of custody, suitability,
profitability or live readiness. [README](../README.md) defines current scope.

## Authority and privacy

- There is no order-submission runtime, trading scheduler, execution broker
  adapter or trading ledger. A separately gated eToro read collector exists;
  it publishes partial, live-unvalidated observations only. Do not reconstruct
  execution as an accounting convenience. Proposals remain blocked; no
  report/export, role assignment, monitoring action or AI output grants authority.
- No secrets, authenticated/paper broker calls, account changes, operational
  maintenance or operational-file access without task-specific approval.
  Never read/print/transmit `.env`, credentials or private account evidence.
  Development uses temporary synthetic stores, not `local-investment/`.
- The local dashboard has no user login. Keep loopback, host/origin and CSRF
  safeguards; they are not authentication. Never expose or tunnel it.
- Future standing authority must be explicit, versioned, revocable and scoped
  to accounts/products/policy, funding/cost/risk, freshness, expiry and failure
  rules. It is not implemented. Non-idempotent uncertain submissions must never
  be retried/adopted from approximate history matching.

## Journal, accounting and recovery

- Only the opening-evidence journal, wire `schema_version=2`, is supported.
  Older/foreign/invalid stores are explicitly rejected without migration.
  Journals containing retired policy-review acknowledgement events are refused
  even when marked version 2; no event migration is provided.
  Readers never initialize, migrate, repair or mutate missing/existing stores.
  Validate exact tables, indexes, triggers and schema identity before integrity
  checks or data queries that could execute altered-schema expressions.
- The investment journal is its own accounting authority; intelligence,
  monitoring caches and review workflows are distinct stores. Preserve binding,
  append-only original evidence, commit boundaries, revision/idempotency and
  hash-linked replay. Identical duplicate imports are idempotent; conflicts fail.
- Validate exact decimal cash/quantity/role conservation and chronology before
  commit, then publish in-memory state only after commit. Failure rolls back
  the entire append, not partial effects. No invented fees/prices, FX, settlement,
  custody, entitlement, accrual or historical cost basis.
- Opening evidence records all included cash and long holdings, not past trades
  or lifetime gains. Complete contemporaneous common valuation alone starts
  the performance baseline. There is no automatic new baseline after correction.
- Profile amendments are append-only. Identity, binding/currency and product
  classification/quantity rules are immutable; retirement requires zero exposure.
  Funded scope additions need complete same-clock balances/held prices and a
  valid existing baseline. They are unitization-neutral, not external deposits.
- Core/dynamic/unassigned attribution is independent of account and asset class.
  Roles conserve aggregate balances, isolate debits and survive account transfers.
  Missing attribution stays unassigned; role-to-role movements are disabled.
  Attribution does not verify custody or implement risk/loss/transfer budgets.
- No automatic core-funded loss refill, experiment promotion, allocation/risk
  expansion or counting core gains as alpha. Illustrative allocations supply
  no approved targets or guaranteed loss bounds.
- Every event preserves effective time and `available_at`. Late-published marks
  cannot establish earlier contemporaneous completeness. Missing held prices
  make valuation incomplete; never present a subtotal or zero-filled return as
  complete. Valuation history groups same-clock relevant events and preserves
  late/incomplete clocks as explicit gaps, not interpolation.
- Preserve journal order but reject ambiguous performance sequencing: required
  marks precede same-clock external flows, baseline precedes same-clock income.
  Missing complete flow-time valuation invalidates linked TWR/drawdown; later
  marks cannot reconstruct it. Unitization remains linked through full
  redemption and refunding without fabricated drawdowns or reset gains.
- Included-account transfers are internal, not contributions/returns. Scheduled
  future funds are not settled cash; external bank deposits are not broker
  buying power. Proposed sale proceeds never fund proposed purchases.
- Financial corrections append evidence and replay downstream events, rejecting
  invalid cash/quantity/role/chronology. Preserve original payload/availability.
  Historical correction invalidates performance without automatically starting
  another baseline. Opening, profile/scope, role, observation and review events
  are not correctable.
- Cash observations never change money or refresh valuation. Historical
  correction replay recalculates past differences and can hide a previously
  reported discrepancy; reconciliation/monitoring surface an attention warning
  when corrections coexist with cash observations. Retain prior reports and
  review correction evidence.
  Savings terms are dated display facts, not accrual, eligibility or cash value.
  Fee/income/capital-flow histories are explicit journal facts only, not proof
  of full expenses, tax, entitlement or external movements.
- Policy review acknowledgements are not implemented. Monitoring review actions
  do not approve trades or resolve discrepancies. Exact cash-only confirmations
  can refresh value only when every included account
  matches, with all prior evidence available; unheld marks cannot refresh it.
  Invalid confirmations fail replay, never silent repair.
- Setup requires same-origin/CSRF, a bound preview and explicit confirmation.
  Normal local launches permit manual journal recording, including after
  completed setup/recovery; `--investment-read-only` blocks setup and recording.
  Partial/uncertain setup never enables recording, and reads never grant it.
  Journal-read-only mode does not change separate monitoring/provider/AI gates.
  Startup/status/preview creates no files. Existing artifacts are never
  overwritten. Setup stages/replay-validates before no-overwrite publication;
  guided first-run setup records current starting figures at preview submission,
  using that one clock for both opening effective time and `available_at`.
  Interrupted-setup recovery preserves the original clocks for exact verification.
  Profile copy, journal and receipts are not one atomic filesystem transaction.
  A post-publication failure must preserve/report the committed journal.
  Recovery requires exact setup provenance, not an arbitrary empty database.
- [Backup/restore](investment-journal.md) uses pinned validated SQLite backup,
  chain/commit/replay validation and a fresh no-overwrite destination under
  operator-controlled permissions. Never install a restore automatically,
  copy only an active WAL file, delete sidecars or reset evidence to bypass
  failure. Internal hashes do not prove authenticity or latestness; keep
  independently recorded receipts and source evidence. Directory durability,
  Windows ACLs, encryption and retention require operator procedures.
- Respect event/evidence/database bounds; no automatic pruning/rollover,
  guessed new opening or limit increases as a workaround. Read failures after
  a committed action must not suggest that the action rolled back.

## Monitoring, reconciliation and awareness

- Monitoring is local and read-only; provider collection requires explicit
  activation and account/retention approval, never page refresh. The fixed eToro
  GET adapter may use a trade-capable key only after explicit operator acceptance
  that the credential can place, modify or cancel orders if exposed or misused.
  The optional eToro wizard read requires explicit account/retention consent.
  Explicit manual refresh may also request a bounded closed-trade history read;
  that data remains in dashboard memory and never becomes journal accounting.
  Startup refresh additionally requires verified Read-only scope, an expiring
  1-30 day permission and revocation; it attempts one current-portfolio read set
  per server startup and does not retry failures or collect trade history.
  Status/page loads never collect, and provider observations remain separate
  from journal accounting.
  This acceptance does not verify read-only scope; the adapter has no order
  methods. Failed collection cannot refresh source clocks or repair journal
  differences. Native-currency evidence cannot manufacture GBP valuation.
- Dynamic-share monitoring uses completely valued, fresh, role-attributed
  non-LISA investment holdings, excluding savings, LISA and all cash.
  Unassigned/missing classification or marks makes it unavailable. The
  illustrative range is no target/cap; concentration is exact instrument share,
  not look-through overlap.
- Staged observations read a pinned journal revision without changing source
  files or accounting. Use only explicit evidenced account/product mappings
  available at the observation clock. Missing/unknown is not zero. Same-clock,
  later-journal or late-available evidence makes comparison unavailable, not a
  guessed historical balance. A declared match is not custody/buying power.
- The separate purgeable observation cache has distinct instance identity and
  monotonic revisions across purge. The separate optional review inbox binds
  portfolio/profile/cache instance and stores summaries/hashes, not raw payloads.
  Actions are append-only, revision-bound and separately opted in; acknowledge,
  defer, review, inactivity or supersession is not resolution or accounting repair.
  Reads never initialize/sync. Guided observations need preview/confirmation
  and write only the cache. Journal/cache/review stores are not one transaction;
  source revisions are rechecked but external updates may overlap the check.
- Awareness locally matches explicitly mapped positive exposures to existing
  public calendar/brief evidence. Reads start no collection/AI, change no journal
  and send no private evidence to AI. Missing/future/stale/unmapped evidence is
  explicit. Editable thesis/calendar notes are not immutable source truth,
  automatic coverage, policy or review approval. Stable technical wire shapes
  remain distinct from journal format. Provider discovery is not permission/sync.

## Research and synthetic mechanics

- Follow [mandate](investment-research-context.md) and [protocol](backtesting-protocol.md).
  Stop at unmet eligibility, licensed persistent data/vintage, cash-terms,
  actions/FX, costs/tax and frozen-policy/specification gates. No histories,
  comparative returns or acquisition are authorized by engineering changes.
- Preserve all prior/manual/failed/descriptive trials and exposed/reserved/
  consumed samples. Keep the 60-candidate total ceiling; no registry/dataset
  switch or code retirement resets history. Lock before one genuinely unexposed,
  report-only holdout; no passing candidate means no holdout opening.
- Use only completed information known at event time; validate aware UTC clocks,
  session/early-close boundaries, source availability, price basis and action
  ordering. Reject missing/conflicting/off-grid data instead of inventing
  continuity. Document gaps, costs, liquidity and fill/OHLC ambiguity.
- `research_data` preserves bounded local manifests/raw bytes/provenance and
  validates checksums on reopen. Its scoped daily/FX readiness checks declared
  availability/age at supplied cutoffs, not truth, vintages, complete transaction
  clocks, conversion, executable rates, entitlement or performance authorization.
  Normalized views require complete selected coverage, never partial-as-complete.
- `research_data.account_fills` is pure synthetic ordered long-only GBP
  cash/quantity/fee conservation. It has no marks, accrual/actions, product
  increments, risk, performance, persistence or execution authority.
- The synthetic investment simulator is disconnected from datasets/store/CLI.
  It models next-open buy-only fills, positive contributions, cash-funded
  withdrawals without liquidation, proportional fees and increment-preserving
  splits available by modeled opens. Insufficient cash fails closed.
  Distributions use pre-ex-open holdings, create unspendable receivables included
  in portfolio/allocation value, then pay before close-time external flows.
  Reject unavailable/unheld distributions and same-product ex-open splits;
  caller prices must not already include distribution adjustment.
- Synthetic annual rates require first-open evidence, simple ACT/365 at
  modeled boundaries and prior rates overnight; changes apply after boundary
  accrual. First capital enters at first close, earning no earlier interest.
  Preserve unit value through complete redemption/refunding. Explicit UTC clocks
  allow at most microseconds; reject missing clocks and datetime subclasses
  rather than truncate hidden precision.
- Simulator provenance/basis/entitlement cannot be verified. No sales/liquidation,
  actual cash terms, tax, FX, inflation, fixed fees, spreads/slippage or verified
  calendars/execution are supplied. Keep synthetic-only labels; no product
  returns, eligibility or profitability claims follow.

## Advisory intelligence and development guards

- Intelligence is separately stored, opt-in and advisory only, with
  master/AI default off. Historical persisted `shadow` labels have no runtime
  effect or hypothetical order evaluation. Unknown/stale coverage is
  uncertainty, not protection, permission, halt or liquidation authority.
- Preserve source revisions, receipt and validated committed AI-completion
  availability, model/prompt/input provenance and coverage. Publication alone
  is not availability. External text/model output is untrusted data, never
  executable instructions. Private account evidence must not enter hosted AI.
- Preserve dedicated store identity, append-only evidence, bounded reads,
  idempotency, exact revisions/expiry, atomic application and no reader repair.
  Requested settings and service-applied receipts are distinct; a toggle/schema
  proves neither application nor active collection.
- Recorded analysis controls require explicit enablement and their existing
  same-origin/CSRF defenses, not user authentication. Settings are bounded analysis only,
  never arbitrary URLs/prompts/secrets/orders/risk limits. Provider capabilities,
  endpoint allowlists, bounded requests and durable spending reservations remain.
- Configured hosted/feed proxy use has no direct fallback; preserve address
  validation/pinning and TLS verification without logging proxy credentials.
  Explicit local AI uses loopback, never as remote-failure fallback.
- Existing campaign news/LLM signal exclusions remain. Future structured features
  need prospective separate approval and failure rules; neither intelligence
  outage nor fluent output may rewrite core policy or create execution authority.
- Offline runner scrubs credential settings/disables dotenv/proxy secret loading
  and blocks Python sockets. It is not an OS sandbox; no subprocess/native
  network bypasses. Keep hook/CI safety guards and read-only review permissions.
  Review reports distinguish hypotheses from executed evidence; offline tests
  prove neither broker/provider behavior nor editor hook activation.
