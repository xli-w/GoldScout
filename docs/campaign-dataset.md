# Offline GBP Campaign Dataset Importer

`python -m research_data import MANIFEST.json --dataset-dir DATA_DIR` validates
operator-supplied local files and writes a content-addressed snapshot under
`DATA_DIR/datasets/<sha256>/`. `python -m research_data validate DATASET_ID
--dataset-dir DATA_DIR` reopens the snapshot and verifies its original manifest
and every preserved source file. Both commands are local-only; they do not
instantiate a market-data client or access a network.

This is an independent offline data-ingestion contract, not part of the journal.
It provides no simulator or portfolio/accounting authority and has no broker or
network dependency. Dataset wire versions are independent of journal versions.
Successful import establishes only the checks documented below. It does not
close product eligibility, session/calendar, FX transaction-time, total-return,
cost, tax, cash-accounting, benchmark, or GBP campaign feasibility gates, and
does not authorize any campaign or backtest run.

## Input manifest (schema version 1)

The UTF-8 JSON root has exactly `schema_version`, `provenance`, `products`,
`fx`, `cash_rates`, and `corporate_actions`. Unknown/missing keys are rejected.
All source paths are safe CSV basenames relative to the manifest directory;
directories, traversal, symlinks, names that alias on case-insensitive
filesystems, and duplicate IDs are rejected. Files are retained byte-for-byte,
including the original manifest.
The SHA-256 identity covers the original manifest bytes and every referenced
CSV byte. Re-importing identical inputs reuses the same immutable dataset ID;
tampered source or generated metadata fails reopen validation.

Example (the files are illustrative only):

```json
{
  "schema_version": 1,
  "provenance": {
    "provider": "operator-supplied",
    "license_terms": "record the actual license/terms",
    "retrieved_at": "2025-02-01T12:00:00Z",
    "metadata": {"source_revision": "optional source details"}
  },
  "products": [{
    "id": "product-1",
    "identifier": "exact product identifier",
    "currency": "USD",
    "resolution": "daily",
    "timestamp_semantics": "period_end",
    "price_basis": "unadjusted_ohlcv",
    "file": "product.csv",
    "metadata": {"optional": "source metadata"}
  }],
  "fx": [{
    "id": "usd-gbp",
    "currency": "USD",
    "direction": "GBP_PER_UNIT",
    "resolution": "daily",
    "timestamp_semantics": "observation_time",
    "file": "usd_gbp.csv"
  }],
  "cash_rates": [{
    "id": "gbp-cash",
    "benchmark_identifier": "exact GBP cash benchmark",
    "currency": "GBP",
    "rate_unit": "annual_decimal",
    "resolution": "daily",
    "timestamp_semantics": "effective_time",
    "file": "cash.csv"
  }],
  "corporate_actions": [{
    "id": "action-1",
    "product_id": "product-1",
    "type": "cash_dividend",
    "currency": "USD",
    "effective_at": "2025-01-15T00:00:00Z",
    "available_at": "2025-01-10T12:00:00Z",
    "terms": {"amount": 0.25},
    "source_metadata": {"optional": "source details"}
  }]
}
```

`provenance.provider`, `license_terms`, and offset-aware `retrieved_at` are
required, as is at least one GBP cash-rate benchmark series. A series or action
may include its own `provenance` object with the same fields; otherwise the
root provenance applies. This allows different products, FX, cash, and action
sources to retain their own provider/license/retrieval record. Every row or
action's `available_at` must be no later than its applicable source
`retrieved_at`. Optional metadata is preserved without interpretation. Series
IDs are unique across products, FX, cash rates, and corporate actions. Currency
codes are three uppercase letters; FX must explicitly say `GBP_PER_UNIT`, and
exactly one FX series is allowed per foreign currency. FX coverage must span
the minimum and maximum observation timestamps of every non-GBP product.
This is only an envelope test: it does not fill gaps or prove conversion data
exists at transaction times.

