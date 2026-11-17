# Decision: Local evidence dashboard

Status: original trading projections retired; current portfolio boundary retained

Dates: original 2026-11-13; retirement 2026-11-17

The original dashboard exposed read-only trading-ledger projections. Those
routes, schemas, fixtures, telemetry and views are retired, not compatibility
interfaces. The current [dashboard](../dashboard.md) presents one opening-evidence
GBP journal plus separately gated monitoring and advisory intelligence.

Retain the useful contracts: readers never initialize/repair missing stores;
source clocks differ from read clocks; missing evidence is explicit; only
allowlisted data reaches responses; errors do not leak paths, SQL or secrets;
synthetic demos use isolated temporary storage.

Setup and manual recording have separate opt-ins, same-origin/CSRF, bound
previews and exact revision checks. They grant no trade authority. Loopback
binding is mandatory; browser defenses are not user authentication.
Stable technical API/schema numbers are not separate products.
