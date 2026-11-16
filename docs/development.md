# AI-Assisted Development

## Entry Points

Project customizations are versioned under [.github](../.github/copilot-instructions.md). Use **Developer** for implementation and **Safety Reviewer** for read-only review. The Developer has an explicit review handoff; reviewer tools are restricted to read/search and it cannot delegate execution.

User-invoked workflow skills: `/review-change`, `/reproduce-bug`, `/validate-change`, `/plan-feature` (stored as `SKILL.md`, not separate prompt files). Domain skills: `trading-change-validation`, `order-recovery-review`, `ledger-migration-review`, `backtest-integrity-review`, and `investment-system-development`. Scoped instructions load for the relevant files; domain skills load on demand. Installed Pylance skills complement these procedures but are not required for discovery or CI.

In VS Code, check the chat customization UI/diagnostics for the two agents, four workflow skills, five domain skills, and scoped instructions. If new files are not listed, reload the window or start a new chat. Hook support/enablement depends on the VS Code/Copilot version and workspace trust; inspect hook execution diagnostics and verify an ordinary execution request asks permission. Static checks cannot prove editor discovery or hook activation.

## Research and Intelligence Scope

The separate [investment path](investment.md) provides GBP-only recorded
accounting and buy-only, export-only allocation proposals. Its store is not the
legacy trading ledger. Use temporary profiles/events/stores for development;
unsupported FX, entitlement, corrections, historical research and execution must
remain explicit rather than inferred from the legacy engine.

Use [`investment-system-development`](../.github/skills/investment-system-development/SKILL.md)
for changes under `investment/`; use the research skill as well for simulator
or selection work.

Read [investor context](investment-research-context.md) and the [backtesting protocol](backtesting-protocol.md) before strategy selection or portfolio-suitability research. They govern the agreed GBP campaign, not live configuration or every generic USD experiment. Its feasibility gates, candidate budget, risk profiles, net cash-relative hurdle, costs, and single-use holdout rules take precedence over generic CLI defaults. The return aspiration is not a pass condition.

The separate [campaign dataset tools](campaign-dataset.md) validate/snapshot user-supplied daily/weekly product histories, GBP-per-unit FX, cash rates and corporate actions offline; the read-only readiness audit performs scoped XLON daily checks. A separate normalized daily view preserves declared availability and source provenance. The FX-readiness audit and complete FX observation view select published, fresh observations only at caller-supplied clocks; they do not convert prices or verify complete event coverage, executable rates or historical vintages. Neither view nor audit supports performance. The pure `account_fills` helper is a synthetic GBP cash/quantity conservation contract only. `investment.simulation.simulate_synthetic_fixture` is a separate engineering-only harness for synchronized caller-supplied synthetic bars, next-open buys, contribution unitization, cash-funded close withdrawals without liquidation, assumed proportional costs, splits known by modeled opens with increment-preserving quantities, and caller-supplied synthetic cash rates accrued under a simple ACT/365 convention; it does not verify input provenance or actual cash-product terms and is not connected to those datasets, the investment store, a CLI, or product research. Neither tool closes GBP feasibility gates or supports performance claims. `prepare --allow-network` still uses Alpaca and remains outside the campaign's public/user-supplied-data boundary; see the [current runner blockers](backtesting-protocol.md#9-current-runner-readiness-and-blockers).

Use the [intelligence guide](intelligence.md), [decision](decisions/0002-market-intelligence.md) and [scoped checklist](../.github/instructions/market-intelligence.instructions.md) for that separate development stream. Advisory/shadow decisions have no trading authority. The initial release implements opt-in feeds, AI adapters, authenticated analysis-only controls and an asynchronous bot observer. Requested, service-applied and bot-observed settings remain distinct; unverified provider behavior and proposed extensions are not established capabilities. Initialization, service startup and external calls are operational actions, not routine development checks. News/LLM trading signals remain excluded from the GBP campaign.

## Environment and Checks

The validated baseline is Python 3.11.9 and [tested dependencies](../requirements-tested.txt); documented Python 3.10 compatibility remains supported but this CI job does not certify it. Select the project environment and verify the editor interpreter rather than relying on terminal PATH. Use its executable in place of `python` below if necessary.

```powershell
python scripts/offline_tests.py --pattern test_ai_tooling.py
python scripts/offline_tests.py --pattern test_ledger.py
python scripts/offline_tests.py
python scripts/validate_ai_setup.py
powershell -NoProfile -File scripts/test_agent_guard.ps1
git diff --check
```

The [offline runner](../scripts/offline_tests.py) isolates cached broker settings, disables dotenv loading and the proxy-only dotenv reader, clears Alpaca credentials/confirmation and the proxy setting, patches standard Python socket connection/send/listen and DNS entry points (including UDP sends), and redirects configured ledger/state/CSV/log/lock paths to a temporary directory before test discovery. It restores the prior broker-settings cache on exit. A pattern matching zero tests fails. Dependencies must already be installed. Test code must still use temporary direct file paths and avoid subprocesses/native network clients: the runner is not an OS sandbox. Temporary redirection is not permission to edit operational files through other commands.

Install `requirements-tested.txt` for the full regression suite. It includes the
pinned dashboard and intelligence dependencies used by tests, plus HTTPX for the
in-process FastAPI test client; this is also the dependency manifest installed by CI.

