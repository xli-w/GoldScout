# GoldScout development

- Start with [development](../docs/development.md),
  [safety invariants](../docs/safety-invariants.md) and [README](../README.md).
- The product is one opening-evidence GBP portfolio journal, wire
  `schema_version=2`. Older journals are explicitly rejected with no migration.
  Proposals are blocked; there is no order-submission runtime or execution
  adapter. A separately gated eToro read collector is partial and live-unvalidated.
  For `investment/**`, use `investment-system-development`.
- For simulation/selection use `backtest-integrity-review`, the
  [mandate](../docs/investment-research-context.md) and
  [protocol](../docs/backtesting-protocol.md). Stop at unmet feasibility gates.
  Preserve prior trials/exposed samples/consumed holdouts. Synthetic tests are
  engineering evidence, not performance or authority.
- [Intelligence](../docs/intelligence.md) is separate, opt-in and advisory;
  follow its scoped instructions. Never send private account evidence to hosted AI.
- No broker/authenticated calls, operational maintenance, secret access or
  account changes without task-specific approval. Paper environments are not
  exempt. Never read/print/transmit `.env`, tokens or private operational data.
  Tests must not discover/read the ignored `local-investment/` workspace.
- Verify the selected Python environment. Python 3.11.9 is the validated
  baseline; preserve documented Python 3.10 support and avoid local paths.
- Reproduce a specific behavior, change narrowly, run focused
  `scripts/offline_tests.py` checks with temporary storage/fixed clocks, then
  full regression for shared/safety-critical contracts. No subprocess/native
  network bypasses: the runner is not an OS sandbox.
- Preserve exact accounting, causal availability, append-only evidence,
  transaction-before-publication, idempotency, role isolation and fail-closed
  replay. Never guess repairs or relax limits.
- Request independent read-only Safety Reviewer for accounting, data or future
  execution contracts. Report exact checks, assumptions, review status and
  absent broker validation; offline success proves neither external semantics
  nor profitability.
- Keep related docs/regressions current. Hook, CI, offline guards, credentials
  policy and these instructions require task-specific approval to change.
  Never weaken guards to pass tests or commit/create branches unless requested.
