import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ArrowDown, CircleHelp, ListOrdered, ReceiptText, RefreshCw, Search, ShieldAlert } from "lucide-react";
import Decimal from "decimal.js";
import { DashboardError, get, label, money as formatMoney, quantity, timestamp } from "./api";

const money = (value: string | null) => formatMoney(value, true);

type Daily = { day: string; recorded_equity: string | null; realized_pnl: string | null; includes_corrections: boolean; accounting_complete?: boolean };
type Exit = { execution_id: string; symbol: string; quantity: string | null; accounting_day: string | null; exit_price: string | null; price_verified: boolean; fees: string | null; fees_estimated: boolean | null; net_pnl: string | null };
type Correction = { correction_id: string; execution_id: string; activity_id: string; exit_price: string; fees: string; reason: string };
type Performance = { daily: Daily[]; exits: Exit[]; corrections: Correction[]; next_cursor: string | null };
type Event = { sequence: number; kind: string; observed_at: string; symbol: string | null; summary: string };
type Order = { client_id: string; broker_order_id: string | null; symbol: string; side: string; status: string; requested_quantity: string | null; cumulative_filled_quantity: string | null };
type ActivityPage = { items: (Event | Order)[]; next_cursor?: string | null };

function useCollection<Value extends { next_cursor?: string | null }>(resource: string, filters: string, databaseId: string | null) {
  return useInfiniteQuery({
    queryKey: ["collection", resource, filters, databaseId], initialPageParam: null as string | null,
    queryFn: async ({ pageParam, signal }) => {
      const response = await get<Value>(`/api/v1/${resource}?limit=100${filters}${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`, resource, signal);
      if (response.meta.database_id !== databaseId) throw new DashboardError("snapshot_changed", "The history source changed. Reload the account snapshot.");
      return response;
    },
    getNextPageParam: page => page.data.next_cursor || undefined,
    refetchOnWindowFocus: false, staleTime: Infinity, retry: false,
  });
}

function Status({ pending, failed, error, reload }: { pending: boolean; failed: boolean; error: Error | null; reload: () => void }) {
  if (pending) return <div className="loading" role="status"><RefreshCw className="spin" size={20} />Loading records</div>;
  if (failed) return <div className="error-state" role="alert"><ShieldAlert size={28} /><h2>{error instanceof DashboardError && error.code === "snapshot_changed" ? "History snapshot changed" : "Records unavailable"}</h2><p>{error?.message}</p><button onClick={reload}><RefreshCw size={15} />Reload snapshot</button></div>;
  return null;
}
function Pages({ more, busy, load }: { more: boolean; busy: boolean; load: () => void }) {
  return <nav className="pagination" aria-label="History pagination"><span role="status">{more ? "More portfolio totals, exits, and corrections are available" : "End of loaded history"}</span>{more && <button type="button" aria-busy={busy} disabled={busy} onClick={load}><ArrowDown size={15} />{busy ? "Loading history" : "Load more history"}</button>}</nav>;
}
function Identifier({ value }: { value: string }) { return <span className="record-id" title={value}>{value}</span>; }

function DailyChart({ daily }: { daily: Daily[] }) {
  const rows = [...daily].sort((left, right) => left.day.localeCompare(right.day)).slice(-60);
  const known = rows.filter(row => row.realized_pnl !== null);
  if (!known.length) return <div className="chart-empty"><CircleHelp size={24} /><span>No complete daily P&amp;L records</span></div>;
  const magnitude = known.reduce((value, row) => Decimal.max(value, new Decimal(row.realized_pnl!).abs()), new Decimal(1));
  const first = Date.parse(rows[0].day + "T00:00:00Z"), last = Date.parse(rows[rows.length - 1].day + "T00:00:00Z");
  const span = Math.max(last - first, 86400000), width = Math.min(38, 640 / Math.max(1, span / 86400000 + 1));
  return <figure className="history-chart"><figcaption>Portfolio daily realized P&amp;L <span>Most recent {rows.length} loaded days</span></figcaption><svg viewBox="0 0 800 220" role="img" aria-label="Recorded daily portfolio P&L, missing observations are gaps">
    <line x1="55" x2="770" y1="104" y2="104" className="chart-baseline" /><text x="8" y="108" className="chart-label">$0</text>
    {rows.map(row => {
      if (row.realized_pnl === null) return null;
      const value = new Decimal(row.realized_pnl);
      const x = 70 + (Date.parse(row.day + "T00:00:00Z") - first) / span * 660;
      const title = `${row.day}: ${money(row.realized_pnl)}${row.includes_corrections ? " (includes corrections)" : ""}`;
      if (value.isZero()) return <circle key={row.day} cx={x} cy={104} r={2} className="chart-zero"><title>{title}</title></circle>;
      const height = Math.max(1, value.abs().div(magnitude).mul(78).toNumber());
      return <rect key={row.day} x={x - width / 2} y={value.isNegative() ? 104 : 104 - height} width={width} height={height} className={value.isNegative() ? "chart-negative" : "chart-positive"}><title>{title}</title></rect>;
    })}
    <text x="55" y="210" className="chart-label">{rows[0].day}</text><text x="770" y="210" textAnchor="end" className="chart-label">{rows[rows.length - 1].day}</text>
  </svg></figure>;
}

