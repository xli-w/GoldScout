# Market Intelligence

The local intelligence service collects BLS/BEA calendars and Federal Reserve
monetary-policy RSS, archives source evidence and optionally generates advisory
AI briefs. It never places orders, changes stops or suppresses strategy signals.
`shadow` produces hypothetical calendar observations only. Calendar completeness
is unverified, so unknown coverage never becomes trading permission.

## Setup

Use the selected Python 3.11 environment. Install optional dependencies:

```powershell
python -m pip install -r requirements-intelligence.txt
python -m market_intelligence initialize --database intelligence.sqlite3
```

The bot observer requires `psutil` in the bot's Python environment; it is included
in these optional requirements. If process identity cannot be checked, the
observer logs a warning and stops observing without changing trading. Previous
observations expire normally.

Existing version-1/2/3 intelligence databases require explicit upgrade with the
service stopped. The trading ledger must never be used as this database:

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
python -m dashboard --database bot_ledger.sqlite3 --intelligence-database intelligence.sqlite3 --analysis-controls --analysis-profiles local
```

The dashboard asks for a local analysis-control password directly in the terminal.
The Intelligence page provides a separate login and CSRF-protected analysis-only
settings. Account/order APIs remain read-only. No endpoint accepts an arbitrary
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
that network capabilities are enabled or that the bot observed the revision. The
bot observer reads `intelligence.sqlite3` beside its state file asynchronously
and records only non-authoritative shadow context. Its instance status sidecar
and per-instance close marker invalidate closed or superseded workers, including
a delayed replacement after bounded shutdown. If both close-marker writing and
status invalidation fail, shutdown reports that invalidation could not be
confirmed. Unreadable or malformed status is an invalidation failure, not a
successful close. Stale or missing observations remain unknown.

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
The observer is best effort, not protective-monitor telemetry. Live provider, broker
and hosted-platform validation have not occurred in development. Research campaign
exclusions for news/LLM signals remain unchanged; no automated entry gate is enabled.