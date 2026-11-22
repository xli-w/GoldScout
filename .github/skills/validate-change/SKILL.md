---
name: validate-change
description: "Validate a change using focused and risk-appropriate offline regression checks."
disable-model-invocation: true
argument-hint: "Change or files to validate"
---
Use the `trading-change-validation` skill for the requested change. Run the smallest relevant checks; full offline regression applies to shared transaction/accounting/data contracts and offline isolation, not every edit. Do not change production behavior as part of validation. Report interpreter, commands/results, missing coverage, and whether independent review was applicable and occurred. No broker calls.