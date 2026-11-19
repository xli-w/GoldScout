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

Existing version-1/2/3/4 intelligence databases require an explicit upgrade for
the latest capabilities, with the service stopped. Older supported databases
remain readable and are never upgraded by a read. Foreign or investment stores
must never be used as this database:

```powershell
python -m market_intelligence upgrade --database intelligence.sqlite3
```

Restart any running intelligence dashboard after the upgrade so it reloads the
database schema.

Version 4 adds append-only AI reservations and briefs. Spending reservations commit
before provider dispatch, persist across restart, and are not refunded on failure.
Daily caps cover calls, projected input bytes and output-token allowance, not a
guaranteed dollar bill or provider-token audit. Failed budget checks retain clock
observations; risk limits and account settings are unrelated and cannot be edited.
Version 5 adds append-only service observations. A version-4 service can still
collect and analyze as before, but its service observations are unavailable until
the operator explicitly upgrades the separate intelligence database.
Saved version-2 news remains visible after upgrade even when no later source
observation exists; the source status stays unknown rather than being invented.

## Providers

Create an operator-owned JSON profile file with no secrets. Example:

```json
[
  {"name":"local","kind":"local","model":"replace-with-local-model","port":11434},
  {"name":"openai","kind":"openai","model":"replace-with-openai-model","key_env":"TRADER_AI_OPENAI_KEY"},
  {"name":"anthropic","kind":"anthropic","model":"replace-with-claude-model","key_env":"TRADER_AI_ANTHROPIC_KEY"},
  {"name":"gemini","kind":"gemini","model":"replace-with-gemini-model","key_env":"TRADER_AI_GEMINI_KEY"}
]
```

Replace the example model names with models the operator has verified for the
selected account and response requirements. A file can contain up to ten
named profiles. Hosted profiles reference operator-owned `TRADER_AI_`
setting names, not key values; multiple profiles can reference different keys.
Gemini profiles use a bare model identifier, not a URL or
`models/` path. Only one registered profile is selected for analysis at a time.
Ambiguous JSON fields and incomplete profile entries are rejected.
Before live use, independently confirm model permissions, public-data and
provider retention terms, rate limits and possible charges; the existing
request caps are not a currency spending limit.

The local adapter uses the explicitly configured loopback OpenAI-compatible server;
it does not install or start a model. It must support the requested
OpenAI-compatible JSON completion shape, including an assistant role and normal
stop reason. Hosted credentials must be entered by
the operator outside chat into the named private setting. They are never
read by the browser or included in evidence. OpenAI, Anthropic and Gemini use
their own fixed HTTPS hosts and request/response formats, DNS/address checks,
TLS verification and the configured `TRADER_PROXY_URL` without a direct fallback.
No arbitrary hosted URLs are accepted. Anthropic must return the requested JSON
object in a completed text response; Gemini must support JSON response mode.
Blocked, truncated, tool or invalid outputs fail rather than becoming briefs.
Local loopback is an explicit profile kind, not a fallback for a failed remote
request. Provider dispatches are bounded and have no internal retries or tools;
the separately enabled long-running service can schedule another attempt and
reserve more budget. Cancellation is cooperative; a request already being
dispatched cannot be retracted. A failed socket cancellation logs that the
request may still be in flight. Actual provider and model compatibility still
requires separate operator-approved validation, not offline mocks.

### Connection configuration

The operator can privately configure the proxy, DNS override and hosted-AI and
eToro credentials in the project-root `.env` file or process environment. Do
not overwrite an existing private file or put real settings in chat, source
control or command arguments. The private file is Git-ignored but is plaintext:
restrict access to the file and machine.

For these GoldScout connection settings, an entry in `.env` takes precedence
over the process environment, including an explicitly empty value. An absent
entry falls back to the environment; an existing but unreadable file fails
rather than falling back. On Windows, names match case-insensitively, as in the
environment; elsewhere, names are case-sensitive. Other environment variables
are not overridden. Set `PYTHON_DOTENV_DISABLED=1` in the process environment to disable
private-file loading entirely; this safety switch cannot be overridden by the
file. Blank credential entries make the corresponding provider unavailable.
**A blank `TRADER_PROXY_URL` explicitly disables the proxy, even if the
environment contains a proxy, and may allow a direct request.** The example
therefore leaves its entries commented out. Values are literal; variable
interpolation is not performed. Restart the service after changing connection
settings.

