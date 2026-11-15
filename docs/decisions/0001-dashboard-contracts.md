# Decision: Read-Only Dashboard Contracts

Status: accepted

Date: 2026-11-13

This decision records the original contract milestone and subsequent additions.
References to planned endpoints or absent implementations describe that stage,
not the current dashboard; use the [dashboard guide](../dashboard.md) for current
capabilities and limitations.

## Context

The first dashboard implementation step defines contracts and synthetic fixtures,
not an HTTP server, database reader, telemetry publisher, or trading controls.
The [ledger](../../bot/ledger.py) is the accounting authority. Its initializer
performs writes, so a future dashboard reader must not instantiate it. The
[portfolio snapshot](../../bot/portfolio.py) includes positions and daily controls,
but does not provide per-position protection observation times or comprehensive
runtime health. No existing snapshot alone proves that the bot is running.

## Decision

Use [JSON Schema Draft 2020-12](../../dashboard/response.schema.json) as the
framework-neutral v1 response contract. This keeps frontend/API development
independent of FastAPI installation and credential-loading bot imports.
[Fixtures](../../dashboard/fixtures.json) are entirely synthetic and never read
operational state. The only Python behavior introduced is the pure
[freshness function](../../dashboard/contracts.py).

### Envelope and Resources

Every response has `api_version`, `resource`, `meta`, and `data`. Metadata includes
UTC generation time, environment from verified account binding (otherwise
`unknown`), ledger revision, telemetry run ID, and separate ledger, telemetry,
and broker source observation times. Never use database read time as broker
observation time or infer environment from a URL or fixture default.

| Planned GET endpoint | Resource | Contents |
| --- | --- | --- |
| `/api/v1/overview` | `overview` | Recorded equity, realized P&L, last observed entry restrictions |
| `/api/v1/positions` | `positions` | Recorded quantities, stops, protection observations, pending exits |
| `/api/v1/orders?cursor=...&limit=...` | `orders` | Order intents/observations, cumulative fills, opaque pagination |
| `/api/v1/events?after=...&limit=...` | `events` | Ascending journal sequence, allowlisted summaries, continuation |
| `/api/v1/performance?from=...&to=...&cursor=...&limit=...` | `performance` | Daily projections, original exit slices, separate corrections |
| `/api/v1/health` | `health` | Runtime, monitor completion, faults, accounting/risk blocks, sync coverage |

Collection requests default to 100 rows and reject limits outside 1..1000.
Performance dates are inclusive New York accounting dates, not UTC fill dates;
independent collection cursors must be encoded together in its opaque cursor.
For events, `next_after` is the last returned sequence (or the requested `after`
on an empty page); `has_more` indicates additional records. A ledger reset or
revision mismatch invalidates cursors rather than silently mixing histories.
These collection query and cursor rules are requirements for later history/activity
implementation, not the step-2 overview/positions slice.

Step 4 implements these collection endpoints. Cursor binding additionally uses
the entire event-journal head, because order observations and prior-day
corrections can change projections without advancing the recovery-state revision.
Events also expose an optional continuation cursor while retaining `next_after`.
Unknown legacy exit position IDs/accounting dates/quantities and order quantities
are nullable. Optional daily `accounting_complete` identifies unresolved totals.

`error` uses the same envelope with sanitized codes/messages. Missing storage is
not a successful empty account. Proposed HTTP statuses: 503 for missing/unavailable
sources, 409 for invalid/unsupported ledger state, and 400 for invalid requests.
Error output must not include paths, SQL, raw exception text, or credentials.
The API must project allowlisted fields rather than forward raw journal payloads.

### Freshness and Uncertainty

- All wire timestamps are RFC 3339 UTC ending in `Z`; absent facts are `null`.
- `fresh` means age is between zero and the configured maximum, inclusive.
  Older observations are `stale`; future/malformed times are `invalid`;
  absent observations are `missing`. Invalid source validation also overrides
  time freshness. The fixture thresholds (90 seconds telemetry/broker and
  600 seconds ledger) are dashboard defaults, not protection latency guarantees.
- Ledger freshness describes the age of the last recorded commit, not process
  liveness. A quiet ledger may legitimately be stale while telemetry is fresh.
- `running` requires fresh telemetry for the current run and a fresh completed
  monitor observation. No lock-file existence or ledger timestamp proves this.
  Stale/missing telemetry becomes `unknown`; stale shutdown records do not prove
  that a new process has not started. Run mismatch is also `unknown`.
- A fresh persistence fault is `faulted`; loss of telemetry later becomes
  `unknown`, with the last fault retained as a historical warning. A false
  fault flag from an old run must not clear a current warning.
