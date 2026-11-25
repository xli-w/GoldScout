# Market Intelligence

The local intelligence service collects selected BLS/BEA calendars, selected
ONS releases, Bank of England MPC dates and Federal Reserve monetary-policy RSS,
archives source evidence and optionally generates advisory AI briefs. It never
places orders, changes stops or suppresses strategy signals.
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
ONS and BoE calendar adapters are now registered as opt-in, limited UK sources;
they do not provide full UK or global coverage. Issuer coverage remains a proposed
extension requiring approved sources and coverage tests, not a capability inferred
from an empty calendar.

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

Existing version-1 through version-7 intelligence databases require an explicit upgrade for
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
Version 6 gates the new UK calendar payload contract and records all ONS result
endpoints contributing to a combined input hash. Upgrade version-5 databases
explicitly before selecting ONS or BoE; the upgrade preserves existing evidence
and does not migrate or rewrite calendar records. Older application builds reject
version-6 stores rather than silently reading a payload they may not understand.

Version 7 adds immutable public-content acquisitions, first-availability
documents, retained briefing manifests, event revision baselines and durable
synthesis operations. Version 8 adds day-precision publication dates for
date-only source metadata and a new public compiler/briefing wire version.
Date-only releases are eligible only when their source date is no later than
the current UTC calendar date and remains within the topic lookback; no
publication instant is invented and receipt time alone cannot make a dated
release fresh. The parser can derive this date
from the retained raw bytes of version-7 acquisitions without rewriting them.
The investment journal remains version 2. Old intelligence stores remain
readable; reads do not upgrade them. Writes to pre-version-6 calendars retain
their historical wire shape or reject newer fields before mutation. Upgrade
preserves historical payload bytes and existing budget use.

## Cross-source public briefings

This is a partial foundation, not complete live economic coverage. BLS CPI and
Employment Situation content remain unsupported after source-format access was
denied during assessment. Several policy/PCE endpoints are explicitly dated
snapshots, not rolling discovery. Structured extraction of rates, units and
vintages is not implemented: facts are retained source excerpts. The minimum
planned current UK/US outcome coverage is therefore not yet met.

The compiler groups selected official documents by topic and UK/US geography.
Scheduled releases stay explicitly separate from published outcomes. Successful
selected-calendar downloads do not establish completeness: collection provenance
can be present while coverage remains unknown. Unavailable sources and input
omissions remain visible in the retained manifest.

One public-only synthesis call covers an eligible changed bundle. No account
names, balances, quantities, private notes, confirmed private mappings or
portfolio-dependent retrieval lists enter this request. The model has no tools
or browsing. Facts must quote retained evidence exactly; numeric values, periods
and units cannot be invented in qualitative interpretation fields. Interpretations use a small explicit topic-template vocabulary selected by the
model, not unrestricted generated factual prose; unsupported narrative is
withheld. Interpretations are labelled unverified. These checks do not prove semantic
correctness or a causal relationship; read the cited documents.

Repeated checks and daily London editions reuse unchanged public evidence
without changing the original generation time. Corrections change semantic
identity. Source checks, current edition, generation time and UTC budget day
are separate clocks. A stale or failed source check never renews evidence.
Prior successful briefings remain available after failures with their original
dates; old one-hour news briefs retain their existing contract.

Synthesis reservations share the historical daily call/input/output allowance.
An append-only operation records dispatch intent before calling the provider.
Timeouts, cancellations and owner loss can leave an uncertain paid outcome:
the allowance is retained and that operation is not automatically dispatched
again. Validated saved results can finish publication after restart without
another model call. No exactly-once provider-delivery guarantee is made.
Replay rejects unbacked spending reservations or transitions reopening a terminal
operation. Briefings are unavailable before their committed generation time.

Local relevance is a separate projection over the recorded journal and
user-confirmed public exposure mappings. Every match explains whether it follows
a supported classification or a declaration. Missing valuation does not prevent
a qualitative match, but it does not permit invented portfolio weights. Neither
the projection nor public synthesis changes journal records or starts orders.

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
eToro credentials in the Settings page, the project-root `.env` file or process
environment. Settings values are stored as plaintext in the ignored
project-root `.goldscout-connections.json`; keep the checkout and computer
private. Do not put real settings in chat, source control or command arguments.

