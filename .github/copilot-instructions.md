# GoldScout Development

- Start with [development workflow](../docs/development.md) and
  [safety invariants](../docs/safety-invariants.md). The [README](../README.md)
  is the current product entry point; review reports are historical evidence.
- The [investment system](../docs/investment.md) is a separate, GBP-only
  operator-evidence journal. Proposals are blocked: v1 can export only a blocked
  envelope; v2 has no proposal export. Keep proposed sales bounded and do not
  fund buys from proposed sale proceeds. For `investment/**`, use
  `investment-system-development`; for simulation or selection also use
  `backtest-integrity-review`.
- Follow the [investor mandate](../docs/investment-research-context.md) and
  [research protocol](../docs/backtesting-protocol.md) for suitability or
  strategy-selection work. Stop at unmet feasibility gates. Illustrative
  allocations are not defaults or approval.
- [Market intelligence](../docs/intelligence.md) is separate, opt-in and
  advisory. It must not influence trading or investment policy; private account
  evidence must never be sent to hosted AI. Follow its scoped instructions.
- Default to offline development. Do not start `bot.main`, contact an
  authenticated broker, submit/cancel orders, change account settings, or run
  operational maintenance without task-specific approval. Paper accounts are
  not exempt.
- Never read, print, transmit, or commit `.env`, credentials, tokens, or broker
  secrets. Do not use operational databases, snapshots, reports, logs, or locks
  for tests.
- Use the selected, verified Python environment: Python 3.11.9 is the validated
  baseline; preserve documented Python 3.10 support. Never hardcode a
  developer's interpreter path in shared files.
- Reproduce a specific behavior first, make the smallest grounded change, and
  immediately run its focused check. Use `scripts/offline_tests.py`; broker
  interactions must be mocked and storage temporary. Do not use subprocesses or
  native network clients to bypass the offline runner.
- Preserve fail-closed behavior, durable order intent, quantity conservation,
  transaction ordering, and causal data. Do not guess repairs or relax risk
  limits. Run the full offline suite and request independent review for
  execution, accounting, shared-data, or configuration changes.
- Keep directly related documentation and regression coverage current. Report
  exact checks, unresolved assumptions, review status, and whether broker
  validation occurred; offline success does not establish live semantics or
  profitability.
- Safety hooks, offline guards, CI, credentials policy, and these instructions
  require explicit task-specific approval to change. Never weaken them to make
  a check pass. Do not commit or create branches unless requested.