Products support only `daily` and `weekly` OHLCV and must explicitly declare
`timestamp_semantics: period_end` and a `price_basis` of `unadjusted_ohlcv`,
`split_adjusted_ohlcv`, or `total_return_adjusted_ohlcv`. These declarations
are preserved, not independently verified. FX series must declare
`observation_time`; cash-rate series must declare `effective_time`, meaning
their row timestamp is when that annual rate becomes effective. Each CSV row
separately supplies an aware `available_at`. FX values are GBP received per
one unit of the stated foreign currency. Cash rates use annual decimal units
(for example, `0.04` is 4%). The importer never infers direction, period
convention, price basis, or availability.

CSV headers and order are exact:

```text
product:   timestamp,available_at,open,high,low,close,volume
FX:        timestamp,available_at,gbp_per_unit
cash rate: timestamp,available_at,annual_rate
```

Every timestamp must be RFC3339 with a valid explicit UTC offset; malformed
offsets are rejected on import and reopen. Series timestamps
must be strictly increasing and unique; ordering and availability comparisons
retain all supplied fractional digits after UTC normalization. Embedded
newlines inside timestamp fields and malformed/unterminated quoted records are
rejected; normal LF or
CRLF record endings are accepted. Numeric values must be finite; OHLC prices
and FX rates must be positive, volume non-negative, and high/low must bound
open and close. Product period-end and FX observation timestamps cannot follow
their `available_at`; cash rates may be published before their
effective timestamp. Every `available_at` must not follow the applicable
source's retrieval time. A cash-rate value must be greater than -1. The
importer does not assess exchange calendars, expected frequency, missing
intervals, declared price-basis accuracy, total-return treatment, or cash
accrual/day-count conventions.

Corporate actions require unique IDs, an imported product ID, matching
instrument currency, supported type (`split`, `cash_dividend`, or
`distribution`), aware `effective_at` and `available_at`, and finite terms.
Splits use positive `numerator` and `denominator`; dividends/distributions use
a positive `amount`. Actions must already be ordered by
`effective_at`, `available_at`, `product_id`, and `id`; duplicate same-product,
same-effective-time, same-type events are rejected. Availability may precede
the effective time (for an announced future event) or follow it (for a later
source record); both times are preserved and neither is inferred or rewritten.

Manifest size is limited to 2 MiB, each CSV to 64 MiB, all source files to 256
MiB, 256 total series, 10,000 actions, and 1,000,000 rows per series. The
generated dataset manifest includes per-file checksums, row counts, timestamp
envelopes, the untouched source payload metadata, and explicit unresolved
checks. Its first/last timestamp summaries retain at most microseconds, so
they are coarse metadata rather than exact sub-microsecond bounds; the original
source bytes and exact validation remain authoritative. Readiness separately
rejects nonzero sub-microsecond source precision. Reopen compares canonical
manifest values with their JSON types, so a
boolean cannot silently replace a numeric provenance value. File reads check
regular-file identity and byte limits after opening as well as before reading;
filesystems that cannot provide a stable file identity are rejected. These
checks do not make a hostile shared filesystem or the offline runner an OS
sandbox. These limits and validations are ingestion guards, not evidence that
a dataset is sufficient for research.

## What remains before research use

