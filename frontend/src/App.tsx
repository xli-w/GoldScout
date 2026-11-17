import { useDeferredValue, useEffect, useState } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, NavLink, useLocation, useSearchParams } from "react-router-dom";
import { Activity, ArrowDown, ArrowUp, ArrowUpRight, CircleHelp, Database, LayoutDashboard, ListOrdered, LockKeyhole, RefreshCw, Search, ShieldAlert, ShieldCheck, Wallet, X } from "lucide-react";
import Decimal from "decimal.js";
import { DashboardError, label, loadDashboard, money, price, quantity, runtimeStatus, sourceFreshness, timestamp, type Health, type Meta, type Position } from "./api";
import { ActivityView, HistoryView } from "./HistoryViews";
import { IntelligenceView, useIntelligence } from "./IntelligenceView";
import { TradingGuide } from "./TradingGuide";
import { InvestmentGuide, InvestmentView } from "./InvestmentView";

const REASONS: Record<string, string> = {
  accounting_unresolved: "Accounting unresolved", daily_halted: "Daily-loss halt",
  entry_intent_pending: "Entry reconciliation pending", exit_pending: "Exit pending",
  protection_unverified: "Protection unverified", persistence_fault: "Persistence fault",
  unmanaged_positions: "Unmanaged holdings",
};

function Protection({ position, now }: { position: Position; now: number }) {
  const observed = position.protection_observed_at;
  const age = observed ? (now - Date.parse(observed)) / 1000 : null;
  const verified = position.protection === "last_verified" && age !== null && age >= 0 && age <= 90;
  const status = verified ? "Last verified" : position.protection === "unverified" ? "Unverified"
    : observed && (age === null || age < 0 || age > 90) ? "Stale observation"
    : position.protection === "unknown" && observed ? "Not verified" : "Unknown";
  return <div className="protection-status">
    <span className={`status ${verified ? "observed" : "caution"}`}>
      {verified ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}{status}
    </span>
    <small className="protection-observed">{observed ? `Observed ${timestamp(observed)}` : "No protection observation"}</small>
  </div>;
}

function PositionTable({ positions, full = false, now }: { positions: Position[]; full?: boolean; now: number }) {
  const [params, setParams] = useSearchParams();
  const search = params.get("q") || "";
  const deferredSearch = useDeferredValue(search.toLowerCase());
  const [ascending, setAscending] = useState(true);
  const [sort, setSort] = useState<"symbol" | "quantity">("symbol");
  const filtered = positions.filter(position => `${position.symbol} ${position.strategy} ${position.side}`.toLowerCase().includes(deferredSearch));
  filtered.sort((left, right) => (sort === "quantity" ? new Decimal(left.quantity).comparedTo(right.quantity) : left.symbol.localeCompare(right.symbol)) * (ascending ? 1 : -1));
  const sortBy = (column: "symbol" | "quantity") => { setAscending(column === sort ? !ascending : true); setSort(column); };
  const Arrow = ascending ? ArrowUp : ArrowDown;
  return <section className="position-section">
    <div className="section-heading">
      <div><h2>{full ? "Holdings" : "Open positions"}</h2><span className="subtle">{positions.length} recorded {positions.length === 1 ? "position" : "positions"}</span></div>
      {full ? <div className="search-box"><Search size={16} /><input aria-label="Search positions" placeholder="Symbol or strategy" value={search} onChange={event => {
        const next = new URLSearchParams(params); event.target.value ? next.set("q", event.target.value) : next.delete("q"); setParams(next, { replace: true });
      }} />{search && <button className="icon-button" title="Clear search" aria-label="Clear search" onClick={() => { const next = new URLSearchParams(params); next.delete("q"); setParams(next, { replace: true }); }}><X size={15} /></button>}</div>
        : <NavLink className="text-link" to="/positions">All positions <ArrowUpRight size={16} /></NavLink>}
    </div>
    {positions.length === 0 ? <div className="empty-state"><Wallet size={30} /><h3>No recorded holdings</h3><p>0 open positions in this snapshot.</p></div>
      : filtered.length === 0 ? <div className="empty-state"><Search size={28} /><h3>No matching positions</h3><p>{search}</p></div>
      : <div className="table-scroll" tabIndex={0} aria-label="Position table" aria-describedby="table-scroll-instructions"><table>
        <thead><tr>
          <th aria-sort={sort === "symbol" ? ascending ? "ascending" : "descending" : "none"}><button onClick={() => sortBy("symbol")}>Instrument {sort === "symbol" && <Arrow size={13} />}</button></th>
          <th>Side</th><th aria-sort={sort === "quantity" ? ascending ? "ascending" : "descending" : "none"}><button onClick={() => sortBy("quantity")}>Quantity {sort === "quantity" && <Arrow size={13} />}</button></th>
          <th>Entry price</th><th>Hard stop</th>{full && <th>Trailing stop</th>}<th>Recorded broker stop</th><th>Protection</th>
        </tr></thead>
        <tbody>{filtered.map(position => <tr key={position.position_id}>
          <td><div className="instrument"><span className="symbol-mark">{position.symbol.split("/")[0].slice(0, 3)}</span><div><strong>{position.symbol}</strong><small>{label(position.strategy)}</small></div></div></td>
          <td><span className={`side ${position.side}`}>{position.side === "long" ? <ArrowUpRight size={13} /> : <ArrowDown size={13} />}{label(position.side)}</span></td>
          <td className="numeric">{quantity(position.quantity)}</td><td className="numeric">{price(position.entry_price)}</td><td className="numeric">{price(position.hard_stop)}</td>
          {full && <td className="numeric">{price(position.trailing_stop)}</td>}<td className="numeric">{price(position.broker_stop)}</td>
          <td><Protection position={position} now={now} />{position.exit_pending && <small className="pending">Exit pending</small>}</td>
        </tr>)}</tbody>
      </table></div>}
    {positions.length > 0 && <p className="position-note">Hard stop is the locally recorded fixed stop; trailing stop is the locally recorded trailing level, when set; broker stop is the broker-stop price saved in this snapshot. These are separate values and do not confirm current broker protection.</p>}
  </section>;
}

