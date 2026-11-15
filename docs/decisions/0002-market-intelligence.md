# Decision: Market Intelligence Foundation

Status: implemented initial advisory/shadow release; operational provider/feed validation remains operator-owned

Date: 2026-11-14

## Context

The bot currently evaluates price-based strategies and applies independent execution,
accounting and risk safeguards. An economic calendar and source-linked AI summaries
could improve awareness, but usefulness and returns have not been established.
The first release is advisory/shadow only, with opt-in front-end
enable/disable controls and interchangeable AI providers. Trading influence is
excluded pending separate approval and prospective research.

The [dashboard contract](0001-dashboard-contracts.md) keeps account and trading APIs read-only.
A browser preference is not a bot control or acknowledgement. The separately enabled
analysis-only control routes do not relax that boundary or any safety policy.

## Decision

### Release Completion

The initial release integration is implemented. See [operator guidance](../intelligence.md)
for the current commands and limits; milestone descriptions below retain the design
history. Schema 4 adds restart-safe AI reservations and immutable briefs, preserving
versions 1--3 through explicit upgrades. Local/OpenAI adapters are registered through
nonsecret operator profiles; keys remain server-side. Network/news/AI launch capabilities
are independent of persisted front-end preferences and default off.

The Intelligence page and narrowly scoped authenticated session/control routes are
implemented. Trading/account routes remain read-only. The synthetic demo owns temporary
storage and never reaches a broker/feed/model. Production controls require explicit
launch enablement, same-origin JSON, HttpOnly/SameSite sessions, CSRF and bounded requests.
Queued, applied and bot-observed state are distinct; unsupported or stale observations
remain unknown. The optional asynchronous bot observer never suppresses orders or
changes stops/sizing. Initial coverage is BLS, BEA and Fed monetary-policy RSS, not
comprehensive political news. Proposed EIA/GDELT and research entry gates are extensions,
not silently enabled components of this release. Hosted execution and broker semantics
are not certified by synthetic tests.

### Earlier milestones (historical)

The following foundation, service, ingestion and analysis descriptions record each
milestone as it stood before release completion. Statements about future or missing
features, schema versions and test results in this historical record are not current
status; use Release Completion and the operator guide above.

#### Implemented Foundation

The [contracts](../../market_intelligence/contracts.py) define immutable settings,
calendar events, source coverage and calendar snapshots. Their imports perform no
network, filesystem, credential or broker access. No trading/configuration modules
are imported. The package is currently source-only, not part of the dashboard wheel.

- Master enablement defaults to false. AI defaults to false and requires a named
  operator-configured profile when enabled. Calendar/news preferences are retained
  independently of the master switch. Only advisory and shadow modes are accepted.
- Observation/availability times must be aware UTC datetimes. Scheduled times may
  legitimately be in the future; unavailable event times remain null. Publication,
  source observation and snapshot availability must be chronologically consistent.
- Event IDs are namespaced by source. A snapshot contains only the current revision
   of each event; the local store retains previously published snapshots as history.
   Future ingestion must preserve prior evidence rather than overwrite it. Shadow
   evidence references preserve source and event ID as separate fields, so colons
   in either identifier cannot create ambiguous or duplicate references.
- Empty calendars are distinct from missing source coverage. Healthy source status
  requires an observation and explicit coverage bounds, not just a recent read time.
- Cancelled events are retained as evidence. Tentative dates and unknown relevant
  event times produce uncertainty rather than invented announcement times.

The [pure policy](../../market_intelligence/policy.py) evaluates hypothetical new
entry restrictions. It returns `disabled`, `advisory`, `not_applicable`, `unknown`,
`would_block` or `would_allow`; every result has `affects_trading=False`.
Exits and holds are not evaluated as entry restrictions, including when data is absent.
No call site in the live bot currently consumes these results.

The example window is 15 minutes before to 30 minutes after an event, with an
inclusive start and exclusive end. Source age is at most two hours, inclusive.
These are versioned research assumptions, not calibrated or effective risk settings.
Category selection is explicit; the default requires coverage for all supported
calendar categories. An instrument-specific rollout must configure relevant policy
categories and source responsibilities rather than claiming global completeness.

