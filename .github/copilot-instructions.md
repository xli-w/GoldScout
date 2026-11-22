# GoldScout development

Routine source edits, local tests/builds and synthetic previews are normal
development work. Implement requested local features; the absence of a trading
runtime is a product boundary, not a ban on development tools.

Default to implementing the requested change, not adding approval steps or
protective UX on the operator's behalf. Focus correctness requirements on
atomic transactions, exact accounting, preserved evidence and truthful data.
Apply only the checklists relevant to the changed behavior; do not treat them
as a requirement to redesign unrelated features.

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
- Routine documentation, skill and instruction improvements are development
  work. Changes to hooks, CI, offline isolation, credential policy or external
  permissions require explicit scope/approval. Guidance edits do not authorize
  operational actions or bypass host permissions. Do not weaken checks to hide
  a failure.
- Preserve unrelated work. Do not commit or create branches unless requested.