function HealthView({ health, meta, now }: { health: Health; meta: Meta; now: number }) {
  const runtime = runtimeStatus(health, meta, now);
  const fresh = sourceFreshness(meta.telemetry, now) === "fresh";
  const flag = (value: boolean | null) => value === null ? "Unknown"
    : !fresh ? value ? "Last reported active" : "Unknown" : value ? "Active" : "Not reported";
  return <section className="health-section">
    <div className="section-heading"><div><h2>System health</h2><span className="subtle">Last observed process and recovery state</span></div><span className={`status ${runtime === "running" ? "observed" : "caution"}`}>{label(runtime)}</span></div>
    <dl className="health-facts">
      <div><dt>Runtime</dt><dd>{label(runtime)}</dd></div>
      <div><dt>Monitor completed</dt><dd>{timestamp(health.last_monitor_completed_at)}</dd></div>
      <div><dt>Health observation</dt><dd>{label(sourceFreshness(meta.telemetry, now))}</dd></div>
      <div><dt>Persistence fault</dt><dd>{flag(health.persistence_fault)}</dd></div>
      <div><dt>Accounting block</dt><dd>{flag(health.accounting_unresolved)}</dd></div>
      <div><dt>Daily-loss halt</dt><dd>{flag(health.daily_halted)}</dd></div>
      <div><dt>Unmanaged symbols</dt><dd>{health.unmanaged_symbols.length ? health.unmanaged_symbols.join(", ") : "None reported"}</dd></div>
      <div><dt>Activity checkpoint</dt><dd>{timestamp(health.activity_sync.checkpoint_at)}</dd></div>
      <div><dt>Activity sync</dt><dd>{label(health.activity_sync.status)}</dd></div>
      <div><dt>Coverage</dt><dd>Bounded overlap; not full history</dd></div>
    </dl>
    <h2 className="health-subheading">Task failures</h2>
    <div className="health-list">{Object.entries(health.task_failures || {}).length ? Object.entries(health.task_failures || {}).map(([task, count]) => <div key={task}><span>{label(task)}</span><strong>{count}</strong></div>) : <p className="subtle">None reported in this observation</p>}</div>
    <h2 className="health-subheading">Warnings</h2>
    <div className="health-list">{health.warnings.length ? health.warnings.map(warning => <div key={warning}><ShieldAlert size={16} /><span>{label(warning)}</span></div>) : <p className="subtle">None reported in this observation</p>}</div>
  </section>;
}

