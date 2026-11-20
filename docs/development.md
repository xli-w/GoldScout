# Development workflow

Read [repository guidance](../.github/copilot-instructions.md),
[safety invariants](safety-invariants.md) and the [README](../README.md).
Routine source edits, local tests/builds and synthetic previews are expected
development work. Implement requested local features without treating every
change as an external account action. "No execution runtime" describes the
product: it does not prohibit running development tools.

**Developer** implements. **Safety Reviewer** independently reviews changes to
accounting, persistence, causal data or external authority with read/search
tools only; ordinary copy/layout changes do not require that review.

## Current owners

- `investment/`: opening-evidence GBP accounting, append-only replay, CLI and
  local monitoring. Use
  [investment-system-development](../.github/skills/investment-system-development/SKILL.md).
  Wire `schema_version=2` is retained; older journals are rejected, never migrated.
- `dashboard/`, `frontend/`: local portfolio views and separately gated manual
  recording/setup. Tests must override workspace selection to temporary paths.
  Never discover/read the real ignored `local-investment/` workspace.
- `market_intelligence/`: separate opt-in advisory service. Apply its
  [scoped checklist](../.github/instructions/market-intelligence.instructions.md).
  Account evidence never enters hosted AI; collection is not trading authority.
- `research_data/`: offline local snapshots, provenance, scoped readiness and
  pure fill accounting. Calendar readiness uses `exchange-calendars`; the
  importer and fill helper use the standard library.
- Synthetic investment simulation is engineering only. For simulation or
  selection use [backtest-integrity-review](../.github/skills/backtest-integrity-review/SKILL.md),
  the [mandate](investment-research-context.md) and [protocol](backtesting-protocol.md).
  Stop at unmet feasibility gates. There is no execution runtime.

## Inspect, reproduce, change, verify

1. Locate the state transition and nearest behavior-level test. State a
   falsifiable hypothesis or focused acceptance check.
2. Verify the selected interpreter. Python 3.11.9 is the validated baseline;
   preserve documented Python 3.10 support. Do not hardcode a local interpreter.
3. Use controlled UTC clocks, exact decimals and temporary synthetic storage.
   Never access operational databases, receipts, snapshots, logs or credentials.
4. Make the smallest justified change, run the focused offline slice immediately,
   then adjacent checks. Shared accounting/data/configuration/tooling contracts
   require full offline regression. Changes to accounting, persistence, causal
   data or external authority also require independent read-only Safety Reviewer.
5. Update related docs, validate customization and inspect the diff. Report exact
   checks/results, assumptions, review status and absent broker validation.

```powershell
python scripts/offline_tests.py --pattern test_campaign*.py
python scripts/offline_tests.py --pattern test_investment_opening.py
python scripts/offline_tests.py --pattern test_ai_tooling.py
python scripts/offline_tests.py
python scripts/validate_ai_setup.py
powershell -NoProfile -File scripts/test_agent_guard.ps1
git diff --check
```

The [offline runner](../scripts/offline_tests.py) disables dotenv/proxy secret
loading, scrubs known credential settings including `GOLDSCOUT_ETORO_*` and
`TRADER_AI_*`, and blocks standard Python
socket connection/send/listen and DNS entry points, including UDP. A zero-test
pattern fails. Tests must still use temporary direct paths; the runner is not
an OS sandbox. Do not bypass it with subprocesses or native network clients.

The full regression manifest is [requirements-tested.txt](../requirements-tested.txt);
installations need network, tests do not. Scientific dependencies remain only
for explicit calendar readiness and precision regression fixtures, not a trading
runtime. [CI](../.github/workflows/offline-validation.yml) installs the snapshot,
validates customization, tests the PowerShell guard and runs offline regression
on Windows/Python 3.11.9 with no broker secrets or persisted checkout credentials.
Its socket guard is not OS egress isolation. Untrusted code requires a
credential-free sandbox with outbound egress disabled.

## Focused checks

| Contract | Starting test pattern |
| --- | --- |
| Local research snapshots, readiness, fill conservation | `test_campaign*.py` |
| Journal opening/accounting/replay | `test_investment_opening.py`, `test_investment.py` |
| Backup, setup and recovery | `test_investment_lifecycle.py`, `test_investment_onboarding.py` |
| Observation previews and roles | `test_investment_reconciliation.py`, `test_investment_roles.py` |
| Monitoring/cache/review workflow | `test_investment_monitor*.py` |
| Awareness and advisory intelligence | `test_investment_awareness.py`, `test_intelligence*.py`, `test_market_intelligence.py` |
| Local API/setup/recording | `test_investment_dashboard.py`, `test_investment_bootstrap.py`, `test_investment_setup.py`, `test_dashboard_api.py` |
| Customization and safety guards | `test_ai_tooling.py`, `scripts/test_agent_guard.ps1` |

These are starting points, not exhaustive coverage; use existing test owners.
Frontend work also requires applicable browser checks and a current asset build.
Offline success certifies neither external-provider semantics nor performance.

## Permissions and hooks

The [hook](../.github/hooks/safety.json) invokes
[the policy script](../scripts/agent_guard.ps1). Recognized, single-command local
checks, frontend builds and synthetic demos can proceed without repository-level
approval when bound to this checkout. Other execution, external interaction and
policy edits require approval. Secret/operational paths including
`local-investment/` are denied; malformed payloads fail closed.

The bounded command forms include the offline runner (with an optional test
pattern), customization validator, PowerShell guard tests, frontend build,
and `python -m investment demo` or `python -m dashboard --demo`. Synthetic
dashboard previews may add `--api-only`, `--intelligence-demo` and a port.
Shell composition, arbitrary Python snippets and unrecognized options are not
covered by this permission. Browser tests still need approval and an explicitly
verified synthetic server: their configured/default URL could otherwise point
at operational data. Hook approvals do not override editor/host policy.

Approve only the specific safe command. No broker/authenticated calls,
operational maintenance, secret access or guard-policy changes without separate
task-specific approval. Paper environments are not exempt. Hooks cannot parse
every custom workspace, shell semantic, alias, symlink or integration envelope;
instructions do not replace filesystem permissions, credential separation or
OS egress control.
Never disable a guard or relax assertions to make a test pass.

Keep read-only reviewer permissions, workflow-skill discovery and CI isolation.
Test approved hook/policy changes with synthetic requests, including nested
calls, aliases, moves and retained denial/approval cases.
Run customization validation after YAML/link/tool
metadata changes. Static validation cannot prove editor discovery or hook
activation; inspect VS Code customization diagnostics and smoke-check after
reload. Record significant architecture decisions under [decisions](decisions/0000-template.md).