To evaluate a decision at time `now`, coverage must include potential event times
from `now - after` through `now + before`, and the snapshot must already be available.
Future snapshot information is not replayed into an earlier decision. Missing,
unhealthy, stale or incomplete coverage yields `unknown`, with no trading consequence.
Fresh alternate-source coverage does not validate an event originating from a stale
or unhealthy source. Such relevant evidence also yields `unknown`; no implicit
cross-source supersession or repair is performed.

The separate [wire schema](../../market_intelligence/schema.json) defines settings,
calendar snapshots, news evidence, AI briefs, control requests/receipts and shadow
decisions. UTC wire times end in `Z`, unknown facts are null, collection/text sizes
are bounded, and extra fields are rejected. It is not yet an HTTP API contract.
Existing dashboard schemas are unchanged.

AI briefs require evidence-linked claims, separate interpretation/uncertainties,
affected instruments, completion/expiry times, provider/model/prompt provenance and
input hashes. Recommendations are limited to watch/review/wait, never buy/sell.
Schema validation does not prove a claim is true, references existing evidence,
or justifies a confirmed label. A future ingestion layer must verify those relationships.

Control receipts distinguish pending from applied. Applied requires an effective
revision and application timestamp. Requests accept only intelligence settings,
not account flags, trading limits, arbitrary endpoint URLs or model instructions.
The local service enforces identity, exact revision advancement, expiry, replay and
registered-profile checks. Authentication remains a future HTTP boundary; no browser
can submit controls yet. The schema alone does not enforce semantic relationships.
Bot acknowledgement is a separate future observation, never inferred from an applied
service receipt.

#### Implemented Local Service

The [store](../../market_intelligence/store.py) creates only an explicitly requested,
separate `.sqlite3` database. Readers never initialize missing storage. Existing
foreign, unsupported or invalid databases are refused without reset or repair.
The conventional trading-ledger filename is also refused. No operational database
has been initialized or accessed during development.

- Database application ID, identity and the complete supported version-1/version-2/version-3 schemas are verified
   before normal operations. Schema validation includes constraints, indexes and
   required immutable-evidence triggers, not merely table names.
- Settings history, calendar history, command observations and original request
   evidence are retained. Conflicting inserts, updates, deletes and replacement of
   immutable evidence are refused. Checksums detect inconsistent payloads; they are
   not cryptographic authentication against an operator who can rewrite all evidence.
- Requests bind to the intelligence database and advance settings by exactly one
   revision. Their lifetime is positive and at most five minutes. Identical retries
   return the retained receipt; conflicting request IDs are refused. Pending requests
   expire rather than silently applying after a delayed restart.
- Pending is not applied. Applying settings, recording the effective revision and
   updating the receipt/audit are one transaction. Failure rolls back all of them.
   Applied receipts must link to the exact request settings and committed observation
   time. Every noninitial effective settings revision must have a matching applied
   control; orphan settings are refused. Application cannot precede the recorded
   enqueue observation.
- Payloads and collections are bounded before publication and on read. Indexed
   request/status/history lookups avoid scanning all retained events. Transactions
   use a short SQLite busy timeout and a bounded instruction budget. Python 3.11+
   also enforces a SQLite row-length limit; Python 3.10 uses guarded projections.
- The database uses rollback-journal mode, not WAL. Tests demonstrated that WAL
   read-only access can create sidecars; supported readers instead preserve database
   bytes and directory contents. A bounded SQLite header check refuses WAL before
   SQLite opens the file; refusing existing WAL storage is also directory-preserving.
   There is no transaction held across external IO. Filesystem permissions remain
   necessary; this is not protection against an administrator racing file replacement.
- Calendar publication checks the effective settings revision, enabled flags,
   monotonic snapshot revision and availability. Results from superseded settings
   are refused. Disabling collection retains existing history without authorizing
   its use as current trading information.

