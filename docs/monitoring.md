# Portfolio monitoring

## What is implemented

Monitoring reads an existing opened investment journal and an explicitly
selected local profile. It reports review reminders, missing/stale valuation,
cash discrepancies, configured instrument concentration, role/denominator gaps,
cost-coverage limitations and LISA evidence checks. Findings are deterministic,
source/revision-bound review prompts, not orders or guaranteed improvements.

The dynamic share uses completely valued, fresh, role-assigned **non-LISA
investment holdings only**. Savings, LISA and all cash are excluded. Missing
classification/marks or unassigned eligible quantities make it unavailable.
The illustrative 10-20% range is not a default target or breach threshold.
Concentration means exact instrument share, not underlying fund overlap.

The dashboard's synthetic demo includes monitoring without making any account
calls. Recorded monitoring defaults off. Refresh and optional minute polling
read only local data. Existing accounting/awareness wire contracts, journal
formats, manual recording gates and intelligence remain independent.

## Local profile and commands

Use the selected Python 3.11.9 environment (Python 3.10 remains supported).
Never select foreign/intelligence storage or use operational files for
tests. The following are operator commands for explicitly selected local files,
not instructions to initialize or maintain an operational store automatically.

Generate a nonsecret profile template to stdout:

```powershell
python -m investment monitor-template --database .\investment.sqlite3 --max-age-seconds 86400 --review-interval-days 30
```

Save and edit it locally. The command binds portfolio ID and the investment
profile SHA256. Every included account must appear exactly once. Example account
mapping, using synthetic aliases:

```json
{
  "account_id": "broker",
  "provider": "etoro",
  "wrapper": "other",
  "denominator": true,
  "max_age_seconds": 86400,
  "last_reviewed_at": null,
  "review_interval_days": 30,
  "evidence": "Operator verified non-LISA investment account and selected cadence"
}
```

Use `wrapper: "LISA"` and `denominator: false` for an evidenced LISA, and false
for savings. Unknown wrapper/membership stays `"unknown"`/null; the template
deliberately leaves it unconfigured. Positive holdings are never classified from
provider or ticker. Profiles preserve `observed_at` and use bounded explicit UTC
clocks. Update the observation clock when editing evidence; future profiles are
refused. Accounts specify their own source-age and review intervals. A null
`last_reviewed_at` is never-reviewed, not a fictional date. The optional
`concentration_threshold_pct` is null until explicitly selected.

```powershell
python -m investment monitor --database .\investment.sqlite3 --monitoring-profile .\monitoring.json
python -m dashboard --investment-database .\investment.sqlite3 --monitoring-profile .\monitoring.json
```

Reporting never creates or repairs missing stores and never contacts a provider.
The API is `GET /api/investment/v1/monitoring`; POST is refused. Rebuild the
frontend after changing the monitoring schema. Invalid/unavailable data does not
fall back to demo or retain previous findings as current. Journal revision must
match the investment view.

## Separate observations

After confirming permitted retention, explicitly initialize a **new** cache:

```powershell
python -m investment monitor-init --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --cache .\monitoring.sqlite3 --retention-seconds 86400
```

New caches include a local random instance identifier and monotonic revision
counter. Purging expired attempts does not reset that counter. Older cache files
remain readable, but cannot be bound to the review inbox until a new cache is
explicitly initialized.

The cache is bound to the exact monitoring profile hash. Changing the profile
requires selecting a newly initialized cache; old evidence is not silently
rebound. The cache is separate from all accounting stores, bounded to 500
attempts/32 MiB, and supports transactional, idempotent batch publication.
It is a purgeable observation cache, not an immutable accounting journal.
No raw provider response or credential is stored.

`monitor-stage` accepts an operator-prepared normalized attempt JSON, **not**
CSV/PDF, a bank export or an arbitrary API payload:

```json
{
  "id": "synthetic-attempt-1",
  "account_id": "broker",
  "provider": "etoro",
  "attempted_at": "2026-11-17T12:00:00Z",
  "status": "success",
  "error_code": null,
  "observation": {
    "id": "synthetic-source-revision-1",
    "account_id": "broker",
    "provider": "etoro",
    "observed_at": "2026-11-17T11:00:00Z",
    "available_at": "2026-11-17T12:00:00Z",
    "currency": "GBP",
    "cash": "100",
    "holdings": [{"source_product": "synthetic-fund", "quantity": "10"}],
    "coverage": "partial",
    "evidence": "Synthetic operator-normalized observation, not provider verification"
  }
}
```

