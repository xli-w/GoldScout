import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { RefreshCw, ShieldAlert } from "lucide-react";
import validate from "./investment-validator.js";
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

async function loadInvestment(signal: AbortSignal): Promise<InvestmentSnapshot> {
  const response = await fetch("/api/investment/v1/snapshot", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error("Investment records are unavailable. Select a valid investment store or synthetic demo.");
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
            <div><span>Investment gain</span><strong>{pounds(data.gain_gbp)}</strong><small>Net of recorded fees</small></div>
          </section>
          <dl className="health-facts">
            <div><dt>Time-weighted return</dt><dd>{percent(data.twr_pct)}</dd></div>
            <div><dt>Flow-neutral maximum drawdown</dt><dd>{percent(data.max_drawdown_pct)}</dd></div>
          </dl>
          <section className="history-group">
            <h2>Allocation and drift</h2>
            <div className="table-scroll" tabIndex={0} aria-label="Investment allocation" aria-describedby="table-scroll-instructions"><table>
              <thead><tr><th>Sleeve</th><th>Target</th><th>Recorded</th><th>Drift (percentage points)</th></tr></thead>
              <tbody>{data.allocation.map(row => <tr key={row.sleeve}><td>{label(row.sleeve)}</td><td>{percent(row.target_pct)}</td><td>{percent(row.actual_pct)}</td><td>{row.drift_pp === null ? "Unavailable" : new Decimal(row.drift_pp).toFixed(2)}</td></tr>)}</tbody>
            </table></div>
          </section>
          <section className="history-group"><h2>Cash accounts</h2><dl className="health-facts">
            {data.accounts.map(account => <div key={account.id}><dt>{account.name} ({label(account.kind)})</dt><dd>{pounds(account.cash_gbp)}</dd></div>)}
          </dl><p className="position-note">External savings are not broker buying power. Deposit protection and product eligibility require separate evidence.</p></section>
          <section className="history-group"><h2>Long-term holdings</h2>
            <div className="table-scroll" tabIndex={0} aria-label="Investment holdings" aria-describedby="table-scroll-instructions"><table>
              <thead><tr><th>Fund</th><th>Account</th><th>Sleeve</th><th>Quantity</th><th>Value</th></tr></thead>
              <tbody>{data.holdings.map(holding => <tr key={`${holding.account_id}:${holding.product_id}`}><td>{holding.name}</td><td>{holding.account_id}</td><td>{label(holding.sleeve)}</td><td>{holding.quantity}</td><td>{pounds(holding.value_gbp)}</td></tr>)}</tbody>
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
    <section><h2>Contributions and rebalancing</h2><p>Recorded broker cash funds underweight purchases first. Annual review or five percentage points of sleeve drift can also suggest sales of overweight holdings. Proposed sale proceeds never fund purchases; record actual proceeds and check settlement first. Review acknowledgements are deferred. Future fees and settlement require human verification. No trade is a valid outcome. The chosen policy is not automatically optimised.</p></section>
    <section><h2>Accurate GBP measurement</h2><p>Deposits are not profit. Internal transfers do not change total contributed capital. Recorded fills, costs and price observations determine the supported valuation; missing or stale evidence never grants permission to trade. External savings cannot be spent as broker cash.</p></section>
    <section><h2>Separate safety boundaries</h2><p>The dashboard remains read-only. Broker execution, licensed portfolio research, money-weighted return, inflation and full multi-currency accounting require further validated capabilities. Existing legacy trading stops and recovery are not removed or changed by the investment path. News and AI remain advisory only.</p></section>
    <div className="guide-warning"><strong>Losses can be large, prolonged or permanent.</strong> Diversification, lower costs and a long horizon do not guarantee returns. Exact products, savings protection, tax wrappers and provider access require current verification.</div>
  </article>;
}