export function HistoryView({ databaseId }: { databaseId: string | null }) {
  const [params, setParams] = useSearchParams(); const client = useQueryClient();
  const from = params.get("from") || "", to = params.get("to") || "", symbol = params.get("symbol") || "";
  const activeFilterCount = Number(Boolean(from)) + Number(Boolean(to)) + Number(Boolean(symbol));
  const filters = `${from ? `&from=${encodeURIComponent(from)}` : ""}${to ? `&to=${encodeURIComponent(to)}` : ""}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`;
  const query = useCollection<Performance>("performance", filters, databaseId);
  const reload = () => void client.resetQueries({ queryKey: ["collection", "performance", filters, databaseId], exact: true });
  const update = (key: string, value: string) => { const next = new URLSearchParams(params); value ? next.set(key, value) : next.delete(key); setParams(next, { replace: true }); };
  const clearFilters = () => {
    const next = new URLSearchParams(params);
    ["from", "to", "symbol"].forEach(key => next.delete(key));
    setParams(next, { replace: true });
  };
  const pages = query.data?.pages || [], daily = pages.flatMap(page => page.data.daily), exits = pages.flatMap(page => page.data.exits), corrections = pages.flatMap(page => page.data.corrections);
  return <section>
    <fieldset className="history-filters">
      <legend>History filters</legend>
      <div className="record-toolbar"><div className="date-filter"><label>From (New York)<input type="date" aria-label="From accounting date (New York)" value={from} max={to || undefined} onChange={event => update("from", event.target.value)} /></label><label>To (New York)<input type="date" aria-label="To accounting date (New York)" value={to} min={from || undefined} onChange={event => update("to", event.target.value)} /></label></div><div className="search-box"><Search size={15} /><input aria-label="Filter exit symbol" aria-describedby="history-symbol-scope" placeholder="All exit symbols" value={symbol} onChange={event => update("symbol", event.target.value.toUpperCase())} /></div>{activeFilterCount > 0 && <button className="clear-filters" type="button" onClick={clearFilters}>Clear filters</button>}<span className="history-filter-status" aria-live="polite">{activeFilterCount ? `${activeFilterCount} active ${activeFilterCount === 1 ? "filter" : "filters"}` : "All history"}</span><button className="icon-button refresh" title="Reload history snapshot" aria-label="Reload history snapshot" onClick={reload}><RefreshCw size={17} /></button></div>
    </fieldset>
    <p className="subtle" id="history-symbol-scope">Symbol filters exit slices and their corrections, not portfolio totals.</p>
    <Status pending={query.isPending} failed={query.isError} error={query.error} reload={reload} />
    {!query.isPending && !query.isError && <><p className="subtle">History snapshot read {timestamp(pages[0]?.meta.generated_at || null)}. Accounting dates use New York time; timestamps are shown in UTC.</p>
      <nav className="history-sections" aria-label="History sections">
        <a href="#history-totals">Portfolio totals</a>
        <a href="#history-exits">Exit details</a>
      </nav>
      <Pages more={query.hasNextPage} busy={query.isFetchingNextPage} load={() => void query.fetchNextPage()} />
      <section id="history-totals" aria-labelledby="history-totals-heading" className="history-group">
      <div className="section-heading"><h2 id="history-totals-heading">Portfolio totals</h2><span className="subtle">All instruments</span></div>
      <DailyChart daily={daily} />
      <div className="section-heading"><h3>Daily accounting</h3></div>
      <div className="table-scroll" tabIndex={0} aria-label="Daily accounting table" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Accounting day (New York)</th><th>Recorded closing equity</th><th>Realized P&amp;L</th><th>Accounting</th></tr></thead><tbody>{daily.map(row => <tr key={row.day}><td>{row.day}</td><td className="numeric">{money(row.recorded_equity)}</td><td className="numeric">{money(row.realized_pnl)}</td><td>{row.accounting_complete === false ? "Unresolved" : row.includes_corrections ? "Includes corrections" : "Original projection"}</td></tr>)}</tbody></table>{!daily.length && <p className="record-empty">No daily records in this range</p>}</div>
      </section>
      <section id="history-exits" aria-labelledby="history-exits-heading" className="history-group">
      <div className="section-heading"><h2 id="history-exits-heading">Exit details</h2><span className="subtle">Original records and separate corrections</span></div>
      <div className="section-heading history-heading"><h3>Original exit slices</h3><span className="subtle">{exits.length} loaded</span></div>
      <div className="table-scroll" tabIndex={0} aria-label="Original exit slices" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Symbol / execution</th><th>Accounting day (New York)</th><th>Quantity</th><th>Exit price</th><th>Fees</th><th>Original net P&amp;L</th></tr></thead><tbody>{exits.map(row => <tr key={row.execution_id}><td><strong>{row.symbol}</strong><Identifier value={row.execution_id} /></td><td>{row.accounting_day || "Unknown"}</td><td className="numeric">{quantity(row.quantity)}</td><td className="numeric">{money(row.exit_price)}{!row.price_verified && <small className="pending">Price unknown</small>}</td><td className="numeric">{money(row.fees)}<small className="subtle">{row.fees_estimated === null ? "Provenance unknown" : row.fees_estimated ? "Estimated" : "Reported"}</small></td><td className="numeric">{money(row.net_pnl)}</td></tr>)}</tbody></table>{!exits.length && <p className="record-empty">No exit slices in this range</p>}</div>
      <div className="section-heading history-heading"><h3>Evidence-linked corrections</h3><span className="subtle">Separate from originals</span></div>
      <div className="table-scroll" tabIndex={0} aria-label="Accounting corrections" aria-describedby="table-scroll-instructions"><table><thead><tr><th>Correction / execution</th><th>Evidence</th><th>Corrected exit price</th><th>Total allocated fees</th><th>Reason</th></tr></thead><tbody>{corrections.map(row => <tr key={row.correction_id}><td><Identifier value={row.correction_id} /><Identifier value={row.execution_id} /></td><td><Identifier value={row.activity_id} /></td><td className="numeric">{money(row.exit_price)}</td><td className="numeric">{money(row.fees)}</td><td className="correction-reason">{row.reason}</td></tr>)}</tbody></table>{!corrections.length && <p className="record-empty">No corrections in this range</p>}</div>
      </section>
      <Pages more={query.hasNextPage} busy={query.isFetchingNextPage} load={() => void query.fetchNextPage()} />
    </>}
  </section>;
}