The [service](../../market_intelligence/service.py) processes bounded control batches
under a dedicated OS file lock. A second owner is refused; closing or failure releases
ownership. Tests exercise two lock owners in-process, not a separate-process deployment.
The lock is separate from the trading bot's lock. The storage directory must have
operator-managed local filesystem permissions; no remote access is supported.

The [local CLI](../../market_intelligence/__main__.py) supports explicit `initialize`,
`upgrade`, `status`, `once` and `run` actions, always requiring a database path. `status` is
read-only; `once` applies one control batch; `run` processes controls until interrupted.
Initialization and service commands are operator actions, never launched by imports,
the dashboard or development tests against operational paths. CLI failures are sanitized.
Its output explicitly reports no trading authority. `status` reports provider
connectivity as unknown, not disconnected: reading settings does not establish
another process's liveness or network state.
The current CLI has no AI profiles; registered names are accepted by the service API
only for future adapters or synthetic tests. No AI credentials are configured.

#### Implemented BLS And BEA Calendar Ingestion

The [provider module](../../market_intelligence/providers.py) implements the fixed
BLS and BEA ICS endpoints. It uses the optional maintained `icalendar` parser from
[intelligence requirements](../../requirements-intelligence.txt). Contract/provider
imports do not connect to sources or read credentials. This optional dependency
does not change the trading dependency snapshot or dashboard wheel dependencies.

- Network access requires `run --allow-network` at service launch plus persisted
   master and calendar enablement. The flag is invalid for initialize/status/once.
   Enablement is an operator action; development has not launched an operational
   intelligence service or fetched a live calendar. Source endpoints do not require authentication.
- Registered source selection defaults to BLS and BEA. Repeated `--calendar-source`
   flags select specific registered sources or their order, such as BEA only.
   Empty, duplicate or unregistered selections are refused. Source selection does
   not itself enable networking, master collection or any trading influence.
- The transport accepts only the exact registered BLS and BEA URLs, verifies all resolved addresses are
   public/nonreserved/nonmulticast, pins a numeric address and retains the original
   hostname for TLS certificate verification/SNI. When configured, `TRADER_PROXY_URL`
   supplies an HTTP CONNECT proxy, with optional HTTP Basic authentication and no direct fallback;
   the proxy must permit CONNECT to the validated numeric source address. Direct transport
   calls also read that setting without injecting other dotenv values into the process environment.
   It does not use automatic retries, redirects or arbitrary article URLs.
- Response status, type, compression and declared/actual size are checked; downloads
   are at most 1 MiB. Socket connect/read/handshake timeout is three seconds. A
   15-second timer shuts down the current socket, including during the TLS handshake,
   and deadline/cancellation checks surround DNS and each body read. Blocking OS DNS
   is not synchronously interruptible: its result is discarded after cancellation or
   expiry. This is not a hard OS deadline or an OS sandbox.
- One daemon worker fetches/parses without holding a SQLite transaction or delaying
   controls. Per-job cancellation exists before worker start. Successful refreshes are
   nominally hourly per source; failures retry after five minutes per source without
   SDK-internal retries. The oldest due source runs first, with configured order
   breaking ties. Each source retains its own deadlines and sanitized error state.
   Settings revisions and service generations are checked before main-thread publication;
   disabled, superseded or closed-service results are discarded.
- The parser recognizes an explicit small set of BLS release names for inflation,
   employment and growth. Unknown series are counted and ignored, not classified by
   AI or guessed. Duplicate IDs/properties, unsupported recurrence, malformed records
   and future source-observation stamps fail explicitly. Stable source/UID hashes,
   release sequences, cancelled events and aware UTC schedules are retained.
- The shared BEA parser accepts explicit national GDP and Personal Income and
   Outlays release stems, including comma-delimited period labels and parenthesized
   estimates. It deliberately excludes near-prefix and state/county variants rather
   than guessing significance. Both adapters retain source identity even when their
   raw UIDs match. Actual live naming/format compatibility still needs validation.
- Floating, date-only, DST-ambiguous/nonexistent times remain unknown/tentative.
   Future scheduled dates are valid. Publication time is sampled after receiving the
   completed worker result, not before the fetch or from a release date.
