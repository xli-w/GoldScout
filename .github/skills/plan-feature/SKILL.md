---
name: plan-feature
description: "Plan a trading bot feature without editing or executing anything."
disable-model-invocation: true
argument-hint: "Feature and acceptance criteria"
---
Create a concise implementation plan for the requested feature using current architecture and [safety invariants](../../../docs/safety-invariants.md). Identify the owning modules, acceptance criteria, smallest first check, regression coverage, operational risks, and decisions requiring approval. Do not edit files, execute commands, or treat this request as authorization to implement.

For strategy/portfolio research, read [investor context](../../../docs/investment-research-context.md) and the [protocol](../../../docs/backtesting-protocol.md) first. Separate feasibility work from performance runs and identify unsupported GBP portfolio and data-import capabilities. For intelligence features, use the [operator guide](../../../docs/intelligence.md) and [foundation decision](../../../docs/decisions/0002-market-intelligence.md); implemented opt-in feeds, analysis controls and a shadow observer do not authorize provider calls, operational use, new integrations or trading influence without the relevant approval.