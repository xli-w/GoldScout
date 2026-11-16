import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { RefreshCw, ShieldAlert } from "lucide-react";
import validate from "./investment-validator.js";
import validateV2 from "./investment-validator-v2.js";
import { label, timestamp } from "./api";

export type InvestmentSnapshot = {
  schema_version: 1; source_mode: "demo" | "recorded"; portfolio_id: string;
  revision: number; generated_at: string; as_of: string | null; currency: "GBP";
  complete: boolean; equity_gbp: string | null; net_contributions_gbp: string;
  gain_gbp: string | null; twr_pct: string | null; max_drawdown_pct: string | null;
  accounts: { id: string; name: string; kind: "broker" | "deposit"; cash_gbp: string }[];
  holdings: { account_id: string; product_id: string; name: string; sleeve: "equity" | "bond"; quantity: string; value_gbp: string | null }[];
  allocation: { sleeve: "equity" | "bond" | "cash"; target_pct: string; actual_pct: string | null; drift_pp: string | null }[];
  proposal: { status: "review" | "no_trade" | "blocked"; reason: string; expires_at: string | null;
    orders: { account_id: string; product_id: string; side: "buy" | "sell"; quantity: string; estimated_notional_gbp: string }[];
    manual_transfers: string[]; execution_enabled: false };
  warnings: string[]; limitations: string[];
};

type OpeningSnapshot = Omit<InvestmentSnapshot, "schema_version" | "gain_gbp" | "allocation" | "holdings" | "accounts"> & {
  schema_version: 2; baseline_as_of: string | null; since_baseline_gain_gbp: string | null;
  lifetime_gain_gbp: null;
  cash_observations: { account_id: string; balance_gbp: string; difference_gbp: string;
    timestamp: string; available_at: string; source: string; evidence: string }[];
  valuation_history: {
    timestamp: string; source_available_at: string; as_of: string | null; complete: boolean;
    equity_gbp: string | null; capital_basis_gbp: string | null;
    net_contributions_gbp: string | null; gain_since_baseline_gbp: string | null;
    twr_pct: string | null; max_drawdown_pct: string | null; gap_reason: string | null;
  }[];
  accounts: { id: string; name: string; kind: "broker" | "deposit"; cash_gbp: string | null;
    cash_observation: { balance_gbp: string; difference_gbp: string; timestamp: string;
      available_at: string; source: string; evidence: string } | null;
    terms: { annual_rate_pct: string | null; rate_basis: "AER" | "gross" | null;
      effective_from: string; observed_at: string; available_at: string; source: string;
      maturity_date: string | null; notice_days: number | null } | null }[];
  holdings: { account_id: string; product_id: string; name: string; sleeve: "equity" | "bond";
    quantity: string; value_gbp: string | null; mark_at: string | null; mark_available_at: string | null }[];
};