| Readiness question | Evidence or backend check still needed | What this importer establishes |
| --- | --- | --- |
| Product access and data rights | Verify the exact product/share class, UK investor and account eligibility, provider terms, and permitted research use. Preserve supporting evidence; the backend cannot establish legal or account eligibility. | Records declared provider and license terms only. |
| Calendar and history completeness | For each selected venue and resolution, compare bars with its expected sessions, holidays, early closes, and period boundaries; report missing, off-grid, and out-of-session rows without filling them. | Requires increasing timestamps and an explicit resolution, but does not check cadence, sessions, or gaps. |
| Price basis and corporate actions | Verify the declared basis and action source/completeness; reconcile split and distribution effects to returns and prevent double-adjustment. | Records the declared price basis and validates action shape/order, but does not verify either against source facts or adjust prices. |
| GBP conversion | Define causal transaction-time conversion, executable rates, spreads and an evidence-based maximum age; verify complete event clocks and historical source vintages. | Import checks only direction and observation envelope. The separate FX audit and view check declared availability and freshness at supplied clocks, but do not convert currency or verify executable rates. |
| Cash comparator | Verify the actual eligible GBP comparator and its terms. Define availability, effective periods, accrual/day-count, compounding, charges, and tax assumptions. | Imports effective-dated annual rates and availability timestamps; it does not accrue cash or validate comparator eligibility. |
| Continuous GBP portfolio | Specify one initial GBP cash/equity ledger, simultaneous holdings and risk limits, FX, actions, costs, and the benchmark clock. | No portfolio accounting or simulator is provided. |

These are separate gates, not a single “dataset valid” result. Source rights,
instrument/account access, and comparator eligibility require external evidence;
calendar, causal availability, and accounting rules need backend validation
once the products and sources are selected.

## Scoped FX publication and freshness audit

```powershell
python -m research_data fx-readiness DATASET_ID --dataset-dir .\campaign_datasets --profile .\fx-readiness.json
```

The equivalent Python function is
`research_data.readiness.audit_fx_readiness(dataset_dir, dataset_id, profile_path)`.
It reopens and checksum-validates an existing snapshot and examines one
declared GBP-per-unit FX series at explicitly supplied clocks. It does not
change the dataset, contact a provider, convert prices or calculate returns.
The profile is a separate, dataset-bound JSON file:

```json
{
  "schema_version": 1,
  "dataset_id": "REPLACE_WITH_EXACT_64_CHARACTER_DATASET_SHA256",
  "series_id": "usd-gbp-fx",
  "synthetic": true,
  "max_age_seconds": 86400,
  "clocks": [
    "2025-01-03T14:30:00Z",
    "2025-01-06T14:30:00Z"
  ]
}
```

All keys are required; unknown or duplicate keys, a different dataset ID, and
an unknown FX series are rejected. The profile must be a regular non-symlink
file outside the snapshot, at most 2 MiB. It must contain 1 to 50,000 strictly
increasing, unique UTC instants, expressed as offset-aware RFC3339 timestamps
without non-zero sub-microsecond precision. `max_age_seconds` is an explicit
non-negative integer, bounded by 18,300 days. The value above is illustrative,
not a recommended trading freshness threshold.

At each supplied clock, the audit selects the most recent **observation time**
whose declared availability is no later than that clock. A later observation
not yet available is ignored; a late publication of an older observation
cannot replace a newer available one. Age is measured from observation time,
not publication time, and the maximum is inclusive. A clock without an
available observation or with only a stale latest observation fails. No
interpolation, inversion, rates or price conversions are returned.

The bounded report includes dataset/profile/source hashes, declared source
provenance, FX currency/direction, clock count, issue counts, and up to five
failure examples and successful observation-time examples. A synthetic profile
remains `unresolved` even if its temporal checks pass; any missing or stale
clock yields `fail`. Repeated unchanged inputs produce the same report. The
snapshot remains byte-for-byte unchanged.

- Exit **0**: the non-synthetic declaration and the supplied-clock checks
  pass. This is only a scoped mechanical result.
- Exit **3**: any supplied clock fails or the profile is synthetic; inspect
  the JSON and stderr.
- Exit **2**: invalid profile/timestamps, corrupt snapshot or local I/O error;
  a diagnostic is emitted and no JSON report is returned.

The caller must independently establish that the clock list covers every
relevant transaction or decision, that availability and historical vintages
are truthful, and that source rights and product/account access are valid.
This audit cannot establish executable FX prices, bid/ask spreads, costs,
calendar completeness, currency accounting, or portfolio suitability. Its
verification flags remain false and no result authorizes data acquisition,
backtesting or trading. Develop and test with synthetic temporary fixtures.