- `last_observed_allowed` is descriptive, never authorization to trade. It
  requires fresh current-run telemetry and fresh monitor completion, and no
  known blocks. Otherwise report `blocked` for known blocks or `unknown`.
- `last_verified` protection requires its own observation time and broker stop
  price. Always display its age. Without that evidence, report `unknown` or
  `unverified`; never manufacture timestamps from a snapshot revision. Current
  bot snapshots lack this timestamp, so a step-2 reader cannot claim verification
  until an evidence source or step-3 telemetry provides it.
- Current price/unrealized P&L remain unavailable until timestamped observations
  exist. Browser polling never triggers extra broker requests.

### Accounting

All quantities, money, and prices are finite base-10 strings, without exponent
notation. IDs, revisions, and journal sequences retain their distinct types.
Future adapters must preserve original decimal strings and explicitly serialize
existing finite float fields; decimal strings do not upgrade historical precision.
Unknown exit prices, gross P&L, and net P&L are null, never estimated at stops.
Legacy unknown fee provenance is null, not falsely settled. Fees marked estimated
stay marked estimated even when the fill price is verified.

An executed exit is a slice, not necessarily a completed position. Fixtures link
exit/order/position IDs and conserve the original quantity across the slice and
residual. Cumulative order fills are observations, not individual executions.
Corrections retain evidence IDs and supplied total allocated fees separately from
immutable original exits. Daily realized projections can include corrections;
clients must not add them again. Historical missing equity remains null.
Activity sync is labelled bounded overlap, never full settled-account coverage.

### Fixture Use

`fixtures.json` contains shared `meta` and named scenarios. Expand each resource
into an envelope by copying shared metadata, replacing top-level fields from
`meta_overrides`, and copying its resource data. Scenarios with fewer resources
are focused cases, not implicit successful responses for omitted endpoints.
They cover healthy observations, partial exits, unknown prices, corrections,
estimated crypto fees, faults/halts/unmanaged positions/incomplete sync, stale
telemetry, explicitly stopped empty holdings, missing storage, and invalid storage.
Tests construct independent copies to prevent mutation leaking between scenarios.

## Consequences

No bot behavior, operational files, credentials, trading dependencies, or safety
hooks change. Schema shape validation cannot enforce quantity arithmetic, current
run identity, or timestamp age; fixtures and focused tests cover those contracts
now, and API integration tests must enforce them in steps 2 and 3. Runtime status
and per-position protection times are new telemetry requirements, not existing
broker facts. Remote access, controls, research execution, and maintenance remain
outside this release. Browser loading/error/rendering tests belong to the UI step.

Schema tests require the optional pinned dashboard development dependencies,
including the RFC 3339 format validator. Without `jsonschema`, schema-only tests
are explicitly skipped; dependency-free fixture/freshness tests still run. The
current trading-only CI does not certify the schemas. Adding dashboard packages
to CI needs separate approval; no CI or safety-policy edits are made here.

```powershell
python -m pip install -r requirements-dashboard-dev.txt
python scripts/offline_tests.py --pattern test_dashboard.py
```

## Verification

Acceptance: every synthetic resource validates; invalid dates, numeric/nonfinite
money, extra fields, invented unknown prices, and timestamp-free verification are
rejected. Fixed-clock freshness tests cover exact expiry, future observations,
missing/malformed timestamps, timezone conversion, and invalid thresholds.
Fixture checks conserve partial-exit quantities and preserve corrected originals.
No server or authenticated broker validation is part of this step.

Executed locally with Python 3.11.9 and the dashboard development dependencies:
the focused offline contract suite validates all resources and rejection cases.
Exact final results are reported in the implementation handoff.

## Step 2 Additions

The [implementation guide](../dashboard.md) records the read-only API and initial
screens. Metadata now requires `source_mode` and `database_id`: synthetic labels
travel atomically with balances, and requests pair database identity with revision
instead of revision alone. Positions require `exit_pending`, covering durable
client-ID-only exits as well as broker-ID-known exits. These are pre-release
contract changes; regenerate the standalone frontend validator when building.

## Step 5 Packaging

The frontend build produces a schema/asset checksum manifest and copies only
generated output into `dashboard/static`. The dashboard-only Python wheel ships
those assets with the API/contracts and exposes `trader-dashboard`; it includes
no trading package or operational files. Production CLI launch validates the
bundle; explicit `--api-only` mode supports Vite/API development without assets.
Build/installation is operator initiated, never performed implicitly at launch.
The existing process-lock, accounting, broker, and credential boundaries remain
unchanged. See the [dashboard guide](../dashboard.md) for exact setup commands.