For these GoldScout connection settings, a saved Settings value takes
precedence over `.env`, which takes precedence over the process environment.
Clearing a Settings override falls back to those existing sources. In `.env`,
an explicitly empty value takes precedence over the process environment; an
absent entry falls back to the environment. An existing but unreadable private
file fails rather than falling back. On Windows, names match case-insensitively,
as in the environment; elsewhere, names are case-sensitive. Other environment
variables are not overridden. Set `PYTHON_DOTENV_DISABLED=1` in the process
environment to disable both private-file sources entirely; this safety switch
cannot be overridden by either file. Blank credential entries make the
corresponding provider unavailable.
**A blank `TRADER_PROXY_URL` explicitly disables the proxy, even if the
environment contains a proxy, and may allow a direct request.** The example
therefore leaves its entries commented out. Values are literal; variable
interpolation is not performed. Close older GoldScout processes before changing connection settings. Changed
eToro keys invalidate the saved dashboard connection; activate it again before
another dashboard read. A running CLI watch stops when its pinned keys change
and must be restarted to use the new keys. A request already dispatched
cannot be recalled.

#### Proxy configuration

`TRADER_PROXY_URL` configures the shared HTTP CONNECT proxy for public feeds,
hosted AI and the separately activated eToro read adapter. It does not affect
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

### Source scope and limitations