## Complete FX observation view

```powershell
python -m research_data fx-view DATASET_ID --dataset-dir .\campaign_datasets --profile .\fx-readiness.json
```

`research_data.readiness.fx_observation_view(dataset_dir, dataset_id, profile_path)`
reuses the same dataset-bound FX profile and publication/freshness rules as the
audit. It returns **one observation for every supplied clock**, never a
sample or a shortened prefix. If any clock lacks a published observation or
its latest available observation is stale, the entire view is rejected.
Newer observations not yet published are ignored; late older publications
cannot displace newer observations already known.

The compact JSON contains the dataset/profile/source hashes, declared source
provenance and retrieval time, FX series/currency/direction, age limit,
`clock_count`, `readiness_status`, synthetic declaration and remaining gates.
Each ordered `observations` entry contains the UTC `clock`,
`observation_at`, `available_at`, and the exact parsed `gbp_per_unit` CSV field
as a string (excluding any CSV quoting). Provenance JSON decimals are also
preserved without binary-float rounding. Values are not rounded, inverted, interpolated or applied to any
prices, cash or positions. Source FX strings over 128 characters or values
that are not representable as finite, positive decimals reject the whole view.
The profile's 2 MiB / 50,000-clock bounds also apply.

- Exit **0**: a complete view was generated from a non-synthetic declaration;
  remaining campaign gates still apply.
- Exit **3**: a complete view was generated from synthetic inputs; the JSON
  and stderr explicitly report unresolved readiness.
- Exit **2**: a missing/stale observation, invalid value/profile, corrupt
  snapshot or local I/O error; stderr contains a diagnostic and stdout has
  no partial view.

The caller supplies the clocks; this tool does not verify that all relevant
transaction or decision clocks are included. Declared publication times do
not verify historical vintages or establish when a revision was truly known.
The snapshot may have been retrieved after a requested clock. Source rights,
product/account eligibility, executable FX prices, spreads and costs remain
unverified; the corresponding verification flags are always false. This view
does not convert currency, perform portfolio accounting, calculate performance,
or connect to a broker. A generated view authorizes neither a campaign run nor
trading. Keep output outside the immutable snapshot and use synthetic temporary
fixtures during development.

## Scoped GBP daily readiness audit

```powershell
python -m research_data readiness DATASET_ID --dataset-dir .\campaign_datasets --profile .\readiness.json
```

This read-only command reopens and checksum-validates the immutable snapshot,
then audits selected daily GBP series against a separate, dataset-bound profile.
It preserves the importer wire contract and is not connected to a simulator. It writes JSON to
stdout and diagnostics to stderr; save any report **outside** the snapshot.
Evidence references are recorded only, never opened or fetched.

Readiness profile schema version 1 has exactly `schema_version`, `dataset_id`,
`synthetic`, `products`, and `cash`. The illustrative profile below deliberately
leaves external evidence unresolved. Replace the dataset ID and product/cash
identities with those of a verified snapshot; do not treat these placeholders
as products or investment evidence.

```json
{
  "schema_version": 1,
  "dataset_id": "REPLACE_WITH_EXACT_64_CHARACTER_DATASET_SHA256",
  "synthetic": true,
  "products": [{
    "product_id": "gbp-product",
    "identifier": "TEST-GBP",
    "share_class": "synthetic fixture",
    "isin": "TEST-ONLY",
    "venue": "XLON",
    "calendar": "XLON",
    "timezone": "Europe/London",
    "currency": "GBP",
    "price_unit": "GBP_PER_UNIT",
    "price_basis": "unadjusted_ohlcv",
    "start_date": "2025-03-27",
    "end_date": "2025-03-31",
    "inception_date": "2020-01-01",
    "audit_clock": "next_session_open",
    "max_availability_delay_seconds": 3600,
    "evidence": {
      "product_access": {"status": "unresolved", "reference": "", "as_of": null},
      "license": {"status": "unresolved", "reference": "", "as_of": null},
      "normalization": {"status": "unresolved", "reference": "", "as_of": null},
      "price_basis": {"status": "unresolved", "reference": "", "as_of": null},
      "actions": {"status": "unresolved", "reference": "", "as_of": null}
    }
  }],
  "cash": {
    "series_id": "cash",
    "benchmark_identifier": "TEST-CASH",
    "rate_unit": "annual_decimal",
    "max_rate_age_seconds": 604800,
    "day_count": "unresolved",
    "compounding": "unresolved",
    "evidence": {
      "eligibility": {"status": "unresolved", "reference": "", "as_of": null},
      "terms": {"status": "unresolved", "reference": "", "as_of": null},
      "license": {"status": "unresolved", "reference": "", "as_of": null}
    }
  }
}
```