export function ActivityView({ databaseId }: { databaseId: string | null }) {
  const [params, setParams] = useSearchParams(); const tab = params.get("view") === "orders" ? "orders" : "events"; const client = useQueryClient();
  const query = useCollection<ActivityPage>(tab, "", databaseId), reload = () => void client.resetQueries({ queryKey: ["collection", tab, "", databaseId], exact: true });
  const rows = query.data?.pages.flatMap(page => page.data.items) || [];
  return <section><div className="record-toolbar"><div className="segmented" role="group" aria-label="Activity view"><button type="button" aria-pressed={tab === "events"} onClick={() => { const next = new URLSearchParams(params); next.set("view", "events"); setParams(next); }}><ListOrdered size={15} />Journal</button><button type="button" aria-pressed={tab === "orders"} onClick={() => { const next = new URLSearchParams(params); next.set("view", "orders"); setParams(next); }}><ReceiptText size={15} />Orders</button></div><span className="subtle">{rows.length} loaded records</span><button className="icon-button refresh" title="Reload activity snapshot" aria-label="Reload activity snapshot" onClick={reload}><RefreshCw size={17} /></button></div>
    <Status pending={query.isPending} failed={query.isError} error={query.error} reload={reload} />
    {!query.isPending && !query.isError && <><p className="subtle">Activity snapshot read {timestamp(query.data?.pages[0]?.meta.generated_at || null)}</p><div className="table-scroll" tabIndex={0} aria-label={tab === "events" ? "Event journal" : "Order observations"} aria-describedby="table-scroll-instructions"><table><thead><tr>{(tab === "events" ? ["Sequence", "Observed UTC", "Event", "Symbol", "Summary"] : ["Client / broker order", "Symbol", "Side", "Status", "Requested quantity", "Cumulative filled"]).map(name => <th key={name}>{name}</th>)}</tr></thead><tbody>{rows.map(row => "sequence" in row ? <tr key={row.sequence}><td className="numeric">{row.sequence}</td><td>{timestamp(row.observed_at)}</td><td>{label(row.kind)}</td><td>{row.symbol || "Unavailable"}</td><td>{row.summary}</td></tr> : <tr key={row.client_id}><td><Identifier value={row.client_id} />{row.broker_order_id && <Identifier value={row.broker_order_id} />}</td><td>{row.symbol}</td><td>{label(row.side)}</td><td>{label(row.status)}</td><td className="numeric">{row.requested_quantity === null ? "Unavailable" : quantity(row.requested_quantity)}</td><td className="numeric">{row.cumulative_filled_quantity === null ? "Unknown" : quantity(row.cumulative_filled_quantity)}</td></tr>)}</tbody></table>{!rows.length && <p className="record-empty">No recorded {tab === "events" ? "events" : "orders"}</p>}</div><Pages more={query.hasNextPage} busy={query.isFetchingNextPage} load={() => void query.fetchNextPage()} /></>}
  </section>;
}