```powershell
python -m investment monitor-stage --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --cache .\monitoring.sqlite3 --attempt .\attempt.json
python -m investment monitor --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --cache .\monitoring.sqlite3
python -m dashboard --investment-database .\investment.sqlite3 --monitoring-profile .\monitoring.json --monitoring-cache .\monitoring.sqlite3
```

Collection time, last success, source as-of and coverage remain distinct.
Unknown source clocks are null, never replaced with receipt time. Freshness
uses the source clock and is stale at equality with the configured age limit.
Failed attempts keep the previous observation visibly dated and display a
failure finding. Later matching observations do not resolve journal cash
differences. Identical attempt retries are idempotent; conflicting IDs fail.

## Local monitoring review inbox

Monitoring findings can be tracked in a separate, local workflow store. This is
not the investment journal or observation cache. It requires an opened journal and
monitoring profile, is explicitly initialized at a new path, and must not share
the journal or cache path:

```powershell
python -m investment review-init --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --reviews .\monitoring-reviews.sqlite3
python -m investment review-sync --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --reviews .\monitoring-reviews.sqlite3
python -m investment review-inbox --reviews .\monitoring-reviews.sqlite3
python -m investment review-update --reviews .\monitoring-reviews.sqlite3 --case-id <case-id> --request-id <unique-action-id> --expected-revision <inbox-revision> --action acknowledged --note "Checked source date"
```

`review-sync` uses the current recorded monitoring report; add `--cache` when
that report uses the separately initialized observation cache. A review store is
bound to the exact portfolio and monitoring-profile hash. When initialized with
`--cache`, it is also bound to that resolved local cache path and cache-instance
ID; use the same cache on every sync. Replacing the cache at that path or
changing the profile requires a newly initialized review store. New monitoring
caches have an instance ID; older cache files without one can still be read by
monitoring but cannot be bound to a review inbox. Older journal revisions,
cache revisions or report clocks cannot replace a newer sync. Purging does not
reset a new cache's revision; replacing it creates a different instance and
requires a new review store.
Repeated syncs of unchanged evidence do not duplicate cases. Changed finding
evidence creates a new case and supersedes the earlier one; findings absent from
a later report become inactive, **not resolved**. A finding that returns after
becoming inactive gets a new case.

Actions are limited to `acknowledged`, `deferred` and `reviewed`. They are
revision-bound workflow records, not evidence that a discrepancy was corrected,
a finding was resolved, a trade was approved or a policy was satisfied. Use the
latest inbox revision when updating; `--request-id` makes an identical retry
idempotent. Local notes are limited to 500 characters; do not put credentials or
secrets in them.

The review store is bounded to 5,000 cases, 10,000 actions, 10,000 syncs and
16 MiB. It stores finding summaries, revisions and a hash of the source-evidence
text, not that source text or provider payload. Locally entered review notes are
retained in its append-only action history. Readers never initialize or repair
the store. An unavailable or invalid monitoring report cannot be synced; the
inbox does not update accounting, collect from a provider or suppress active
monitoring findings. Local file protection, backup and any note-retention needs
remain the operator's responsibility.

## Dashboard inbox and local workflow

The dashboard can read an explicitly selected review store without
authentication or mutation. Select the same existing `--monitoring-reviews`
store with the investment database and monitoring profile. The inbox displays
finding summaries, action status and source revisions; it does not sync on page
load. When the bound current report differs from the last sync, actions remain
unavailable until the operator explicitly enables workflow actions and syncs.

Actions use the separate `--monitoring-workflow` opt-in, independent of
`--investment-recording`; they cannot record journal events. There is no
password or user login, and browser request defenses are not authentication.
Same-origin and CSRF
checks, rate-limited short-lived sessions, bounded JSON, exact portfolio/profile/
cache-instance binding and revision-bound idempotent actions apply. Actions are
only acknowledged, deferred or reviewed; they never resolve a finding or approve
a trade.

Manual observation entry is available only with an explicitly initialized,
identified `--monitoring-cache`. The operator supplies normalized local fields
and explicit mapping evidence. The dashboard validates and previews a GBP
reconciliation against a pinned journal revision before a separate confirmation.
Confirmation appends the attempt only to the monitoring cache and requires the
same journal/profile/cache instance and cache revision. It makes no provider or
network call and never writes to the investment journal. Native-currency or
unknown-clock observations remain unreconciled; no FX, missing values or
accounting changes are inferred. The journal and cache are separate stores and
cannot be committed atomically; each action checks its pinned source revisions
immediately before writing, while a concurrent external journal update can
overlap that check. Abandoning a preview stages nothing.