All shown keys are required; unknown/duplicate keys, invalid values, duplicate
selected product IDs, unknown series IDs and dataset-ID mismatches are rejected.
Product and cash identifiers, currency and declared price basis must match
their immutable source declarations. Share class, ISIN, inception and evidence
are operator declarations, not independently verified facts. A recorded
evidence item has `status: recorded`, a non-empty `reference` and an ISO
`as_of` date. An unresolved item has `status: unresolved`, a string reference
(possibly empty) and an ISO date or null. There is no status meaning that the
backend has verified legal/account eligibility.

The profile is a regular non-symlink local file outside the snapshot, at most
2 MiB, with 1 to 256 unique products. Dates use `YYYY-MM-DD`; ordered intervals
span at most 18,300 days. Delay/age values are explicit non-negative integer
seconds, capped at that span in seconds. These are engineering bounds, not
recommended freshness limits. Choose actual source/comparator rules from
evidence rather than adopting the illustrative values.

### Implemented checks and conservative boundaries

- Only explicitly declared XLON / Europe/London regular sessions, with
  `audit_clock: next_session_open`, are supported. Calendar range and the
  following executable open must be available in `exchange_calendars`;
  unsupported ranges fail rather than being extended with guessed holidays.
- Inclusive start/end **London calendar dates** define the complete requested
  interval. Each in-scope row timestamp must equal that session's UTC close,
  including holidays, early closes and DST. All expected sessions are required;
  requested edges are not trimmed. Rows outside the interval are counted
  separately and are not evidence of in-scope completeness.
- Every completed daily bar must be published within the declared delay **and**
  no later than its following session's open. Equality at the limit is allowed.
  This audits declared historical availability, not current live-feed freshness
  or the truth of the provider's availability claims.
- The readiness audit supports source timestamps to microsecond precision.
  Non-zero digits beyond six fractional places in encountered product/cash
  observation or availability timestamps, or their applicable retrieval
  timestamps, are rejected explicitly rather than rounded into an earlier
  session or decision. Invalid UTC offset components are rejected rather than
  normalized; extra trailing zeroes are allowed. The importer wire contract is unchanged.
- Only daily, GBP-denominated inputs explicitly documented as `GBP_PER_UNIT`
  (pounds) or `GBX_PER_UNIT` (pence) are supported. For pence, the declared
  conversion is 100 pence per GBP. The source manifest currency remains `GBP`:
  GBX is a quote unit, not an FX currency. A GBP currency label alone cannot
  prove the price unit; verify it and record the normalization evidence.
  Ambiguous labels such as `GBX`, and all other units, remain unsupported.
  The report shows a scale and at most two GBP-normalized OHLC examples only;
  the separate view described below is needed for a complete normalized series.
  Neither supplies execution prices to a simulator. GBP trading does not remove
  underlying FX exposure.
- Weekly-only execution, adjusted OHLC and any recorded action for a selected
  product anywhere in the snapshot are unsupported in this first slice.
  Unadjusted OHLC without recorded actions can pass the scoped shape check, but
  absent records do not prove action completeness. Dividend entitlement/payment,
  split reconciliation and executable versus adjusted features remain gated.