The [CI workflow](../.github/workflows/offline-validation.yml) installs the version snapshot, validates customization, tests the PowerShell policy, and runs the isolated suite on Windows/Python 3.11.9. It references no broker secrets and does not persist checkout credentials. Dependency installation needs network access; the regression phase blocks Python sockets, not all OS egress. Use a credential-free OS/container sandbox with outbound egress disabled for untrusted code. CI execution and package availability must be confirmed by an actual hosted run.

## Test Map

| Change | Focused Starting Checks |
| --- | --- |
| Order lifecycle / protective coverage | [portfolio](../tests/test_portfolio.py), [repair cases](../tests/test_review2_repairs.py), [next cases](../tests/test_review2_next.py) |
| Fresh-entry ownership / OTO group linkage | [review 3 ownership](../tests/test_review3_ownership.py) |
| Ledger / restart / persistence | [ledger](../tests/test_ledger.py), [state validation](../tests/test_state_validation.py) |
| Candle/session/action data | [data](../tests/test_review2_data.py), [research safety](../tests/test_research_safety.py) |
| GBP campaign import/readiness/accounting contracts | [campaign dataset](../tests/test_campaign_dataset.py), [readiness](../tests/test_campaign_readiness.py), [fill accounting](../tests/test_campaign_accounting.py) |
| Offline investment accounting, replay and proposals | [investment](../tests/test_investment.py), [read-only investment API](../tests/test_investment_dashboard.py), [desktop/mobile views](../frontend/tests/dashboard.spec.mjs) |
| Backtest / monitor timing | [backtest](../tests/test_backtest.py), [monitor](../tests/test_review2_monitor.py) |
| Experiment runner (spec, dataset, selection, holdout) | [experiments](../tests/test_experiments.py) |
| Intelligence calendar/contracts/wire schema | [intelligence](../tests/test_market_intelligence.py) |
| Intelligence storage/control/restart behavior | [controls](../tests/test_intelligence_controls.py) |
| Intelligence feeds, archive and scheduled polling | [providers](../tests/test_intelligence_providers.py), [news](../tests/test_intelligence_news.py), [archive](../tests/test_intelligence_archive.py), [polling](../tests/test_intelligence_polling.py) |
| Intelligence AI, observer and dashboard controls | [analysis](../tests/test_intelligence_analysis.py), [completion](../tests/test_intelligence_completion.py), [frontend API](../tests/test_intelligence_frontend.py), [browser](../frontend/tests/intelligence.spec.mjs) |
| Research statistics / selection | [remaining findings](../tests/test_remaining_findings.py), [final cases](../tests/test_review2_final.py) |
| AI setup / offline guard | [tooling](../tests/test_ai_tooling.py), [PowerShell policy tests](../scripts/test_agent_guard.ps1) |

These are starting points, not exhaustive coverage. Read the nearest relevant test and owner, form a falsifiable hypothesis, reproduce, edit narrowly, and immediately validate. Execution/accounting/shared data/config/tooling changes require full offline regression and independent review where available. Final reports list commands/results, review status, unresolved assumptions, and broker validation status.

## Permissions and Hooks

The [PreToolUse hook](../.github/hooks/safety.json) invokes [the policy script](../scripts/agent_guard.ps1) on Windows PowerShell; Linux/macOS require `pwsh` installed. It emits documented allow/ask/deny JSON, denies recognized direct secret/operational paths, asks approval for safety-policy edits and execution/external interaction, and fails closed on malformed payloads. It never echoes tool input or secrets.

The SDK can present the free-form patch tool to the hook as `Edit`, even when the chat displays `apply_patch`. Both aliases are inspected as patches, including move destinations, and require recognizable targets. JSON-only tests of `apply_patch` are insufficient to verify this integration; the policy tests cover raw/structured `Edit`, host envelopes, protected paths, and policy-edit approval.

If an active rejection disagrees with synthetic replay, the session's host envelope may differ from the payload received by the policy process. Check the actual tool alias before changing parsers. Do not disable the hook or allow arbitrary non-JSON strings. If the hook blocks edits to itself, an operator may need to bootstrap a specifically approved compatibility repair outside the agent; preserve deny/ask boundaries, add the regression, remove temporary diagnostics, and verify an actual editor patch as well as offline checks.

Approve only the specific safe offline command. Paper/live API calls, order mutations, account changes, maintenance/corrections, and guard changes need separate task-specific approval. Secrets must be entered directly by the operator, never passed through chat. The hook intentionally asks for all execution tools rather than claiming it can safely parse arbitrary shell code.

Hook decisions cover recognized path fields, protected-name glob searches, and nested parallel calls, not arbitrary aliases/file names, symlinks, every integration schema, shell semantics, or a hostile agent. Invalid JSON, non-object inputs, and recognized file tools without targets are denied; the hook is not a full validator for every tool schema. Disabling hooks or approving unrestricted shell commands defeats that layer. Instructions and hooks do not replace filesystem permissions, credential separation, OS egress controls, or human review. No broker MCP tools or autonomous strategy optimization are configured.

## Maintenance

Keep [safety invariants](./safety-invariants.md) and this guide current when behavior changes. Record significant architectural choices under `docs/decisions/` using [the decision template](./decisions/0000-template.md); do not create a decision for every small edit. Repository memory is a convenience, not authoritative documentation.

Run the validator after changing YAML, agent names/tools, links, hooks, or CI. Test policy changes using synthetic inputs. Periodically replay a known historical bug as an offline regression and confirm the relevant skill is selected; do not intentionally break production code or tune instructions to a single example. Discovery and handoff behavior need a VS Code smoke check after reload.