async function loadInvestment(signal: AbortSignal): Promise<InvestmentSnapshot | OpeningSnapshot> {
  const response = await fetch("/api/investment/v1/snapshot", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error("Investment records are unavailable. Select a valid investment store or synthetic demo.");
  if (typeof body !== "object" || body === null || !("schema_version" in body))
    throw new Error("Investment response failed validation.");
  if (body.schema_version === 2) {
    if (!validateV2(body)) throw new Error("Investment response failed validation.");
    return body as OpeningSnapshot;
  }
  if (!validate(body)) throw new Error("Investment response failed validation.");
  return body as InvestmentSnapshot;
}

function pounds(value: string | null) {
  if (value === null) return "Unavailable";
  const amount = new Decimal(value);
  const [whole, fraction] = amount.abs().toFixed(2).split(".");
  return `${amount.isNegative() && !amount.isZero() ? "-" : ""}\u00a3${new Intl.NumberFormat("en-GB").format(BigInt(whole))}.${fraction}`;
}
function percent(value: string | null) { return value === null ? "Unavailable" : `${new Decimal(value).toFixed(2)}%`; }

function InvestmentGrowthHistory({ history }: { history: OpeningSnapshot["valuation_history"] }) {
  const chartHistory = history.slice(-500);
  const chartKnown = chartHistory.filter(point =>
    point.complete && point.equity_gbp !== null && point.capital_basis_gbp !== null);
  const values = chartKnown.flatMap(point =>
    [new Decimal(point.equity_gbp!), new Decimal(point.capital_basis_gbp!)]);
  const low = values.length
    ? values.slice(1).reduce((value, candidate) => Decimal.min(value, candidate), values[0])
    : new Decimal(0);
  const high = values.length
    ? values.slice(1).reduce((value, candidate) => Decimal.max(value, candidate), values[0])
    : new Decimal(0);
  const spread = high.minus(low);
  const padding = spread.isZero() ? Decimal.max(high.abs().mul("0.05"), new Decimal(1)) : spread.mul("0.1");
  const minimum = low.minus(padding), maximum = high.plus(padding);
  const range = maximum.minus(minimum);
  const firstTime = chartHistory.length ? Date.parse(chartHistory[0].timestamp) : 0;
  const lastTime = chartHistory.length ? Date.parse(chartHistory[chartHistory.length - 1].timestamp) : firstTime;
  const timeRange = Math.max(lastTime - firstTime, 1);
  const left = 72, right = 780, top = 20, bottom = 220;
  const x = (point: OpeningSnapshot["valuation_history"][number]) =>
    left + (Date.parse(point.timestamp) - firstTime) / timeRange * (right - left);
  const y = (value: string) =>
    top + new Decimal(maximum.minus(value)).div(range).toNumber() * (bottom - top);
  const lineSegments = (field: "equity_gbp" | "capital_basis_gbp") => {
    const segments: string[][] = [];
    let segment: string[] = [];
    for (const point of chartHistory) {
      const value = point.complete ? point[field] : null;
      if (value === null) {
        if (segment.length) segments.push(segment);
        segment = [];
      } else {
        segment.push(`${x(point)},${y(value)}`);
      }
    }
    if (segment.length) segments.push(segment);
    return segments;
  };
  const latest = [...history].reverse().slice(0, 20);
  const dateLabel = (value: string) => new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium", timeZone: "UTC",
  }).format(Date.parse(value));

  return <section className="history-group">
    <h2>Recorded growth history</h2>
    <p className="position-note">Only recorded valuation event clocks are shown. Lines connect observations; no intermediate values are inferred. Incomplete clocks remain gaps.</p>
    {history.length === 0
      ? <p>No valuation-relevant events are recorded yet.</p>
      : <>
        {chartKnown.length > 0 ? <figure className="history-chart">
          <figcaption>Recorded value and baseline capital <span>{chartKnown.length} complete of {chartHistory.length} recent event clocks</span></figcaption>
          <svg viewBox="0 0 800 260" role="img" aria-label="Recorded portfolio value and baseline capital; incomplete valuation clocks are gaps">
            {[0, 1, 2, 3].map(index => {
              const value = maximum.minus(range.mul(index).div(3));
              const yAt = top + index / 3 * (bottom - top);
              return <g key={index}>
                <line x1={left} x2={right} y1={yAt} y2={yAt} className="chart-baseline" />
                <text x={left - 8} y={yAt + 4} textAnchor="end" className="chart-label">{pounds(value.toFixed(2))}</text>
              </g>;
            })}
            {lineSegments("equity_gbp").map((segment, index) =>
              segment.length > 1 && <polyline key={`equity-${index}`} points={segment.join(" ")}
                fill="none" stroke="#21745f" strokeWidth="2" />)}
            {lineSegments("capital_basis_gbp").map((segment, index) =>
              segment.length > 1 && <polyline key={`basis-${index}`} points={segment.join(" ")}
                fill="none" stroke="#69777c" strokeWidth="2" strokeDasharray="5 4" />)}
            {chartKnown.map(point => <g key={point.timestamp}>
              <circle cx={x(point)} cy={y(point.equity_gbp!)} r="3" fill="#21745f">
                <title>{`${dateLabel(point.timestamp)} · Value ${pounds(point.equity_gbp)}`}</title>
              </circle>
              <circle cx={x(point)} cy={y(point.capital_basis_gbp!)} r="2.5" fill="#69777c">
                <title>{`${dateLabel(point.timestamp)} · Baseline capital ${pounds(point.capital_basis_gbp)}`}</title>
              </circle>
            </g>)}
            {chartHistory.filter(point => !point.complete).map(point =>
              <g key={point.timestamp}>
                <line x1={x(point)} x2={x(point)} y1={top} y2={bottom}
                  stroke="#a63747" strokeDasharray="3 4" />
                <title>{`${dateLabel(point.timestamp)} · ${point.gap_reason}`}</title>
              </g>)}
            <text x={left} y="246" className="chart-label">{dateLabel(chartHistory[0].timestamp)}</text>
            <text x={right} y="246" textAnchor="end" className="chart-label">{dateLabel(chartHistory[chartHistory.length - 1].timestamp)}</text>
          </svg>
          <div className="investment-chart-legend"><span><i className="investment-legend-value" />Recorded value</span><span><i className="investment-legend-capital" />Baseline capital</span><span><i className="investment-legend-gap" />Incomplete clock</span></div>
        </figure> : <p>No complete contemporaneous valuation is available in the recent history.</p>}
        <p className="position-note">The chart shows the latest {chartHistory.length} of {history.length} event clocks; the table shows the latest {latest.length}. Source-availability times and gaps are retained.</p>
        <div className="table-scroll" tabIndex={0} aria-label="Recorded growth history">
          <table>
            <thead><tr><th>Event clock (UTC)</th><th>Status and gap reason</th><th>Available at</th><th>Value</th><th>Capital basis</th><th>Gain since baseline</th><th>TWR</th></tr></thead>
            <tbody>{latest.map(point => <tr key={point.timestamp}>
              <td>{timestamp(point.timestamp)}</td><td>{point.complete ? "Complete" : `Gap: ${point.gap_reason}`}</td>
              <td>{timestamp(point.source_available_at)}</td><td>{pounds(point.equity_gbp)}</td>
              <td>{pounds(point.capital_basis_gbp)}</td><td>{pounds(point.gain_since_baseline_gbp)}</td>
              <td>{percent(point.twr_pct)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
  </section>;
}

export function InvestmentView() {
  const query = useQuery({ queryKey: ["investment"], queryFn: ({ signal }) => loadInvestment(signal), refetchInterval: 60000 });
  const data = query.isError ? undefined : query.data;
  return <article className="investment-view">
    <div className="record-toolbar">
      <NavLink className="text-link" to="/investment-guide">Investment guide</NavLink>
      <button className="icon-button refresh" aria-label="Refresh investment" disabled={query.isFetching} onClick={() => void query.refetch()}>
        <RefreshCw size={19} className={query.isFetching ? "spin" : ""} />
      </button>
    </div>
    {query.isPending ? <p role="status">Loading investment records</p>
      : !data ? <div className="error-state" role="alert"><ShieldAlert size={30} /><h2>Investment data unavailable</h2>
        <p>{query.error instanceof Error ? query.error.message : "The investment source could not be reached."}</p></div>
        : <>
          <div className="demo-band"><span>{data.source_mode === "demo" ? "SYNTHETIC INVESTMENT DEMO" : "RECORDED INVESTMENT"}</span><span>Read-only / execution disabled</span></div>
          <p className="position-note">Valuation as of {timestamp(data.as_of)}. Revision {data.revision}. {data.complete ? "Complete at the recorded clock." : "Incomplete valuation; no complete portfolio total."} Recorded inputs are not current broker verification.</p>
          <section className="metrics" aria-label="Investment totals">
            <div><span>Portfolio value</span><strong>{pounds(data.equity_gbp)}</strong><small>GBP, recorded valuation</small></div>
            <div><span>Net contributions</span><strong>{pounds(data.net_contributions_gbp)}</strong><small>Deposits are not profit</small></div>
            <div><span>{data.schema_version === 2 ? "Gain since baseline" : "Investment gain"}</span><strong>{pounds(data.schema_version === 2 ? data.since_baseline_gain_gbp : data.gain_gbp)}</strong><small>Net of recorded fees</small></div>
          </section>
          {data.schema_version === 2 && <p className="position-note">Baseline as of {timestamp(data.baseline_as_of)}. Lifetime gain and prior cost basis are unavailable. No valuation freshness threshold is configured.</p>}
          <dl className="health-facts">
            <div><dt>Time-weighted return</dt><dd>{percent(data.twr_pct)}</dd></div>
            <div><dt>Flow-neutral maximum drawdown</dt><dd>{percent(data.max_drawdown_pct)}</dd></div>
          </dl>
          {data.schema_version === 2 && <InvestmentGrowthHistory history={data.valuation_history} />}
          {data.schema_version === 1 && <section className="history-group">
            <h2>Allocation and drift</h2>
            <div className="table-scroll" tabIndex={0} aria-label="Investment allocation" aria-describedby="table-scroll-instructions"><table>
              <thead><tr><th>Sleeve</th><th>Target</th><th>Recorded</th><th>Drift (percentage points)</th></tr></thead>
              <tbody>{data.allocation.map(row => <tr key={row.sleeve}><td>{label(row.sleeve)}</td><td>{percent(row.target_pct)}</td><td>{percent(row.actual_pct)}</td><td>{row.drift_pp === null ? "Unavailable" : new Decimal(row.drift_pp).toFixed(2)}</td></tr>)}</tbody>
            </table></div>
          </section>}
          <section className="history-group"><h2>Cash accounts</h2><dl className="health-facts">
            {data.accounts.map(account => <div key={account.id}><dt>{account.name} ({label(account.kind)})</dt><dd>{pounds(account.cash_gbp)}</dd></div>)}
          </dl><p className="position-note">External savings are not broker buying power. Deposit protection and product eligibility require separate evidence.</p></section>
          {data.schema_version === 2 && data.accounts.some(account => account.cash_observation !== null) &&
            <section className="history-group"><h2>Observed cash balances</h2>
              <p className="position-note">Operator-entered evidence, not provider verification. Differences compare with recorded cash at that timestamp; they are not corrections or proof of a later balance.</p>
              <p className="position-note">Showing the latest {Math.min(data.cash_observations.length, 100)} of {data.cash_observations.length} recorded observations.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Cash reconciliation history">
                <table>
                  <thead><tr><th>Observed at (UTC)</th><th>Account</th><th>Balance</th><th>Difference (observed minus recorded)</th><th>Available at</th><th>Source and evidence</th></tr></thead>
                  <tbody>{[...data.cash_observations].reverse().slice(0, 100).map((observation, index) =>
                    <tr key={`${observation.account_id}:${observation.timestamp}:${index}`}>
                      <td>{timestamp(observation.timestamp)}</td><td>{observation.account_id}</td>
                      <td>{pounds(observation.balance_gbp)}</td><td>{pounds(observation.difference_gbp)}</td>
                      <td>{timestamp(observation.available_at)}</td>
                      <td>{observation.source} · {observation.evidence}</td>
                    </tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.schema_version === 2 && data.accounts.some(account => account.terms !== null) &&
            <section className="history-group"><h2>Reported savings-account terms</h2>
              <p className="position-note">Operator-recorded facts, not current provider verification or an interest-accrual input.</p>
              <dl className="health-facts">{data.accounts.filter(account => account.terms !== null).map(account => {
                const terms = account.terms;
                if (terms === null) return null;
                return <div key={account.id}><dt>{account.name}</dt><dd>
                  {terms.annual_rate_pct === null ? "Rate unavailable" : `${percent(terms.annual_rate_pct)} ${terms.rate_basis}`}
                  {` · Effective ${terms.effective_from} · Observed ${timestamp(terms.observed_at)} · Available ${timestamp(terms.available_at)} · Source: ${terms.source}`}
                  {terms.maturity_date !== null && ` · Maturity ${terms.maturity_date}`}
                  {terms.notice_days !== null && ` · Notice ${terms.notice_days} days`}
                </dd></div>;
              })}</dl>
            </section>}
          <section className="history-group"><h2>Long-term holdings</h2>
            <div className="table-scroll" tabIndex={0} aria-label="Investment holdings" aria-describedby="table-scroll-instructions"><table>
              <thead><tr><th>Fund</th><th>Account</th><th>Sleeve</th><th>Quantity</th><th>Value</th>{data.schema_version === 2 && <><th>Mark as of</th><th>Available at</th></>}</tr></thead>
              <tbody>{data.holdings.map(holding => <tr key={`${holding.account_id}:${holding.product_id}`}><td>{holding.name}</td><td>{holding.account_id}</td><td>{label(holding.sleeve)}</td><td>{holding.quantity}</td><td>{pounds(holding.value_gbp)}</td>{"mark_at" in holding && <><td>{timestamp(holding.mark_at)}</td><td>{timestamp(holding.mark_available_at)}</td></>}</tr>)}</tbody>
            </table></div>
          </section>
          <section className="history-group"><h2>Allocation proposal: {label(data.proposal.status)}</h2>
            <p>{data.proposal.reason}</p><p className="position-note">Export-only. No order approval or execution is available here. Proposal expiry: {timestamp(data.proposal.expires_at, "Not issued")}.</p>
            {data.proposal.orders.length > 0 && <div className="table-scroll" tabIndex={0} aria-label="Proposed investment changes" aria-describedby="table-scroll-instructions"><table>
              <thead><tr><th>Account</th><th>Product</th><th>Side</th><th>Quantity</th><th>Estimated notional</th></tr></thead>
              <tbody>{data.proposal.orders.map((order, index) => <tr key={index}><td>{order.account_id}</td><td>{order.product_id}</td><td>{label(order.side)}</td><td>{order.quantity}</td><td>{pounds(order.estimated_notional_gbp)}</td></tr>)}</tbody>
            </table></div>}
            {data.proposal.manual_transfers.map(message => <p key={message}>{message}</p>)}
          </section>
          {data.warnings.length > 0 && <section className="history-group" role="status"><h2>Evidence and valuation warnings</h2><ul>{data.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>}
          <section className="history-group"><h2>Capability limits</h2><ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul>
            <p>No market investment guarantees profit or a maximum loss. This is a measurement and discipline tool, not evidence of future outperformance.</p>
          </section>
        </>}
  </article>;
}

export function InvestmentGuide() {
  return <article className="trading-guide">
    <p className="guide-intro">GoldScout's investment path supports a long-term, human-chosen portfolio of global funds, GBP savings and high-quality bonds. It measures recorded holdings and proposes changes; it does not trade.</p>
    <section><h2>Ownership, not short-term prediction</h2><p>Broad diversified funds spread company exposure, but still face market declines and concentration. GBP dealing does not remove underlying currency exposure. Bonds can fall when interest rates or credit conditions change; they are not protected savings.</p></section>
    <section><h2>Contributions and rebalancing</h2><p>Recorded broker cash funds underweight purchases first. Annual review or five percentage points of sleeve drift can also suggest sales of overweight holdings. Proposed sale proceeds never fund purchases; record actual proceeds and check settlement first. Review acknowledgements are recorded through the offline CLI, not the dashboard; five-point drift remains a trigger. They do not approve trades. Future fees and settlement require human verification. No trade is a valid outcome. The chosen policy is not automatically optimised.</p></section>
    <section><h2>Accurate GBP measurement</h2><p>Deposits are not profit. Internal transfers do not change total contributed capital. Recorded fills, costs and price observations determine the supported valuation; missing or stale evidence never grants permission to trade. External savings cannot be spent as broker cash.</p></section>
    <section><h2>Separate safety boundaries</h2><p>The dashboard remains read-only. Broker execution, licensed portfolio research, money-weighted return, inflation and full multi-currency accounting require further validated capabilities. Existing legacy trading stops and recovery are not removed or changed by the investment path. News and AI remain advisory only.</p></section>
    <div className="guide-warning"><strong>Losses can be large, prolonged or permanent.</strong> Diversification, lower costs and a long horizon do not guarantee returns. Exact products, savings protection, tax wrappers and provider access require current verification.</div>
  </article>;
}