#### Proxy configuration

`TRADER_PROXY_URL` configures the shared HTTP CONNECT proxy for public feeds,
hosted AI and the separately approved eToro read adapter. It does not affect
local-loopback AI or configure other applications; `HTTP_PROXY` and
`HTTPS_PROXY` are not substitutes. For HTTP Basic authentication its form is
`http://<username>:<password>@<proxy-host>:<port>`; percent-encode reserved
characters in both credentials. Only HTTP proxies, either unauthenticated or
using Basic authentication, are supported; integrated Windows authentication
is not. Basic credentials are sent to the proxy without transport encryption;
use a trusted network. HTTPS to the destination still uses certificate and
hostname verification.

A nonempty configured proxy has no direct-connection fallback. Destination DNS
must resolve locally and the proxy must allow CONNECT to the validated IP
address. Configuring the proxy does not start collection or authorize broker
access. For an approved live check, see
[supervised single-source checks](#supervised-single-source-checks).

An operator can instead perform one approved BLS transport check without opening
an intelligence store. It uses the same connection precedence as GoldScout and
refuses to connect when the resolved proxy is blank:

```powershell
@'
from market_intelligence.providers import BLS_CALENDAR_URL, CalendarTransport
from proxy_config import configured_proxy_url

proxy = configured_proxy_url()
if not proxy or not proxy.strip():
    raise SystemExit("TRADER_PROXY_URL is missing; no request made.")
CalendarTransport(proxy_url=proxy).fetch(BLS_CALENDAR_URL)
print("BLS response received through the configured proxy.")
'@ | python -
```

This makes a live public request, not an offline test. It does not parse or
save the calendar, establish complete coverage, or validate broker or AI access.
Keep the URL and any diagnostic output private.

#### IPv4 DNS-server override

`TRADER_DNS_IPV4` is optional and must be a single operator-approved unicast
IPv4 DNS-server address, without a port. Set it privately in the connection
file or process environment. An absent or blank value keeps the system DNS
resolver; a blank value in the connection file overrides a nonblank environment
value. The selected Python environment must have the declared `dnspython`
dependency installed.

The override resolves public feed, hosted-AI and eToro destination hostnames
and configured proxy hostnames. Local-loopback AI does not use it. It queries
both A and AAAA records through the IPv4 DNS server, with IPv4 answers first.
Each query has a bounded lifetime. Invalid settings, DNS failures and excessive
answers fail closed without falling back to system DNS or another server.
Destination address validation and pinning, TLS hostname verification, proxy
requirements and provider approval gates are unchanged.

DNS traffic goes **directly** to the selected server on UDP port 53, or TCP
port 53 for a truncated response; it does not pass through `TRADER_PROXY_URL`.
Use this only where that direct DNS route is permitted and the resolver is
trusted to see queried hostnames. HTTPS still follows the configured proxy, if
any, with no direct-connection fallback.

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

### Supervised single-source checks

Only after separate task-specific approval, a `check` invocation can attempt
one selected source without starting the recurring service loop:

```powershell
python -m market_intelligence check --database intelligence.sqlite3 --allow-network --check-source bls
python -m market_intelligence check --database intelligence.sqlite3 --allow-network --collect-news --check-source fed_monetary
python -m market_intelligence check --database intelligence.sqlite3 --allow-network --collect-news --allow-ai --ai-profiles profiles.json --check-source analysis
```

Select `bea` instead of `bls` to check that calendar. Each invocation applies
pending controls, takes the service lock and may write source evidence, a
durable AI reservation or a brief to the explicitly selected
intelligence store. It makes at most one source or AI dispatch, never a provider
fallback or scheduled retry. An analysis check does not fetch news: it needs
already saved, recent, successfully observed public news and an enabled,
registered AI profile. Even local AI currently requires the network and news
capability flags; the model request itself stays on loopback.

Checks do not write service observations, so they do not erase failures previously
reported for unselected sources. Any returned service observation is from an
earlier service, not this check. The check outcome is returned in its JSON output;
an AI check failure is not separately recorded as a service failure. Retain the
output if a record of that result is needed.

The master and applicable source/AI settings must be enabled. `not_started`,
`failed`, `discarded`, `cached` and `interrupted` results have a nonzero exit
status and do not establish connectivity. `succeeded` means that one result
was validated and committed, not that coverage is complete or the provider
remains connected. A timeout cancels cooperatively and discards late results;
an already dispatched request, in-progress publication or committed reservation
cannot be retracted. System DNS and storage delays can exceed the check's local
deadline. Inspect recorded state after an uncertain interruption rather than
assuming rollback.

### US source assessment

The [BEA subscription page](https://www.bea.gov/news/schedule/icalendar)
identifies the configured calendar feed. The Federal Reserve's
[RSS directory](https://www.federalreserve.gov/feeds/feeds.htm) identifies the
configured monetary-policy press-release feed, not general news. The indexed
official BLS schedule identifies its calendar subscription, but the BLS
documentation page returned 403 during this assessment. These references do
not validate current feed contents, parser compatibility, coverage, retention
rights or rate limits; verify those separately before an approved live check.

### UK source assessment

No UK source is enabled. The official ONS
[release-search documentation](https://developer.ons.gov.uk/search/search-releases/)
describes upcoming/cancelled releases, provisional dates and paginated results;
its [API introduction](https://developer.ons.gov.uk/) labels the API beta. The
published example does not establish a per-entry revision clock or stable
multi-page snapshot. Exact release-calendar retention, attribution, AI-use and
rate-limit terms, date changes and product relevance still need verification
before an adapter can preserve the existing evidence contract.

The [Bank of England legal terms](https://www.bankofengland.co.uk/legal) permit
certain personal or internal non-commercial use and distinguish its database
licence and third-party material. They do not establish blanket permission to
archive every feed or send its text to hosted AI. Verify the exact source,
rights, machine-readable coverage and clocks before considering collection.
Neither assessment is a live feed check or evidence of complete UK coverage.

### Service observations

The dashboard and `status` command show the latest recorded service observation,
its settings revision, reported network/news/AI capabilities and sanitized source
or AI failure codes. Calendar checks and the last saved news acquisition have
separate timestamps. A calendar's saved check may precede a later failed attempt,
whose time is reported separately when available. No secrets, provider response
text or private account data are recorded in service observations.
The dashboard refuses a snapshot if recorded settings, calendar, news or brief
availability is later than its generation time; check the system clock if this
occurs. Each dashboard snapshot and command-line status is read from one pinned
database view.

The service records an observation after settings changes and completed jobs,
and at most every five minutes while otherwise idle. An observation older than
ten minutes is stale; a future timestamp or an observation for a prior settings
revision is not current. A recent observation is **not** proof that the service
is still running, that a provider is connected or that coverage is complete:
even `once` or a stopped service can leave a recent observation. There is no
independent liveness monitor. Old-schema stores and the synthetic dashboard demo
do not provide a service observation.

Reported failures remain visible across service restarts with the same settings
revision and capabilities, and clear from the latest observation after a later
successful result for that source is reported in a new observation. Their original
timestamps remain visible and the append-only history is retained; a displayed
failure may be historical. Reusing an older cached AI brief does not establish
recovery from a reported failure, even if its wall-clock timestamp is later.
Status records accumulate over time and are not automatically pruned. The
long-running service logs and retries transient database lock contention. Other
status-reading or persistence failures stop it with a sanitized error; settings or
source evidence committed earlier may already have succeeded. Check their receipts
rather than assuming a rollback. A fatal failure can also leave only a stale
observation. Analysis retries local storage operations without repeating a provider
call, and cancellation interrupts those waits.

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