- A lower sequence or changed facts at an equal sequence cannot silently replace
   prior event evidence. Conflicting downloads mark source coverage invalid and retain
   the last observed events. Omitted events remain last-known evidence with their
   original receipt times, preserving the comparison baseline across restart. They
   are not evidence of current feed membership. The retained set is bounded to 1,000
   total events; exceeding that limit quarantines the result instead of dropping
   revision evidence. Higher sequences can update them. Source behaviour that
   changes schedules without advancing sequences requires explicit investigation,
   not invented revision repair.
- Download SHA256 and endpoint are retained in source metadata with the successful
   receipt time. Earlier source records load with unknown provenance; no hashes are
   fabricated. The raw ICS file and original UID text are not archived, so the checksum
   and normalized records alone are not a complete replay dataset.
- A successful parse does not prove complete horizon coverage. Both sources' coverage
   remains `unavailable` with null bounds while events and receipt/hash are retained.
   Existing calendar shadow evaluation therefore remains `unknown` for these sources;
   it does not infer `would_allow` from an empty or partial downloaded list.

Fetch/parse failure keeps prior event evidence and its original provenance, marks
the source unavailable/invalid and exposes a sanitized error in the local service.
Another source's success cannot erase this error or its prior events/hash/timestamps.
The sources share one worker: transient completed failures do not starve another
due source, but a blocked OS resolver can still delay all collection until it returns.
Controls remain responsive; this is not independent parallel source execution.
That error code is currently volatile; persisted source status and timestamps remain
available after restart. Source heartbeat telemetry and browser controls are still
future work. Live endpoint format, terms, actual latency and coverage are not certified
by the synthetic tests.

#### Implemented Fed RSS Evidence Foundation

The [news module](../../market_intelligence/news.py) adds immutable `NewsEvidence`
and `NewsBatch` records plus a parser for the fixed Federal Reserve monetary-policy
RSS feed. The existing transport now accepts that exact feed URL with XML/RSS
content types while retaining DNS/TLS, cancellation, redirect and byte-limit checks.
Article references are not transport endpoints and are never fetched by the parser.

- `defusedxml`, an optional [intelligence dependency](../../requirements-intelligence.txt),
   rejects DTDs, entity expansion and external references. Input bytes, XML depth/node
   count, feed items and normalized text lengths are bounded. Parsing accepts the
   supported RSS 2.0 shape; unsupported/malformed formats fail explicitly.
- Titles/snippets become bounded plain text. Script/style/frame/object contents and
   markup attributes are excluded. Block breaks separate paragraphs, while inline
   formatting must not alter words or numerical values. Source directives remain
   untrusted text, not executable instructions or permission for an AI/tool action.
- Article URLs must reference canonical HTTPS Fed press-release paths without
   credentials, query strings, encoded paths, dot/empty segments or backslashes.
   Harmless fragments are removed. Arbitrary links and ambiguous path aliases are
   rejected rather than followed or treated as distinct confirming sources.
- Every item records actual parser receipt time. Valid aware publication dates are
   converted to UTC; missing or floating dates remain null. Future publication times
   are refused. A receipt timestamp does not establish original publication history.
- Canonical URL, title, snippet and known publication time determine content hash
   and evidence ID. Identical items collapse. Changed text creates distinct evidence
   in the same article cluster; neither RSS GUIDs nor list ordering invent supersession.
   All items retain the same Federal Reserve publisher family, so copies are not
   independent corroboration. The batch retains the original feed SHA256.
- The pure parser cannot authenticate an arbitrary supplied payload's origin. Its
   source label identifies the configured adapter; a future ingestion handoff must
   pair records with the fixed-host certificate-verified transport and durable source
   provenance. Schema validity and an official source label do not prove a claim true.

#### Implemented Durable News Archive

The [version-2 store](../../market_intelligence/store.py) adds immutable raw feed
bodies, acquisitions and first-seen normalized evidence. New storage now initializes
at version 3, adding the source-observation journal described below. Existing
version-1/version-2 databases remain supported for their original capabilities;
initializing or reading an existing database does not upgrade it.
News operations on version 1 refuse with `upgrade_required`.