The [BEA subscription page](https://www.bea.gov/news/schedule/icalendar)
identifies the configured calendar feed. The Federal Reserve's
[RSS directory](https://www.federalreserve.gov/feeds/feeds.htm) identifies the
configured monetary-policy press-release feed, not general news. The indexed
official BLS schedule identifies its calendar subscription, but the BLS
documentation page returned 403 during this assessment. These references do
not validate current feed contents, parser compatibility, coverage, retention
rights or rate limits; verify those separately before an approved live check.

UK adapters are registered but are **not selected by default**. The official ONS
[release-search documentation](https://developer.ons.gov.uk/search/search-releases/)
describes upcoming/cancelled releases, provisional dates and paginated results;
its [API introduction](https://developer.ons.gov.uk/) labels the API beta. The
adapter selects release titles matching a limited set of inflation, labour
market and economic activity terms; it is not a complete ONS catalogue and does
not map events to holdings. It fetches the upcoming and cancelled result sets
separately, accepts only complete responses of at most 1,000 records each, and
publishes neither if either fetch or validation fails. It keeps recent
cancellations (scheduled within the prior 30 days), provisional/postponed
status and available date-change notes. The API does not provide a dependable
per-entry revision clock or exact change-publication time: `available_at` is
the local receipt time, not the ONS publication time. Pagination or a changed
API contract fails closed rather than being treated as complete coverage.

The [Bank of England legal terms](https://www.bankofengland.co.uk/legal) permit
certain personal or internal non-commercial use and distinguish its database
licence and third-party material. They do not establish blanket permission to
archive every feed or send its text to hosted AI. The MPC adapter reads the
official upcoming-dates page and creates dated entries from confirmed-year
Thursday schedules, using noon Europe/London as the scheduled time. It does
not collect rate decisions, minutes or economic commentary. Past dates are
excluded, and at least one full future year of eight Thursday dates is required;
a changed or incomplete schedule fails closed. Neither UK adapter has been validated against a
live GoldScout collection in this implementation; source rights, provider
compatibility, coverage and current rate limits remain unverified. Empty UK
calendar results are not evidence that no releases or meetings exist.

The existing default remains BLS and BEA. UK sources must be selected
explicitly; for example, after separate task-specific approval, one-source
checks can be run with:

```powershell
python -m market_intelligence check --database intelligence.sqlite3 --allow-network --check-source ons
python -m market_intelligence check --database intelligence.sqlite3 --allow-network --check-source boe_mpc
```

An ONS check makes two requests, one for each result set, but remains one
supervised source check. Routine collection can instead select one or both UK
sources with repeated `--calendar-source` options. These public feeds remain
opt-in, advisory evidence; calendar entries have no instrument mapping or
trading authority.

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

## Bounded official release-content adapters

`market_intelligence.content` is a pure parsing/one-request collection boundary,
not a crawler, database writer, calendar-outcome inference engine or numeric
extractor. `CONTENT_SOURCES` maps stable capability IDs to immutable metadata
(`institution`, `url`, `kind`, `topic`, `geography`, `supported`, `reason`).
Support means the specific bounded excerpt shape below has synthetic regression
coverage. It does **not** mean current provider compatibility, complete coverage,
automatic rolling policy coverage, rights clearance for every use or live
GoldScout validation. Public official pages were consulted for source shapes and
terms; no GoldScout live provider or operational configuration was used.

### Content source matrix and transport endpoints

Only the five supported fixed URLs below need transport registration, with
identity encoding, the existing HTTPS/address/proxy controls, no redirect or
direct-network fallback, and a response cap of 1 MiB. A link retained as a
document citation is never another fetch instruction.

| Capability | Institution / scope | Fixed fetch URL | Selected content and limitations |
| --- | --- | --- | --- |
| `ons_cpi` | ONS / UK inflation / release | `https://www.ons.gov.uk/economy/inflationandpriceindices/bulletins/consumerpriceinflation/latest` | Bulletin `h1` and all list items in the unique `#main-points` block, requiring both CPIH and CPI labels. Not tables, forecasts, arbitrary ONS releases or all release revisions. A transport refusing a redirect fails rather than following an unregistered target. |
| `boe_policy` | Bank of England / UK monetary policy / policy | `https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/2026/november-2026` | **Dated November 2026**, direct paragraphs of the section headed “Monetary Policy Summary, November 2026”. Not minutes, current Bank Rate metadata, related links or future decisions. Personal/internal non-commercial scope only, subject to the Bank's terms below. |
| `bea_gdp` | BEA / US growth / release | `https://apps.bea.gov/rss/rss.xml` | First feed item whose title starts `GDP (Advance/Second/Third Estimate),` or `Gross Domestic Product (Advance/Second/Third Estimate),`; its description is an official **summary excerpt**, not a full release. Regional GDP, other GDP series and later historical feed items are deliberately excluded. |
| `bea_pce` | BEA / US inflation / release | `https://www.bea.gov/news/2026/personal-income-and-outlays-september-2026` | **Dated September 2026** release: the two direct body paragraphs beginning “From the preceding month,” and “From the same month one year ago,” containing `PCE price index`, before the first table/technical section. Not nominal consumption treated as inflation, tables, technical notes or rolling releases. |
| `fed_statement` | Federal Reserve / US monetary policy / policy | `https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm` | **Dated 28 October 2026** FOMC statement paragraphs in the official article body, ending before media enquiries. Not the headline-only RSS description, implementation note, minutes, projections or schedule announcements. |
| `bls_cpi` | BLS / US inflation / release | Unsupported; candidate `https://www.bls.gov/news.release/cpi.nr0.htm` is **not** a required allowlist addition | Official public-page/text research returned Access Denied. No parser compatibility or complete release shape is claimed; no alternate-network bypass is attempted. |
| `bls_employment` | BLS / US employment / release | Unsupported; candidate `https://www.bls.gov/news.release/empsit.nr0.htm` is **not** a required allowlist addition | Official public text research returned Access Denied. No speculative Employment Situation parser or numeric outcomes. US employment coverage remains absent. |

Dated capabilities are intentionally finite evidence, not automatically updated
“latest” aliases. Adding a different dated URL requires a reviewed registry/parser
change and transport registration, not accepting a URL from a feed, webpage,
operator text or model. They may already be outside a downstream compiler's
freshness window. In particular, date-only UK publication metadata remains
unknown at instant precision; a new receipt must not be described as proof of
a newly issued policy decision. The date is retained for conservative day-level
freshness, not converted to a guessed publication instant.

### Parser assumptions, clocks and quoting

`parse_content(name, raw_bytes, received_at)` returns one complete
`SourceDocument` tuple or raises `ContentError`; unavailable selected content is
not an empty healthy result. HTML must be UTF-8, end with `</html>`, contain unique
selected blocks and explicitly closed selected quotation elements. HTML
complexity is bounded to 30,000 tags and depth 128. Scripts/styles/forms/frames
inside selected quotations, duplicate attributes, unexpected selected shapes,
invalid dates, XML DTD/entities and partial pages fail closed. These conservative
checks may reject legitimate future markup changes; they are not claims of a
general-purpose HTML parser. RSS is one complete RSS/channel, at most 1,000
items; only the **first matching national GDP item** is selected, and malformed
selected fields fail rather than falling back to an older item. Historical
unselected feed entries may lack descriptions.

Source punctuation and words are retained as plain text with HTML entity
decoding and whitespace normalization. Only the documented excerpts are quoted.
Title and text are non-empty, bounded to 500 and 12,000 characters, respectively;
overlong selected content is rejected, never silently truncated. The public
evidence contract does not accept literal angle brackets or control characters.
No actual/forecast/previous, rate-decision value, CPI/PCE value, numeric
surprise, vintage or structured time-series extraction is claimed.

The supplied clock runs after the fixed fetch, establishing aware-UTC receipt,
not publication or market availability before receipt. A source publication
instant is retained only when explicitly stated: BEA RSS RFC-style dates with an
explicit offset/EST/EDT/GMT; BEA release embargo date/time with explicit EST/EDT;
Fed displayed release date plus explicit EST/EDT release time. Missing exact
publication metadata stays `None`; date-only ONS/BoE labels are retained as
`published_on` calendar dates and are not converted to guessed midnight/noon
UTC. Invalid/conflicting selected dates, ambiguous time-zone labels and
publication after receipt are rejected. Date-only documents are excluded
before their stated UTC calendar day and after their topic lookback window; a
same-day receipt does not reset that window.
`scheduled_at` remains `None`; schedules never manufacture realized outcomes.
`document_id` hashes normalized content/source and any exact publication
instant, excluding receipt; date-only publication metadata instead participates
in the version-2 compiler's semantic input hash.
Publisher families are stable `ons`, `boe`, `bea`, `fed` IDs.

`collect_content(name, transport, clock, cancel_event)` makes exactly one
`transport.fetch(fixed_url, cancel_event=...)` call, checks cancellation before
dispatch and before return, and returns the **original raw bytes** plus parsed
documents. No persistence, archive rewriting, retries, linked-document fetches,
credentials, broker access or AI calls occur here. The parent owns archival,
re-parsing/commit and enabled-network policy.

### Rights and validation boundary

The [ONS terms](https://www.ons.gov.uk/help/termsandconditions) identify most
website content as Crown copyright under OGL v3, with attribution and explicit
third-party exemptions; these adapters select textual official bulletin points,
not images, logos or third-party material. The
[Federal Reserve disclaimer](https://www.federalreserve.gov/disclaimer.htm)
identifies Board information as public domain unless otherwise indicated and
requests source citation; third-party material and insignia are excluded.
BEA selection is limited to the agency's own US-government release prose,
not linked third-party content or database licence claims.

The [Bank legal terms](https://www.bankofengland.co.uk/legal) permit downloading,
displaying or printing for personal use or internal non-commercial use and
distinguish Bank database licensing and third-party rights. The `boe_policy`
parser is limited to that textual personal/internal-use basis. It is **not**
blanket clearance for redistribution, commercial exploitation, sending Bank
text to hosted AI, training, or treating policy prose as OGL database data.
Those uses require independently established permission/terms; an entry marked
supported is a parser capability, not a grant of rights. Unknown rights for a
different source or use remain unsupported rather than being inferred from
public accessibility.

Regression fixtures are synthetic and offline, with explicit tests for selected
content, bounded text/raw bytes, revisions/receipt-independent IDs, source dates,
cancellation, no linked fetching, malformed/partial selected content and
unsupported sources. The focused command is
`py -3.11 scripts/offline_tests.py --pattern test_intelligence_content.py`.
There has been **no live GoldScout collection validation** of these adapters.