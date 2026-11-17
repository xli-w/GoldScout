---
name: trading-change-validation
description: "Validate portfolio evidence changes offline; historical skill name grants no trading authority."
---
# Change validation

1. Read [development](../../../docs/development.md) and
   [invariants](../../../docs/safety-invariants.md). Locate owner and nearest test.
2. Verify interpreter (validated baseline Python 3.11.9; preserve Python 3.10).
   State one falsifiable check, use fixed clocks and temporary synthetic files.
3. Reproduce, make the smallest requested change, immediately run
   `python scripts/offline_tests.py --pattern test_<area>.py`.
4. Run adjacent tests and full offline suite for accounting/shared-data/tooling.
   No operational stores, credentials, subprocess/native-network bypasses.
5. Validate metadata/links with `python scripts/validate_ai_setup.py`; safety
   policy changes additionally need explicit approval and PowerShell policy tests.
   Keep hooks/CI failure-gating behavior unchanged unless separately approved.
6. Request independent read-only Safety Reviewer for safety-critical contracts.
   Report exact checks/results, assumptions, review status and no broker validation.

For investment use [investment-system-development](../investment-system-development/SKILL.md).
For research use [backtest-integrity-review](../backtest-integrity-review/SKILL.md);
stop at external feasibility gates. For intelligence apply its
[scoped checklist](../../instructions/market-intelligence.instructions.md).
There is no execution runtime. Synthetic results establish engineering only,
not suitability, licensed data, provider behavior or profitability.