An operator must explicitly run the `upgrade` action to add the new tables. Upgrade
requires the dedicated service lock, validates the old schema and settings, creates
only additive news objects and advances the schema version in one transaction.
Failure rolls back the entire transition. Identity and every original settings,
receipt, command-event and calendar row are preserved. Repeated upgrades do not
reset state. Unknown/foreign/future schemas are refused, never guessed or repaired.
CLI output includes the observed schema version. No operational database was
upgraded during development; fixtures and commands in tests use temporary paths.

- Every acquisition binds raw-byte SHA256, normalized `NewsBatch`, parser version,
   source receipt time, settings revision and committed availability time. Raw RSS
   and normalized metadata commit together with newly observed evidence; failure
   returns no archive acknowledgement and leaves no partial acquisition.
- The current parser identity is `fed-rss-v1`. Publication checks that parsing the
   supplied raw bytes reproduces the supplied normalized batch; readers replay the
   same registered parser and verify checksums and historical applied settings.
   Future parser changes require explicit version handling, not reinterpreting or
   silently rewriting old evidence. Parser dependencies remain required for replay.
- Master and news flags must be enabled under the captured settings revision.
   Results from superseded settings are refused. Receipt cannot precede the effective
   settings observation; commit cannot precede receipt or the prior acquisition.
   Historical reads remain valid after disabling collection.
- Acquisition IDs make identical retries idempotent and reject conflicting inputs.
   Repeated downloads share a content-addressed raw body, but retain separate
   acquisition observations. An unchanged article retains its immutable first receipt;
   later acquisitions preserve their own receipt time instead of refreshing history.
- Changed articles retain separate evidence versions within their article cluster.
   No supersession/correction relationship is invented from RSS order, GUID or price
   reaction. The evidence reader verifies its first acquisition relationship.
- Raw bodies are bounded to 1 MiB, batches to 200 items and serialized metadata to
   the existing 512-KiB limit. Reads use guarded projections and bounded indexed
   lookups. SQLite row limits accommodate the explicitly bounded raw body. Raw and
   normalized corruption, invalid provenance and unsupported schemas are refused.
- News tables reject updates, deletes and replacement of existing evidence. Archive
   readers neither create storage nor change database/directory contents. These are
   local integrity contracts, not authentication against an administrator capable of
   replacing the entire database or proof that an arbitrary caller obtained bytes
   from the Federal Reserve.

#### Implemented Scheduled News And Source Status

The [service](../../market_intelligence/service.py) can now collect the fixed Fed
RSS feed through its existing single worker. This requires explicit `run
--allow-network --collect-news`, persisted master/news enablement, and a version-3
intelligence database. Without the new capability flag, calendar-only behaviour is
unchanged. AI processing is never enabled by news collection.

Version 3 adds only an append-only source-observation journal and index. Explicit
`upgrade` can migrate version 1 or version 2 transactionally to version 3; original
schemas and rows remain unchanged. Legacy manifest fingerprints and populated
version-2 upgrade fixtures protect that compatibility. Readers never create the
journal or upgrade implicitly. No operational migration was run in development.

- Poll completion records attempt identity, captured settings revision, submission,
   completion and committed availability times. Status is successful, unavailable
   or invalid, with a registered sanitized error code and last-success acquisition.
- Successful archive and source-status publication are one transaction. Failed
   attempts do not invent an acquisition; they retain the last successful archive
   link and its original evidence. Restart preserves both observations and failures.
- News refreshes nominally every five minutes on success or failure. Restart restores
   the remaining wait from the persisted observation when its settings revision still
   matches. A settings change invalidates in-flight work and resets scheduling.
- Calendar/news switches remain independent. Both use oldest-due selection on one
   worker; news polls cannot overwrite calendar snapshots. A blocked OS resolver can
   delay all collection, but not serialized local control processing. No hard outage
   or polling-latency guarantee is claimed.
- Disabling or superseding settings, or closing a service generation, cancels/discards
   a result before source-status or archive writes. Unknown exception details become
   a generic registered error, never raw messages or credentials in persistent status.
