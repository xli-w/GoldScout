---
name: trading-change-validation
description: "Use when implementing, fixing, or validating trading bot changes; reproduce behavior, select scoped regressions, and report offline safety evidence."
---
# Trading Change Validation

1. Read [development guidance](../../../docs/development.md) and [invariants](../../../docs/safety-invariants.md). Identify the owning function and affected contracts; do not map the whole repository.
2. Verify the selected interpreter. Use Pylance facts for types/call relationships when available; otherwise state limitations and use local source/tests.
3. State a falsifiable hypothesis and run or add the cheapest offline reproduction. Do not read credentials or operational state.
4. Make the smallest requested change. Immediately run `python scripts/offline_tests.py --pattern test_<area>.py`; repair the same slice before broadening.
5. Select adjacent tests from the [test map](../../../docs/development.md). Run the full isolated suite for execution, ledger, shared data, config, and tooling changes. Check the diff for unrelated edits.
6. Validate documentation/customization changes with `python scripts/validate_ai_setup.py`. Safety hook changes also require `powershell -NoProfile -File scripts/test_agent_guard.ps1` on Windows (or `pwsh` where installed).
7. Ask the read-only Safety Reviewer to independently inspect safety-critical changes when available. Do not treat its hypotheses as verified runtime failures.

For research/spec changes, use `backtest-integrity-review` and the [campaign protocol](../../../docs/backtesting-protocol.md) when applicable; a CLI smoke test cannot verify GBP portfolio suitability. For intelligence work, read the [scoped guidance](../../instructions/market-intelligence.instructions.md): validate opt-in feed/provider behavior, analysis controls, storage and observer order-call parity with synthetic fixtures; distinguish them from proposed extensions and unverified live behavior. Use the test map rather than assuming older backtest tests cover experiment or intelligence contracts.

Documentation-only alignment needs customization validation and diff/link checks, not a performance run or full trading regression. Run tooling regressions if discovery metadata changes; guard changes additionally need policy tests, full offline regression and independent review. Static success does not prove VS Code discovery or handoff activation.

Output: changed behavior and files, hypothesis/reproduction, exact interpreter and executed checks/results, independent review status, and remaining risks. Explicitly state that offline validation made no broker calls; do not infer paper/live readiness.