- Cash is checked at the expected following opens of all supported daily
  intervals. At each open, select the latest **effective** row, require that
  row to have been available, and enforce maximum age from its effective time.
  Do not fall back to an older rate if the latest effective row is unavailable,
  or use an announced future rate before it takes effect. Timestamp precision
  and UTC offsets are validated for the entire selected cash source, including
  rows beyond the last audited open, without applying those future rates.
  No daily accrual, compounding, weekend cash returns or savings performance is
  calculated.
- Evidence recording has its own check. Missing external evidence and
  `synthetic: true` remain unresolved even if every mechanical check passes.
  The synthetic flag is operator-declared; changing it does not verify data.

### Report and exit status

JSON report schema version 1 identifies scope `gbp_daily_data_readiness`, the
dataset/profile SHA-256 hashes, synthetic declaration, calendar package version
and coverage, product intervals/units/row counts, cash audit-clock count, check
counts, and external evidence. Each supported product reports
`price_scale_to_gbp` (`"1"` for pounds or `"0.01"` for pence) and up to two
`normalized_price_examples` from the first and last in-scope session-close
rows. OHLC prices are exact decimal strings; timestamps/availability denote
the same instants, and volume is unchanged. Examples illustrate the declared
unit conversion, not executable or independently verified prices. A numeric
field exceeding 128 characters causes its entire example to be omitted rather
than rounded or truncated; `omitted_normalized_examples` records the count.
Unsupported units have a null scale and no examples. Source snapshots remain
byte-for-byte unchanged.

Each check contains `subject`, `name`, `status`, `reason`, `issues`, and at most
five actionable `examples`. Status is `pass`,
`fail`, `unsupported`, or `unresolved`; the aggregate scoped status prioritizes
fail, then unsupported, then unresolved. Repeating unchanged inputs under the
same calendar coverage/version produces the same report.

- Exit **0**: all required scoped checks pass and evidence declarations are
  recorded. This is not campaign readiness or independent verification.
- Exit **3**: required scoped checks fail, are unsupported or remain unresolved.
- Exit **2**: malformed arguments/profile, corrupt snapshot or local I/O error;
  a diagnostic is emitted, not a success-shaped JSON report.

Every report includes `remaining_campaign_gates`: external source/product facts,
action reconciliation, cash accrual, costs/tax, GBP portfolio accounting,
weekly features, simulation/selection and autonomous broker recovery. No audit
result authorizes acquisition, a performance run, account access or orders.
Existing `import` and `validate` exit behavior is unchanged.

## Complete GBP-normalized daily view

```powershell
python -m research_data normalize DATASET_ID PRODUCT_ID --dataset-dir .\campaign_datasets --profile .\readiness.json --as-of 2025-03-31T16:00:00Z
```

The equivalent Python function is
`research_data.readiness.normalized_product_view(dataset_dir, dataset_id, profile_path, product_id, as_of)`.
It reopens and checksum-validates the dataset, binds the profile to that dataset,
and runs the same whole-profile readiness audit. Any failing or unsupported
check, including checks for other selected products or cash, rejects the view.
Unresolved evidence and synthetic declarations remain visible rather than being
treated as verified.

The selected product's **entire** declared interval is returned, never a
silently clipped prefix. `--as-of` is required and must be an offset-aware
RFC3339 timestamp supported to microsecond precision. Every selected bar's
declared `available_at` must be at or before that cutoff; if even one is later,
no view is returned. The cutoff checks declarations in the input; it does not
prove that a later-retrieved or revised source had the same values historically.
It does not constrain the source retrieval time, evidence dates or cash audit
clocks, so `readiness_status` is not an as-of verdict.
Before any strategy or simulator uses bars, it must independently establish
point-in-time vintage and filter information at **each decision time**. A full
view as of a later cutoff is not a causal historical backtest.