- CLI `status` exposes `last_news_observation` as a recorded historical fact, not
   current connectivity, freshness, liveness or trading permission. Its age must be
   evaluated separately; the configured five-minute poll is not proof of fresh data.

Source-observation readers verify persisted structure, historical applied settings
and referenced archive evidence. Unknown/unsupported/corrupt records are refused.
News HTTP resources and front-end/bot integration remain unimplemented. The injected
advisory analysis core below is not connected to the scheduler. Live feed behaviour
and source coverage remain unvalidated.

#### Implemented Advisory Analysis Core

The [analysis module](../../market_intelligence/analysis.py) provides interchangeable
operator-owned JSON completion callbacks, immutable profile/model metadata and
validated advisory briefs. It configures no SDK, hosted endpoint, credential or
automatic AI job. Imports perform no network, broker or operational-state access.
The existing service still does not invoke this analyzer.

- Disabled master/news/AI settings return without reading source state or invoking a
   provider. Enabled calls require a registered profile, a validated archived batch,
   matching successful source observation, and current settings/status readers.
- Inputs project only news evidence IDs, family/source/URL, title/snippet and known
   publication time plus configured instrument names. No account, position, broker,
   raw internal log, model credential or browser-controlled endpoint fields are sent.
   Source text remains untrusted data; callbacks are trusted operator-owned adapters,
   not model-generated code or an OS sandbox.
- One call is active per analyzer. Default per-call input is at most 32 KiB including
   the system prompt; responses are at most 32 KiB and must be a strict JSON object
   without duplicate fields, nonfinite values or extra provider-owned metadata.
- Objective claims must be exact quotations from every referenced title/snippet in
   the selected article cluster. Unknown references and unsupported paraphrases are
   rejected. This establishes quotation/reference consistency, not truth. Paraphrases
   and possible transmission channels belong in explicitly unverified interpretation.
- Output supports only configured instruments and watch/review/wait recommendations.
   At least one uncertainty is required. The server owns IDs, model/provider, prompt
   and schema versions, settings revision, validated availability and expiry. A model
   cannot mark itself confirmed, authorize trading or supply future availability.
- Current settings, source status, provider selection and cancellation are rechecked
   before dispatch and acceptance. Stale, failed or future source observations and
   replaced profiles are rejected. Source freshness defaults to 15 minutes; receipt
   freshness is not proof that an old article describes today's market conditions.
- The cache is bounded to 256 keys and expires briefs after 30 minutes by default.
   Keys include provider/model/prompt/settings revision, cluster and normalized input
   SHA256. Unchanged evidence can reuse a valid brief without a provider reservation.
   Cache availability/expiry is rechecked at the final current-state sample.
- Defaults reserve at most 25 calls, 256 KiB of input and 65,536 output-token allowance
   per UTC day. The per-request output allowance is 2,048 tokens. Reservations remain
   consumed after dispatch failures, invalid output or late results. Clock rollback
   is refused, including after an unsuccessful reservation on a later day.

These budgets are process-local, not durable account/billing controls. Restart or
independently constructed budgets do not share usage. The output-token allowance
must be enforced by the future SDK adapter; byte checks and provider-reported total
tokens do not prove exact token usage, dollar costs or a hard billing ceiling.
Durable reservations and explicit hosted-provider limits are prerequisites to live
service integration. No automatic provider fallback or hidden retry is implemented.

The default 30-second callback latency check rejects late results after return;
it cannot forcibly interrupt arbitrary callbacks or undo a remote charge. Future
network adapters must honor cancellation/timeouts and the configured proxy policy.
Briefs are not persisted yet, and no freshness/significance routing automatically
invokes this layer. Hosted/local adapters, durable budget/brief evidence, scheduler
handoff, HTTP controls and bot influence remain separately scoped work.

### Next Implementation Slices

These were the next slices at the time, not a current backlog. The initial release
completed the adapters, controls and observer; additional sources and trading
influence remain outside that release.

1. Extend the calendar adapters and add source adapters to the separate service/store. They must publish
   against their captured settings revision, preserve source evidence and reject
   late results after disabling or changing profiles. The accounting ledger remains
   untouched; service failure cannot halt current trading in advisory/shadow mode.
