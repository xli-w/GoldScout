# GoldScout development

Routine source edits, local tests/builds and synthetic previews are normal
development work. Implement requested local features; the absence of a trading
runtime is a product boundary, not a ban on development tools.

- Use the [development workflow](../docs/development.md) for checks and review,
  [README](../README.md) for product scope, and
  [invariants](../docs/safety-invariants.md) for accounting/privacy contracts.
- For `investment/**`, use `investment-system-development`. For research or
  selection use `backtest-integrity-review` and its mandate/protocol; preserve
  trial/holdout history and stop at unmet feasibility gates.
- Apply [intelligence guidance](instructions/market-intelligence.instructions.md)
  only to that separate advisory subsystem. Never send private account evidence
  to hosted AI.
- Develop with temporary synthetic data, not `.env`, credentials or the ignored
  `local-investment/` workspace. Broker/authenticated calls, operational access,
  account changes and maintenance require task-specific approval, including paper.
- Policy/instruction, hook, CI, offline-isolation and credential-policy changes
  require explicit scope/approval. Do not weaken checks to hide a failure.
- Preserve unrelated work. Do not commit or create branches unless requested.
