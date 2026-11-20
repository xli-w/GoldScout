---
name: trading-change-validation
description: "Validate portfolio evidence changes offline; historical skill name grants no trading authority."
---
# Change validation

Follow the [development validation procedure](../../../docs/development.md#inspect-reproduce-change-verify):
verify the interpreter, run the focused offline slice, then adjacent checks.
Shared accounting/data/tooling changes require full regression; copy/layout
changes do not require accounting review.

Use temporary synthetic evidence and fixed clocks. Apply
[invariants](../../../docs/safety-invariants.md) for the affected contract.
Customization changes need `python scripts/validate_ai_setup.py`; approved hook
changes also need `powershell -NoProfile -File scripts/test_agent_guard.ps1`.
Report exact checks/results and applicable independent review.

For investment use [investment-system-development](../investment-system-development/SKILL.md).
For research use [backtest-integrity-review](../backtest-integrity-review/SKILL.md);
stop at external feasibility gates. For intelligence apply its
[scoped checklist](../../instructions/market-intelligence.instructions.md).
Synthetic results establish engineering behavior, not provider validation or
investment performance.