2. Connect concrete AI adapters and durable reservations/brief storage to the implemented
   provider/validation layer on the archived evidence and source-status
   boundary. Remaining free-first adapters include Federal Reserve
   calendars and other official RSS feeds. Add EIA after verifying schedule exceptions, then optional GDELT
   discovery. Source format, terms, limits and actual latency require validation;
   discovery does not grant rights to copy publishers' full articles. Exact FOMC
   announcement times must not be inferred from meeting dates alone.
3. Deduplicate by source/publisher family before bounded, cached AI summarisation.
   Add one hosted adapter and an optional configured local-compatible adapter,
   without assuming providers share identical structured-output behaviour. Enforce
   output/evidence validation, budgets and no tool execution. Secrets stay server-side;
   account data and broker credentials never enter AI inputs. Future network adapters
   must honor `TRADER_PROXY_URL` without direct fallback when it is configured.
4. Introduce a specifically approved local analysis-control API, disabled unless
   explicitly enabled at launch, with authentication, CSRF/same-origin protection,
   narrow settings allowlists, idempotency, revision/expiry checks and honest pending
   states. Existing account APIs remain read-only. Do not weaken existing middleware
   globally. No auth/credential/safety-hook changes are part of this milestone.
5. Add an independently loading Intelligence dashboard page using synthetic fixtures.
   Show calendar/brief/decision freshness and requested/service-applied/bot-observed
   controls separately, even if account storage is unavailable or the bot is stopped.
6. Add a non-authoritative bot shadow hook without extra broker calls or changes to
   exits, risk gates, stops, quantity, candle consumption or deferred-entry behaviour.
   Validate mocked order-call parity before any bot integration is accepted.

## Consequences

The following describes the earlier milestone, not the completed release.

This milestone implements explicitly enabled BLS, BEA and Fed RSS fetching, but development did not
fetch a live feed, call AI or launch an operational service. Orders, UI controls and
existing dashboard/trading behaviour do not change. Tests use synthetic payloads,
fake connections and temporary storage. Optional ICS and safe XML dependencies were
installed into the selected development environment. CI, safety hooks, credentials handling,
the trading dependency snapshot and operational files are unchanged.
News evidence ingestion/archival and injected-provider analysis are implemented;
hosted AI calls and service integration remain unimplemented. Local control application and synthetic provider behaviour
are tested, but HTTP authentication,
browser commands and bot-observed settings have not been implemented. Live provider
semantics remain unvalidated.

Calendar adapters need maintained ICS/structured parsers and safe bounded fetches.
Source references in these contracts are not permission to fetch arbitrary URLs.
The current transport allowlists BLS/BEA calendars and the fixed Fed RSS feed and refuses unsafe addresses/redirects;
additional source endpoints need their own verified allowlist and parsing contracts.
News availability is at receipt, and AI availability is at validated committed
completion, not the original article's timestamp. AI provider changes create new
provenance instead of silently rewriting archived summaries.

Historical research must retain calendar revisions and contemporaneous inputs/results.
Running today's model on old headlines is not causal out-of-sample evidence.
Future deterministic entry gating requires separate approval, failure-policy design,
baseline comparisons and locked report-only holdouts. Profitability and protection
latency are not promised.

## Verification

The following records earlier milestone checks; it is not a current full-release validation.

[Focused regressions](../../tests/test_market_intelligence.py) use fixed UTC clocks,
immutable synthetic records and no broker calls. They cover default-off behaviour,
exit preservation, mode/authority restrictions, exact window and freshness boundaries,
cancelled/rescheduled/unknown events, positive empty-calendar coverage, missing/stale
sources including overlapping provenance, future availability, immutability and
invalid inputs. Structured-reference collision cases retain both events. Wire rejection
checks include newline identifiers, credential-bearing references and non-HTTPS ports.
Schema fixtures cover
actual contract records plus evidence, briefs and pending/applied control states.
Schema checks require the existing dashboard development dependencies; pure policy
checks are dependency-free. Skipped schema checks must be disclosed.