The JSON contains dataset/profile and source SHA-256 values, source provenance
and retrieval time, calendar version/coverage, selected product and interval,
declared source units, GBP output units, cutoff, synthetic declaration,
`readiness_status`, remaining gates, `bar_count`, and the ordered `bars`.
Each bar preserves its timestamp and availability as UTC instants, unchanged
volume, and exact decimal-string OHLC prices: `GBP_PER_UNIT` is unchanged and
`GBX_PER_UNIT` is divided by 100. Exact OHLC relationships and non-negative
volume are checked again without float rounding. Source OHLC/volume fields over
128 characters, unrepresentable decimals, or inconsistent values reject the
whole view instead of omitting or truncating data. The profile's 18,300-day
interval bound also bounds the returned rows. The immutable snapshot is not
modified.

`external_facts_verified`, `point_in_time_vintage_verified`, and
`supports_performance` are always false. Declarations do not establish product
eligibility, licensed data, action completeness, a cash comparator, costs, or
historical data vintages. This view has no cash accrual, valuation, returns,
corporate-action processing, FX, strategy, execution or broker integration.
Do not treat it as a normalized executable dataset or as an authorization to
acquire data, run a campaign or trade.

- Exit **0**: the complete view was generated and scoped readiness declarations
  pass; the remaining campaign gates still apply.
- Exit **3**: the complete engineering view was generated, but scoped readiness
  remains unresolved; inspect its metadata and stderr.
- Exit **2**: invalid/corrupt input, a failing or unsupported readiness check,
  unavailable bars, or unsupported numeric values; a diagnostic is emitted and
  no JSON view is returned.

The command writes compact JSON to stdout and diagnostics to stderr. Save output only
outside the snapshot. Test and develop with synthetic temporary fixtures.

## Synthetic GBP fill-accounting contract

The pure helper `research_data.account_fills(initial_cash_gbp, fills)` is
an offline engineering contract only. It accepts a positive initial GBP cash
amount and an already ordered `list`/`tuple` of synthetic fill events. It uses
exact decimal strings, explicit `GBP` / `GBP_PER_UNIT`, a caller-supplied
sequence for simultaneous timestamps, and a single cash balance shared across
all product IDs.

Each fill has exactly:

```json
{
  "event_id": "fill-1",
  "timestamp": "2025-03-27T09:00:00Z",
  "sequence": 0,
  "product_id": "synthetic-gbp-product",
  "side": "buy",
  "quantity": "2",
  "price_gbp": "12.34",
  "fees_gbp": "0.10",
  "currency": "GBP",
  "price_unit": "GBP_PER_UNIT"
}
```

Timestamps must be offset-aware RFC3339 with no non-zero sub-microsecond
precision. Events must already be ordered by UTC timestamp and then by unique
non-negative integer sequence; the helper never sorts them. Event IDs must be
unique. Numeric amounts must be non-exponent, non-negative decimal strings
(quantity and price strictly positive), with at most 64 digits. No implicit
floating-point conversion, product-specific quantity rounding, FX conversion
or price-unit conversion occurs.

The helper is bounded to 100,000 fills and uses enough isolated decimal
precision for the declared input digit/exponent and event-count limits; its
results are independent of the caller's Decimal context. These fixed parser
bounds are not recommended broker quantity limits.

Buys debit exact notional plus fees and fail if available cash is insufficient.
Sells fail if they exceed the current long quantity or if fees would make cash
negative. Fees are charged on each fill. Positions and cash are conserved and
the result reports an ordered event ledger, final cash/quantities, total fees
and gross traded notional. The caller owns evidence that these synthetic
normalized fills and fees are correct.

This helper deliberately does **not** calculate portfolio equity or returns:
it has no market marks, cash accrual, distributions, corporate actions,
position valuation, lots/realized P&L, product increments/minimums, risk sizing,
execution simulation, or persistence. It cannot consume readiness examples,
campaign datasets, live orders, or broker responses. Its output explicitly
marks equity/performance claims unsupported. Use synthetic fixtures only; do
not use the helper as a backtest or a broker execution ledger.
