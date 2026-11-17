# Market Intelligence

The local intelligence service collects BLS/BEA calendars and Federal Reserve
monetary-policy RSS, archives source evidence and optionally generates advisory
AI briefs. It never places orders, changes stops or suppresses strategy signals.
There is no order evaluator, broker observer or execution runtime. The persisted
`shadow` settings value is a historical evidence label with no runtime effect;
it does not evaluate hypothetical orders. Calendar completeness is unverified.

## Portfolio-manager awareness

The optional [local portfolio projection](portfolio-automation.md) now connects
existing stored evidence to explicitly mapped recorded exposures, dated briefs,
operator-configured review reminders, editable local thesis history and manual
calendar watch notes. These notes are not an immutable audit trail, automatic
assessment or feed. It performs local reads only, starts
no service and sends no portfolio data to AI. Read-only sync remains provider
discovery, not an enabled account connection.

### Further direction (planned, not enabled)

The [2026-11-16 mandate](investment-research-context.md) makes intelligence a
central supporting capability for substantial long-term investment capital:
a stable core and capped dynamic experiments, not a household cash planner.
Reuse the existing service rather than building a parallel ungoverned AI trader.
Current advisory evidence contracts and opt-in launch/provider gates remain.

Prioritise automatic, source-linked portfolio briefs, thesis/watch records,
economic-event awareness, uncertainty and research-hypothesis generation. The
local exposure projection links explicitly mapped public context to recorded instruments;
account balances, private identifiers and broker/account evidence must not become
hosted AI inputs. Public research sources still need verified rights and relevance.
Current BLS/BEA/Fed coverage is US-focused; BoE/UK/global calendars and issuer
coverage are proposed extensions requiring approved sources and coverage tests,
not capabilities inferred from an empty calendar.

Separate three uses:

1. **Explain and monitor:** dated evidence, known changes, exposure context and
   watch/review/wait briefs. Measure factual reference accuracy, freshness,
   coverage, alert usefulness and AI spend; fluent prose is not return evidence.
2. **Propose experiments:** AI can help formulate testable hypotheses and identify
   missing evidence. Humans freeze products, signals, model/prompt versions,
   costs, trial history and admission criteria. Proposed ideas are not trades.
3. **Future admitted dynamic features:** deterministic calendar constraints or
   structured AI features require separate explicit approval and prospectively
   logged research with receipt/validated-completion times. They do not modify
   the stable core. Existing news/LLM exclusions are not lifted for current runs.

Future standing-policy execution must sit behind independent deterministic
funding, cost, ownership and risk checks. Models cannot submit orders, edit
mandates, expand budgets or automatically refill experimental losses. External
text/model output is untrusted data, never executable instruction. An intelligence
failure cannot silently become a favourable signal, change protective recovery
or force core liquidation. A dependent dynamic strategy needs its own approved
failure/suspension policy before admission.

See the [portfolio roadmap and verified design sources](investment.md) and
[research admission protocol](backtesting-protocol.md). The local projection starts no service, calls no provider and grants no trading
authority.

## Setup

Use the selected Python 3.11 environment. Install optional dependencies:

```powershell
python -m pip install -r requirements-intelligence.txt
python -m market_intelligence initialize --database intelligence.sqlite3
```

Existing version-1/2/3 intelligence databases require explicit upgrade with the
service stopped. Foreign or investment stores must never be used as this database:

```powershell
python -m market_intelligence upgrade --database intelligence.sqlite3
```

Version 4 adds append-only AI reservations and briefs. Spending reservations commit
before provider dispatch, persist across restart, and are not refunded on failure.
Daily caps cover calls, projected input bytes and output-token allowance, not a
guaranteed dollar bill or provider-token audit. Failed budget checks retain clock
observations; risk limits and account settings are unrelated and cannot be edited.

## Providers

Create an operator-owned JSON profile file with no secrets. Example:

```json
[
  {"name":"local","kind":"local","model":"llama3.2","port":11434},
  {"name":"hosted","kind":"openai","model":"gpt-4.1-mini","key_env":"TRADER_AI_OPENAI_KEY"}
]
```

The local adapter uses the explicitly configured loopback OpenAI-compatible server;
it does not install or start a model. Hosted credentials must be entered by the
operator outside chat into the named environment variable. They are never read by
the browser or included in evidence. Hosted traffic uses the fixed OpenAI HTTPS
endpoint, DNS/address checks, TLS verification and the configured `TRADER_PROXY_URL`
without a direct fallback. Local loopback operation is an explicit profile kind,
not a fallback for a failed remote request. Provider calls are bounded and have no
automatic retries or tools. Actual provider compatibility still requires separate
operator-approved validation, and cannot be established by offline mocks.

## Run And Controls

Starting an operational service or external provider needs task-specific operator
approval. After approval, run from the project directory:

```powershell
python -m market_intelligence run --database intelligence.sqlite3 --allow-network --collect-news --allow-ai --ai-profiles profiles.json
python -m dashboard --intelligence-database intelligence.sqlite3 --analysis-controls --analysis-profiles local
```

Analysis controls require explicit dashboard opt-in, same-origin and CSRF-protected
sessions, but there is no password or user login. These browser defenses are not
authentication and do not protect against other local users. No endpoint accepts an arbitrary
provider URL, prompt, secret, risk limit or order. Do not expose the loopback service
remotely. The dashboard can instead be started without controls for read-only use.
The Intelligence header identifies its own synthetic or recorded source, rather
than displaying cached account environment or runtime status. An unavailable or
unrecognized source remains unknown. Empty calendar, news and brief sections
distinguish collection disabled in applied settings from no records in the
snapshot; neither state establishes complete source coverage or the absence of
events.

The master flag defaults off. Calendar and news preferences are retained when it
is turned off, but turning off the master also disables AI summaries and clears
the saved AI profile. Turning off AI summaries also clears the profile so shutdown
does not depend on matching dashboard and service registrations. Collection and
analysis additionally need explicit service capabilities.

Configure one dashboard profile with `--analysis-profiles` and register it with
the service. Enabling AI retains a saved profile if it is registered, otherwise
it uses the sole dashboard profile. If no profile or multiple profiles without a
registered saved selection are available, AI cannot be enabled. After AI is
turned off, configure exactly one dashboard profile to enable it again. Change
profiles in the service and dashboard configuration, not on the page. While AI
is off, an unregistered saved profile is cleared when another setting changes.
If the service rejects a saved profile while AI is off, use the displayed
"Clear saved AI profile" action and retry. Requests are queued with database
identity, base revision and expiry. Pending is not applied. Applied is not proof
that network capabilities are enabled. No broker-observed revision or execution
bridge exists. Stale or missing evidence remains unknown.

AI interpretations are always unverified, factual claims are source quotations,
and recommendations are only watch/review/wait. Analysis considers recent dated
articles; unknown publication times are not silently treated as new events. Model
output, budget exhaustion or intelligence outages cannot affect broker mutations.

## Synthetic Preview

Build the frontend, then launch an isolated demo with temporary storage, fake source
evidence and a synthetic completion callback. It makes no broker/feed/AI calls:

```powershell
npm --prefix frontend run build
python -m dashboard --demo --intelligence-demo --port 8766
```

Demo controls operate only on the temporary synthetic workspace and require no
real credential. The source mode travels with the data. It is not operational or
paper-account validation. Use `/intelligence` for the working experience.

## Limits

Coverage currently consists of the named official sources, not comprehensive
political/breaking-news coverage. Raw RSS and normalized evidence are archived;
corporate-action/news completeness and a profitable edge are not established.
OS DNS and local outages cannot be guaranteed to honor a hard wall-clock deadline.
Live provider and hosted-platform validation have not occurred in development.
Research exclusions for news/LLM signals remain unchanged; no order gate exists.