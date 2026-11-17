---
name: plan-feature
description: "Plan a portfolio evidence feature without editing or executing anything."
disable-model-invocation: true
argument-hint: "Feature and acceptance criteria"
---
Create a concise implementation plan for the requested feature using current architecture and [safety invariants](../../../docs/safety-invariants.md). Identify the owning modules, acceptance criteria, smallest first check, regression coverage, operational risks, and decisions requiring approval. Do not edit files, execute commands, or treat this request as authorization to implement.

For portfolio research, read [investor context](../../../docs/investment-research-context.md) and the [protocol](../../../docs/backtesting-protocol.md) first. Separate feasibility work from performance runs and identify unsupported capabilities. For intelligence use the [guide](../../../docs/intelligence.md) and [decision](../../../docs/decisions/0002-market-intelligence.md); opt-in feeds and analysis controls authorize no provider calls, operational use or trading authority without the relevant approval.

For changes under `investment/`, also use
[`investment-system-development`](../investment-system-development/SKILL.md).
Distinguish implemented GBP-only recorded accounting, role attribution and
local awareness from blocked public proposals, the planned continuous simulator
and any separately approved future execution. No execution runtime exists. Synthetic simulation tests are engineering evidence only;
do not describe them as performance research.