Provider units are not automatically journal product IDs. GBP observations
reuse the existing conservative comparison rules: missing source clocks,
same-clock/later journal evidence, omitted holdings and unmapped products produce
comparison-unavailable findings; comparable cash/quantity differences remain
visible and do not repair accounting. Optional observation `product_mappings`
contains explicit `{source_product, product_id, evidence}` entries; absent
mappings are never guessed. Nonempty product mappings require their own
`mapping_available_at` UTC clock; later mapping evidence cannot support an
earlier source comparison. Unknown journal products produce explicit comparison-
unavailable findings rather than disabling the report. All retained observations
are compared; a newer match cannot clear an earlier retained difference.
For a standalone full-account preview use the existing
[reconciliation preview](portfolio-automation.md#offline-observation-staging-and-reconciliation),
which requires explicit mappings, coverage and causal clocks. Monitoring staging
does not automatically approve reconciliation, repair or append accounting events.

Expired attempts are hidden from reads but are not physically deleted by reads.
An explicitly approved maintenance command securely deletes expired cache rows:

```powershell
python -m investment monitor-purge --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --cache .\monitoring.sqlite3
```

Backups and exports remain the operator's responsibility under applicable rights.
A provider deletion/termination request may require deleting all relevant local
cache copies, not just waiting for expiry. Do not initialize/activate a cache if
the permitted data handling is unresolved. Purging never changes accounting.

## eToro Read adapter: engineering implemented, live unvalidated

The adapter implements one documented account snapshot GET:
`https://public-api.etoro.com/api/v1/trading/info/real/pnl`.
The main portal was access-blocked during development; the schema/conventions
were checked against eToro's official
[snapshot reference](https://github.com/etoro-builders/etoro-cursor-plugin/blob/a18e511a7226df46a9a7587c6ce92582c3592ab2/rules/etoro-account-snapshot.mdc)
and [API conventions](https://github.com/etoro-builders/etoro-cursor-plugin/blob/a18e511a7226df46a9a7587c6ce92582c3592ab2/rules/etoro-api-conventions.mdc).
Account-specific compatibility remains unverified.

The transport fixes host/path and GET, validates and pins public DNS addresses,
uses TLS hostname verification, bounded requests/response sizes, refuses
redirects and honors the configured proxy without direct fallback. Failures are
sanitized. No order/write functions, arbitrary URLs or automatic retries exist.
This route is a snapshot, not paginated transaction history. History/window
collection remains gated rather than inventing unsupported coverage.

Source account currency is USD; price currency, full account identity, product
classification and source as-of are unresolved. Collection therefore publishes
**partial native evidence only**: a source credit field and position count.
Credit is not asserted to be settled cash or buying power. Positions, mirrors,
orders, FX, GBP value and returns are not inferred or written to accounting.
No source clock means freshness remains unavailable even after a successful GET.
The raw response and private instrument/position identifiers are discarded.

Activation requires separate task-specific approval plus operator verification
of account eligibility, actual Read-only scope, account binding and permitted
retention. Merely making GET requests does not prove a key is read-only.
Resolve [provider gates](provider-feasibility.md) first; never request keys or
statements in chat or test permissions by attempting a trade.

The operator may create a nonsecret approval file, bound to the monitoring
profile hash and selected local account alias:

```json
{
  "profile_hash": "replace-with-monitoring-report-profile-hash",
  "account_id": "broker",
  "approved_at": "2026-11-17T12:00:00Z",
  "expires_at": "2026-11-17T13:00:00Z",
  "read_scope_verified": true,
  "eligibility_verified": true,
  "retention_rights_verified": true,
  "account_binding_verified": true,
  "supervised_read_approved": true,
  "evidence": "Operator-verified permissions, rights and explicit supervised-read approval"
}
```

Approval has a maximum one-day lifetime and is an operator attestation, not an
authenticated approval service or proof of permissions. The operator supplies
`GOLDSCOUT_ETORO_API_KEY` and `GOLDSCOUT_ETORO_USER_KEY` privately through their
environment, never command-line arguments, browser fields or committed files.
Only the explicitly invoked collector reads them.

```powershell
python -m investment monitor-collect --database .\investment.sqlite3 --monitoring-profile .\monitoring.json --cache .\monitoring.sqlite3 --account-id broker --approval .\read-approval.json --allow-network
```

Optional `--watch` waits one day between collections, never backfills missed
intervals, and stops on an error or expired approval. Given the short approval
lifetime it requires renewed operator approval before another day's read;
this is not unattended standing authority. Ctrl+C stops the foreground loop.
No OS task/service is installed and no collection process starts with the
dashboard. Concurrent publications serialize through SQLite; the in-process
transport permits only one active request and bounds unresolved DNS workers.

No live provider validation, custody verification, reconciliation approval,
profitability or execution readiness is established by offline tests.