export function App() {
  const client = useQueryClient();
  const workspaceQuery = useQuery({
    queryKey: ["workspace"],
    queryFn: async ({ signal }) => {
      const response = await fetch("/api/v1/settings", {
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
      });
      if (!response.ok) throw new Error("Workspace settings are unavailable.");
      const settings: unknown = await response.json();
      if (typeof settings !== "object" || settings === null || !("workspace" in settings)) {
        throw new Error("Workspace settings failed validation.");
      }
      const workspace = settings.workspace;
      if (workspace !== "demo" && workspace !== "trading" && workspace !== "investment") {
        throw new Error("Workspace settings failed validation.");
      }
      return workspace;
    },
  });
  const investmentWorkspace = workspaceQuery.data === "investment";
  const collectionFetching = useIsFetching({ queryKey: ["collection"] });
  const intelligenceFetching = useIsFetching({ queryKey: ["intelligence"] });
  const location = useLocation();
  const full = location.pathname === "/positions";
  const healthPage = location.pathname === "/health";
  const activityPage = location.pathname === "/activity";
  const historyPage = location.pathname === "/history";
  const intelligencePage = location.pathname === "/intelligence";
  const guidePage = location.pathname === "/guide";
  const investmentPage = location.pathname === "/investment";
  const investmentGuidePage = location.pathname === "/investment-guide";
  const overviewPage = location.pathname === "/";
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const legacyPage = !intelligencePage && !guidePage && !investmentPage && !investmentGuidePage;
  const query = useQuery({ queryKey: ["dashboard"], queryFn: ({ signal }) => loadDashboard(signal), refetchInterval: 5000, enabled: legacyPage && workspaceQuery.isSuccess && !investmentWorkspace });
  const intelligence = useIntelligence(intelligencePage);
  const refreshing = intelligencePage ? intelligenceFetching > 0 : query.isFetching || collectionFetching > 0;
  const refresh = async () => {
    if (intelligencePage) {
      await client.invalidateQueries({ queryKey: ["intelligence"] });
      await client.invalidateQueries({ queryKey: ["analysis-session"] });
      return;
    }
    const result = await query.refetch();
    if (!result.isError) await client.resetQueries({ queryKey: ["collection"] });
  };
  const data = query.data;
  const current = !query.isError && data ? data : null;
  const failure = query.error instanceof DashboardError ? query.error : null;
  const meta = current?.overview.meta || failure?.meta;
  const overview = current?.overview.data;
  const mode = meta?.source_mode;
  const intelligenceMode = intelligence.isError ? undefined : intelligence.data?.source_mode;
  const intelligenceStatus = intelligence.isError ? "Source unavailable"
    : intelligence.isFetching ? intelligence.data ? "Refreshing intelligence" : "Checking source"
    : intelligence.data ? `Snapshot ${timestamp(intelligence.data.generated_at)}` : "Source unknown";
  const telemetryFresh = meta ? sourceFreshness(meta.telemetry, now) === "fresh" : false;
  const blockReasons = overview?.block_reasons.filter(reason => telemetryFresh || reason !== "monitor_not_ready") || [];
  const staleMonitorOnlyBlock = overview?.entries === "blocked"
    && overview.block_reasons.includes("monitor_not_ready") && blockReasons.length === 0;
  const blocked = overview?.entries === "blocked" && !staleMonitorOnlyBlock;
  const positionsAffected = blockReasons.some(reason => ["exit_pending", "protection_unverified", "unmanaged_positions"].includes(reason));
  const runtime = current ? runtimeStatus(current.health.data, current.health.meta, now) : "unknown";
  const pageName = investmentPage ? "Long-term investment" : investmentGuidePage ? "Investment guide" : guidePage ? "Trading guide" : intelligencePage ? "Market intelligence" : historyPage ? "History" : activityPage ? "Activity" : healthPage ? "Health" : full ? "Positions" : "Overview";
  const pageDescription = activityPage ? "Recorded journal events and observed orders; not live order control."
    : historyPage ? "Portfolio totals, original exit records, and separate evidence-linked corrections."
    : intelligencePage ? "Optional calendar, news, and analysis. Advisory only and separate from account records."
    : null;
  const ledgerFreshness = meta ? sourceFreshness(meta.ledger, now) : "missing";
  const ledgerStale = ledgerFreshness !== "fresh";
  if (overviewPage && investmentWorkspace) return <Navigate to="/investment" replace />;
  return <div className="shell">
    <aside className="sidebar">
      <NavLink to={investmentWorkspace ? "/investment" : "/"} className="brand"><span className="brand-mark"><Activity size={24} /></span><span>GoldScout<small>Portfolio monitor</small></span></NavLink>
      <div className="nav-groups">
        <div className="nav-section"><span className="nav-section-label">INVESTMENT</span><nav aria-label="Investment navigation"><NavLink to="/investment"><Wallet size={18} />Investment</NavLink><NavLink to="/investment-guide"><CircleHelp size={18} />Investment guide</NavLink></nav></div>
        {workspaceQuery.isSuccess && !investmentWorkspace && <><div className="nav-section"><span className="nav-section-label">ACCOUNT</span><nav aria-label="Account navigation"><NavLink to="/" end><LayoutDashboard size={18} />Overview</NavLink><NavLink to="/positions"><Wallet size={18} />Positions</NavLink><NavLink to="/health"><Activity size={18} />Health</NavLink></nav></div>
        <div className="nav-section"><span className="nav-section-label">RECORDS</span><nav aria-label="Records navigation"><NavLink to="/activity"><ListOrdered size={18} />Activity</NavLink><NavLink to="/history"><Database size={18} />History</NavLink></nav></div></>}
        <div className="nav-section advisory-nav-section"><span className="nav-section-label">ADVISORY</span><nav aria-label="Advisory navigation"><NavLink to="/intelligence"><Activity size={18}/>Intelligence</NavLink></nav></div>
      </div>
      <span className="nav-hint">Scroll for more sections</span>
      <div className="sidebar-bottom"><LockKeyhole size={16} /><span>Trading read-only</span><span className="local-label">LOCAL</span></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><div className="breadcrumb">Portfolio <span>/</span> <strong>{pageName}</strong></div>
        {!guidePage && !investmentPage && !investmentGuidePage && (!legacyPage || !investmentWorkspace) && <div className="topbar-status">{intelligencePage
          ? <><span className={`mode ${intelligenceMode === "demo" ? "demo" : ""}`}>{intelligenceMode === "demo" ? "SYNTHETIC DEMO" : intelligenceMode === "recorded" ? "RECORDED INTELLIGENCE" : intelligence.isFetching ? "Checking source" : "Source unknown"}</span><span className="connection" aria-live="polite">{intelligenceStatus}</span></>
          : <><span className={`mode ${mode === "demo" ? "demo" : meta?.environment === "live" ? "live" : ""}`}>{mode === "demo" ? "SYNTHETIC DEMO" : meta?.environment ? label(meta.environment) : "Source unknown"}</span><span className="connection"><span className={`dot ${runtime}`} />Runtime {runtime}</span></>}</div>}
      </header>
      <main>
        <span className="sr-only" id="table-scroll-instructions">Scroll horizontally to see all columns.</span>
        <div className="page-heading"><div><p className="eyebrow">{guidePage ? "GUIDE" : intelligencePage ? "INTELLIGENCE WORKSPACE" : mode === "demo" ? "DEMO WORKSPACE" : "ACCOUNT WORKSPACE"}</p><h1>{pageName === "Overview" ? "Portfolio overview" : pageName}</h1>{pageDescription && <p className="page-description">{pageDescription}</p>}</div>
          {!guidePage && !investmentPage && !investmentGuidePage && (!legacyPage || (!investmentWorkspace && workspaceQuery.isSuccess)) && <button className="icon-button refresh" aria-label="Refresh data" title="Refresh data" disabled={refreshing} onClick={() => void refresh()}><RefreshCw size={19} className={refreshing ? "spin" : ""} /></button>}
        </div>
        {!guidePage && !investmentPage && !investmentGuidePage && !intelligencePage && mode === "demo" && <div className="demo-band"><Activity size={16} /><span>Synthetic data</span></div>}
        {investmentPage ? <InvestmentView now={now} /> : investmentGuidePage ? <InvestmentGuide /> : guidePage ? <TradingGuide /> : intelligencePage ? <IntelligenceView data={intelligence}/> : investmentWorkspace ? <div className="error-state" role="status"><p>Legacy trading records are not selected in this workspace.</p><NavLink to="/investment">Open investment records</NavLink></div>
          : workspaceQuery.isError ? <div className="error-state" role="alert"><p>Workspace settings are unavailable; account source is unknown.</p><button onClick={() => void workspaceQuery.refetch()}>Retry</button></div>
          : workspaceQuery.isPending || query.isPending ? <div className="loading" role="status"><RefreshCw size={24} className="spin" /><span>Loading recorded account data</span></div>
          : !current ? <div className="error-state" role="alert"><ShieldAlert size={36} /><p className="eyebrow">ACCOUNT DATA UNAVAILABLE</p><h2>{failure?.code === "ledger_invalid" ? "Snapshot validation failed" : failure?.code === "schema_unsupported" ? "Unsupported ledger schema" : "No account snapshot"}</h2><p>{failure?.message || "The dashboard source could not be reached."}</p><button onClick={() => void query.refetch()}><RefreshCw size={16} />Retry</button></div>
          : <>
            <div className="snapshot-strip"><span><Database size={14} />Revision {meta?.ledger_revision ?? "Unknown"}</span><span className={ledgerStale ? "warning-text" : ""}>{label(ledgerFreshness)} snapshot</span>{query.isFetching && <span>Refreshing</span>}<time>{timestamp(meta?.ledger.observed_at || null)}</time></div>
            {overviewPage && <>
              <section className={`gate-band ${blocked ? "blocked" : "unknown"}`}>
                <span className="gate-icon">{blocked ? <ShieldAlert size={22} /> : <CircleHelp size={22} />}</span>
                <div className="gate-content">
                  <h2>Entry gate <span>{blocked ? "Blocked in snapshot" : "Unknown"}</span></h2>
                  <div className="gate-reasons">{blockReasons.length ? blockReasons.map(reason => <span key={reason}>{REASONS[reason] || label(reason)}</span>) : <span>{meta?.telemetry.observed_at ? "Entry permission not established" : "No runtime observation"}</span>}</div>
                  <p>{blocked ? "This records a block in the snapshot; it is not a live broker confirmation." : "Unknown is not approval to trade. This read-only snapshot cannot establish current entry permission."}</p>
                  <div className="gate-actions"><NavLink to="/health">Review system health <ArrowUpRight size={14} /></NavLink>{positionsAffected && <NavLink to="/positions">Review affected positions <ArrowUpRight size={14} /></NavLink>}</div>
                </div>
                <LockKeyhole size={17} className="gate-lock" />
              </section>
              <div className="observation-band"><div><Activity size={17} /><span>Broker prices</span><strong>{!meta?.broker.observed_at ? "No price observation" : label(sourceFreshness(meta.broker, now))}</strong></div><div><Activity size={17} /><span>Monitoring</span><strong>{!meta?.telemetry.observed_at ? "No runtime observation" : label(sourceFreshness(meta.telemetry, now))}</strong></div></div>
              <section className="metrics" aria-label="Account totals">
                <div><span>Recorded equity</span><strong>{money(overview!.recorded_equity)}</strong><small>USD</small></div>
                <div><span>Realized P&amp;L</span><strong className={overview!.realized_pnl !== null && new Decimal(overview!.realized_pnl).isPositive() ? "positive" : ""}>{money(overview!.realized_pnl)}</strong><small>{overview!.accounting_day || "Accounting day unknown"}</small></div>
                <div><span>Unrealized P&amp;L</span><strong className="unavailable">{money(overview!.unrealized_pnl)}</strong><small>Live observation unavailable</small></div>
                <div><span>Open positions</span><strong>{current.positions.data.length.toString().padStart(2, "0")}</strong><small>Recorded holdings</small></div>
              </section>
            </>}
            {historyPage ? <HistoryView databaseId={current.overview.meta.database_id} /> : activityPage ? <ActivityView databaseId={current.overview.meta.database_id} /> : healthPage ? <HealthView health={current.health.data} meta={current.health.meta} now={now} /> : <PositionTable positions={current.positions.data.map(position => runtime === "running" || position.protection !== "last_verified" ? position : { ...position, protection: "unknown" })} full={full} now={now} />}
            {!overviewPage && <div className="observation-band"><div><Activity size={17} /><span>Broker prices</span><strong>{!meta?.broker.observed_at ? "No price observation" : label(sourceFreshness(meta.broker, now))}</strong></div><div><Activity size={17} /><span>Monitoring</span><strong>{!meta?.telemetry.observed_at ? "No runtime observation" : label(sourceFreshness(meta.telemetry, now))}</strong></div></div>}
          </>}
        <footer><span>GoldScout <span className="footer-separator">/</span> Portfolio monitor <span className="footer-separator">/</span> <NavLink to={investmentWorkspace ? "/investment-guide" : "/guide"}>{investmentWorkspace ? "Investment guide" : "Trading guide"}</NavLink></span><span><LockKeyhole size={12} />Trading records read-only</span></footer>
      </main>
    </div>
  </div>;
}