```powershell
python scripts/offline_tests.py --pattern test_market_intelligence.py
python scripts/offline_tests.py --pattern test_intelligence_controls.py
python scripts/offline_tests.py --pattern test_intelligence_providers.py
python scripts/offline_tests.py --pattern test_intelligence_news.py
python scripts/offline_tests.py --pattern test_intelligence_archive.py
python scripts/offline_tests.py --pattern test_intelligence_polling.py
python scripts/offline_tests.py --pattern test_intelligence_analysis.py
```

[Control/service regressions](../../tests/test_intelligence_controls.py) use real
temporary SQLite databases. They cover request lifetime/revision validation,
restart, duplicate/conflicting IDs, competing revisions, late-result rejection,
rollback failure injection, immutable/replacement evidence, missing triggers,
foreign/future schemas, corrupt metadata, application provenance, clock rollback,
directory-preserving readers, 20,000 retained expired requests, lock ownership and
sanitized CLI failures. Tests never initialize the operational intelligence store.
The guarded-read fallback is exercised with the SQLite row-limit API hidden;
this is not execution on an actual Python 3.10 runtime. Full regression results
and review status are reported separately in the implementation handoff.

[Provider regressions](../../tests/test_intelligence_providers.py) cover synthetic
ICS revisions and timezone/uncertainty cases; fake DNS/HTTP address pinning,
redirect/encoding/size/deadline refusals; original-host TLS verification and
cancellation using the real connection class with fake sockets/context; explicit
network/master enablement, background cadence, failed-source recovery, publication
timing, pre-worker cancellation and stale-generation/revision discard. Optional
parser tests skip when `icalendar` is absent; disclose those skips. None contact a
live calendar, news, AI or broker service.
Multi-source cases additionally cover exact BEA host/path routing, GDP/PCE title
boundaries, equal UID namespaces, oldest-due ordering, source-isolated success and
failure/recovery, preserved BEA provenance during BLS refresh, BEA cancellation and
resume, and CLI default/explicit/duplicate/unknown selections with mocked startup.

Full offline regression and independent review results are reported in the
implementation handoff. No authenticated broker or live provider validation is
part of this milestone.

[News regressions](../../tests/test_intelligence_news.py) cover synthetic RSS identity,
receipt/publication uncertainty, duplicate/version handling, entity/DTD refusal,
unsafe URL aliases, bounded XML/text, fidelity across inline formatting, script/attribute
exclusion, wire/schema roundtrips and fixed-feed transport routing. Parser tests skip
when the optional XML dependency is absent; schema checks require dashboard development
dependencies. Disclose any skips. No tests call a live RSS, AI or broker endpoint.

[Archive regressions](../../tests/test_intelligence_archive.py) use temporary SQLite
fixtures for version-1 preservation, explicit/locked upgrades, repeat upgrades,
transactional migration failure, unknown-schema refusal, raw/normalized roundtrips,
idempotent/conflicting IDs, repeated first receipts, changed article versions,
settings/clock gates, commit/post-insert failure recovery, immutable evidence,
directory-preserving reads, 1-MiB bodies, 200-item batches and corrupted raw,
normalized or first-acquisition references. Parser-based cases skip if the optional
XML dependency is absent. No operational storage is read, copied or upgraded.

[Polling regressions](../../tests/test_intelligence_polling.py) cover source-status
identity/clock contracts, atomic archive/status failure injection, idempotent attempts,
last-success retention, explicit collection flags, restart-after-success/failure
cadence, mixed calendar/news scheduling, unknown error sanitization, in-flight disable,
recorded CLI status and wire validation. All feeds and storage are synthetic/temporary;
no live RSS, AI, broker or operational-service validation is part of these checks.

[Analysis regressions](../../tests/test_intelligence_analysis.py) use immutable
synthetic records and callback providers. They cover default-off behaviour, source
guards, exact evidence quotes, profile swapping/replacement, strict output/JSON,
limits, caching/expiry, budget/day rollback, stale settings/cancellation and late
responses. No hosted AI provider or real credential is used. The wire-schema check
requires the existing dashboard development dependencies; disclose any skips.