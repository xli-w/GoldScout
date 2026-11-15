---
name: validate-change
description: "Validate a change using focused and risk-appropriate offline regression checks."
disable-model-invocation: true
argument-hint: "Change or files to validate"
---
Use the `trading-change-validation` skill for the requested change. Run focused tests first and full offline tests for shared safety contracts. Do not change production behavior as part of validation. Report interpreter, commands/results, missing coverage, and whether independent review occurred. No broker calls.