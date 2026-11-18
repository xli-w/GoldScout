import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import validate from "./investment-validator-v2.js";
import { label, timestamp } from "./api";
import { PortfolioAwareness } from "./PortfolioAwareness";
import { PortfolioMonitoring } from "./PortfolioMonitoring";

export type InvestmentSnapshot = {
  schema_version: 2; source_mode: "demo" | "recorded"; portfolio_id: string;
  revision: number; generated_at: string; as_of: string | null; currency: "GBP";
  complete: boolean; equity_gbp: string | null; net_contributions_gbp: string;
  twr_pct: string | null; max_drawdown_pct: string | null;
  proposal: { status: "blocked"; reason: string; expires_at: null;
    orders: []; manual_transfers: []; execution_enabled: false };
  warnings: string[]; limitations: string[];
  baseline_as_of: string | null; since_baseline_gain_gbp: string | null;
  role_accounting_at: string | null;
  role_assignments: { id: string; account_id: string; product_id: string | null;
    role: "core" | "dynamic"; amount: string; timestamp: string; available_at: string; evidence: string }[];
  role_accounting: { role: "core" | "dynamic" | "unassigned"; cash_gbp: string | null;
    value_gbp: string | null; accounts: { account_id: string; cash_gbp: string }[];
    holdings: { account_id: string; product_id: string; quantity: string }[] }[];
  lifetime_gain_gbp: null;
  recorded_fees_gbp: string; recorded_interest_gbp: string; recorded_dividends_gbp: string;
  recorded_income_gbp: string;
  recorded_fee_events: { id: string; type: "buy" | "sell"; account_id: string;
    product_id: string; role: string; quantity: string; price_gbp: string; fee_gbp: string;
    timestamp: string; available_at: string; evidence: string }[];
  recorded_income_events: { id: string; type: "interest" | "dividend"; account_id: string;
    product_id: string | null; amount_gbp: string; timestamp: string;
    role: string; role_evidence: string | null; available_at: string; evidence: string }[];
  recorded_flow_events: { id: string; type: "contribution" | "withdrawal" | "transfer";
    account_id: string; destination_account_id: string | null; amount_gbp: string;
    role: string; timestamp: string; available_at: string; evidence: string }[];
  recorded_corrections: { id: string; original_event_id: string; replacement_event_id: string;
    replaced_type: "contribution" | "withdrawal" | "transfer" | "interest" | "dividend" | "buy" | "sell" | "split" | "mark";
    account_id: string | null; role: string; role_evidence: string | null;
    original_amount_gbp: string | null; replacement_amount_gbp: string | null; timestamp: string;
    available_at: string; evidence: string }[];
  recorded_profile_changes: { id: string; action: "add_product" | "rename" | "retire" | "scope_addition";
    target: "account" | "product"; entity_id: string; name: string | null;
    timestamp: string; available_at: string; evidence: string }[];
  recorded_scope_additions_gbp: string;
  recorded_scope_additions: { id: string; account_id: string; amount_gbp: string;
    timestamp: string; available_at: string; evidence: string }[];
  cash_observations: { account_id: string; balance_gbp: string; difference_gbp: string;
    timestamp: string; available_at: string; source: string; evidence: string }[];
  valuation_history: {
    timestamp: string; source_available_at: string; as_of: string | null; complete: boolean;
    equity_gbp: string | null; capital_basis_gbp: string | null;
    net_contributions_gbp: string | null; gain_since_baseline_gbp: string | null;
    twr_pct: string | null; max_drawdown_pct: string | null; gap_reason: string | null;
  }[];
  accounts: { id: string; name: string; kind: "broker" | "deposit"; status: "active" | "retired"; cash_gbp: string | null;
    cash_observation: { balance_gbp: string; difference_gbp: string; timestamp: string;
      available_at: string; source: string; evidence: string } | null;
    terms: { annual_rate_pct: string | null; rate_basis: "AER" | "gross" | null;
      effective_from: string; observed_at: string; available_at: string; source: string;
      maturity_date: string | null; notice_days: number | null } | null }[];
  holdings: { account_id: string; product_id: string; name: string; sleeve: "equity" | "bond";
    quantity: string; value_gbp: string | null; mark_at: string | null; mark_available_at: string | null }[];
};

type OpeningSnapshot = InvestmentSnapshot;

async function loadInvestment(signal: AbortSignal): Promise<InvestmentSnapshot> {
  const response = await fetch("/api/investment/v1/snapshot", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error("Investment records are unavailable. Select a valid investment store or synthetic demo.");
  if (!validate(body)) throw new Error("Investment response failed validation.");
  return body as InvestmentSnapshot;
}

function pounds(value: string | null, exact = false) {
  if (value === null) return "Unavailable";
  const amount = new Decimal(value);
  const [whole, fraction] = amount.abs().toFixed(exact ? Math.max(2, amount.decimalPlaces()) : 2).split(".");
  return `${amount.isNegative() && !amount.isZero() ? "-" : ""}\u00a3${new Intl.NumberFormat("en-GB").format(BigInt(whole))}.${fraction}`;
}
function percent(value: string | null) { return value === null ? "Unavailable" : `${new Decimal(value).toFixed(2)}%`; }

function evidenceAge(value: string | null, now: number) {
  if (value === null) return "Age unavailable";
  const age = now - Date.parse(value);
  if (!Number.isFinite(age) || age < 0) return "Age unavailable (future or invalid clock)";
  const days = Math.floor(age / 86400000);
  if (days) return `${days} ${days === 1 ? "day" : "days"}`;
  const hours = Math.floor(age / 3600000);
  if (hours) return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  const minutes = Math.floor(age / 60000);
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

function CashEvidenceOverview({ data, now }: { data: OpeningSnapshot; now: number }) {
  const differences = useMemo(() => {
    const result = new Map<string, { count: number; first: OpeningSnapshot["cash_observations"][number];
      latest: OpeningSnapshot["cash_observations"][number] }>();
    for (const observation of data.cash_observations) {
      if (new Decimal(observation.difference_gbp).isZero()) continue;
      const prior = result.get(observation.account_id);
      if (prior) {
        prior.count++;
        prior.latest = observation;
      } else {
        result.set(observation.account_id, { count: 1, first: observation, latest: observation });
      }
    }
    return result;
  }, [data.cash_observations]);
  const correctionWarning = data.warnings.find(warning =>
    warning.startsWith("Historical correction replay may have changed past cash-observation differences."));
  return <section className="history-group" aria-label="Cash evidence overview">
    <h2>Cash evidence overview</h2>
    <p className="position-note">Counts and differences below reflect the current replay of recorded observations. Later matching observations do not resolve differences in this replay. Absence of a difference does not establish a current balance.</p>
    {correctionWarning && <p className="position-note">{correctionWarning}</p>}
    <dl className="health-facts">{data.accounts.map(account => {
      const history = differences.get(account.id);
      const firstDifference = history?.first;
      const latestDifference = history?.latest;
      const latest = account.cash_observation;
      return <div key={account.id}><dt>{account.name} ({account.id})</dt><dd>
        {latest === null ? "No cash observation" : <>
          Latest observed {timestamp(latest.timestamp)} (age: {evidenceAge(latest.timestamp, now)});
          available {timestamp(latest.available_at)}; difference {pounds(latest.difference_gbp, true)}.
        </>}
        {" "}{history?.count ?? 0} differing {history?.count === 1 ? "observation" : "observations"} in current replayed history.
        {firstDifference && firstDifference !== latestDifference && <> Earliest difference: {pounds(firstDifference.difference_gbp, true)} at {timestamp(firstDifference.timestamp)};
          source {firstDifference.source}; evidence {firstDifference.evidence}.</>}
        {latestDifference && <> Latest difference: {pounds(latestDifference.difference_gbp, true)} at {timestamp(latestDifference.timestamp)};
          source {latestDifference.source}; evidence {latestDifference.evidence}.</>}
      </dd></div>;
    })}</dl>
  </section>;
}

function InvestmentNextSteps({ data, onReviewCashEvidence }: {
  data: OpeningSnapshot; onReviewCashEvidence: () => void;
}) {
  const unmarked = data.holdings.filter(holding => holding.mark_at === null);
  const hasDifferences = data.cash_observations.some(observation =>
    !new Decimal(observation.difference_gbp).isZero());
  const missingObservations = data.accounts.some(account => account.cash_observation === null);
  return <section className="history-group investment-next-steps" aria-label="Next steps">
    <h2>Next steps</h2>
    {data.source_mode === "demo" && <p><strong>Synthetic demonstration:</strong> these are illustrative records, not your accounts.</p>}
    <ul>
      <li>{data.complete
        ? "Recorded valuation is complete at its recorded clock; this does not establish a current value."
        : "A complete common-clock valuation is unavailable. Missing or later-available evidence is not zero."}
        {!data.complete && unmarked.length > 0 && <ul>{unmarked.slice(0, 5).map(holding =>
          <li key={`${holding.account_id}:${holding.product_id}`}>No recorded mark: {holding.name} ({holding.account_id}).</li>)}
          {unmarked.length > 5 && <li>And {unmarked.length - 5} more holdings without recorded marks.</li>}</ul>}
      </li>
      {data.baseline_as_of === null
        ? <li>The performance baseline is unavailable. A later-available mark cannot establish an earlier baseline;
          later evidence does not necessarily restore past returns.</li>
        : (data.twr_pct === null || data.max_drawdown_pct === null) &&
          <li>Time-weighted return or drawdown is unavailable; a complete recorded valuation does not restore missing history.</li>}
      <li>{hasDifferences
        ? "Earlier recorded cash differences need review. Later matching observations do not resolve prior differences; they are not corrections or proof of a current discrepancy."
        : data.cash_observations.length === 0
          ? "No cash observations have been recorded; this does not establish a current balance."
          : "Recorded cash observations do not independently verify current balances."}
        {missingObservations && data.cash_observations.length > 0 && " Some accounts have no recorded cash observation."}
        {" "}<a href="#investment-accounts" onClick={onReviewCashEvidence}>Review cash evidence</a>.
      </li>
      <li>Profile changes retain identities and prior evidence. A funded account addition needs a complete,
        contemporaneous snapshot and is unitization-neutral. Historical corrections replay downstream events,
        invalidate performance and never create a new baseline automatically.</li>
      <li>Unassigned roles are not policy or targets; attribution is optional and does not approve trades.</li>
      <li>Backup status is unknown to this screen. Git ignore is not access control, encryption or a backup.{" "}
        <NavLink to="/investment-guide">Backup and recovery guidance</NavLink>.</li>
      <li>Recording requires a separate server opt-in and a reviewed confirmation.
        {data.source_mode === "recorded" && <> <NavLink to="/investment/record">Open manual recording</NavLink>.</>}</li>
    </ul>
  </section>;
}

const ChartDecimal = Decimal.clone({ precision: 300 });

function intervalReturn(current: OpeningSnapshot["valuation_history"][number],
  previous: OpeningSnapshot["valuation_history"][number] | undefined) {
  if (!previous || !previous.complete || previous.twr_pct === null ||
      !current.complete || current.twr_pct === null) return null;
  const start = new ChartDecimal(previous.twr_pct).plus(100);
  const end = new ChartDecimal(current.twr_pct).plus(100);
  if (start.lte(0) || end.lt(0)) return null;
  return end.minus(start).div(start).mul(100).toString();
}

function InvestmentGrowthHistory({ history }: { history: OpeningSnapshot["valuation_history"] }) {
  const returnMaskId = `investment-return-gap-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
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
  const microseconds = (value: string) => {
    const fraction = value.match(/\.(\d{1,6})Z$/)?.[1] ?? "";
    return BigInt(Date.parse(value)) * 1000n + BigInt(fraction.padEnd(6, "0").slice(3));
  };
  const firstTime = chartHistory.length ? microseconds(chartHistory[0].timestamp) : 0n;
  const lastTime = chartHistory.length ? microseconds(chartHistory[chartHistory.length - 1].timestamp) : firstTime;
  const timeRange = lastTime > firstTime ? lastTime - firstTime : 1n;
  const left = 72, right = 780, top = 20, bottom = 220;
  const x = (point: OpeningSnapshot["valuation_history"][number]) =>
    left + Number(microseconds(point.timestamp) - firstTime) / Number(timeRange) * (right - left);
  const y = (value: string) =>
    top + new Decimal(maximum.minus(value)).div(range).toNumber() * (bottom - top);
  const returnKnown = chartHistory.filter(point => point.complete && point.twr_pct !== null);
  const returnValues = returnKnown.map(point => new ChartDecimal(point.twr_pct!));
  const returnLow = returnValues.length
    ? returnValues.slice(1).reduce((value, candidate) => ChartDecimal.min(value, candidate), returnValues[0])
    : new ChartDecimal(0);
  const returnHigh = returnValues.length
    ? returnValues.slice(1).reduce((value, candidate) => ChartDecimal.max(value, candidate), returnValues[0])
    : new ChartDecimal(0);
  const returnSpread = returnHigh.minus(returnLow);
  const returnPadding = returnSpread.isZero()
    ? ChartDecimal.max(returnHigh.abs().mul("0.05"), new ChartDecimal(1))
    : returnSpread.mul("0.1");
  const returnMinimum = returnLow.minus(returnPadding), returnMaximum = returnHigh.plus(returnPadding);
  const returnRange = returnMaximum.minus(returnMinimum);
  const returnY = (value: string) =>
    top + returnMaximum.minus(value).div(returnRange).toNumber() * (bottom - top);
  const lineSegments = (field: "equity_gbp" | "capital_basis_gbp" | "twr_pct",
    coordinate: (value: string) => number = y) => {
    const segments: string[][] = [];
    let segment: string[] = [];
    for (const point of chartHistory) {
      const value = point.complete ? point[field] : null;
      if (value === null) {
        if (segment.length) segments.push(segment);
        segment = [];
      } else {
        segment.push(`${x(point)},${coordinate(value)}`);
      }
    }
    if (segment.length) segments.push(segment);
    return segments;
  };
  const tableStart = Math.max(0, history.length - 20);
  const latest = history.slice(tableStart).map((point, index) => {
    const previous = history[tableStart + index - 1];
    return { point, previous, interval: intervalReturn(point, previous) };
  }).reverse();
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
        {returnKnown.length > 0 ? <figure className="history-chart">
          <figcaption>Time-weighted return since baseline <span>{returnKnown.length} available of {chartHistory.length} recent event clocks</span></figcaption>
          <p className="position-note">Flow-neutral recorded return, not a benchmark or a prediction. A complete value does not necessarily mean a return is available. Percentages are rounded for display; points near a gap may be masked. Full fractional UTC clocks are shown in the table.</p>
          <svg viewBox="0 0 800 260" role="img" aria-label="Time-weighted return since baseline; unavailable returns are gaps">
            <defs>
              <mask id={returnMaskId} maskUnits="userSpaceOnUse" x="0" y="0" width="800" height="260">
                <rect x="0" y="0" width="800" height="260" fill="white" />
                {chartHistory.filter(point => !point.complete || point.twr_pct === null).map(point =>
                  <rect key={point.timestamp} x={x(point) - 5} y={top} width="10" height={bottom - top} fill="black" />)}
              </mask>
            </defs>
            {[0, 1, 2, 3].map(index => {
              const value = returnMaximum.minus(returnRange.mul(index).div(3));
              const yAt = top + index / 3 * (bottom - top);
              return <g key={index}>
                <line x1={left} x2={right} y1={yAt} y2={yAt} className="chart-baseline" />
                <text x={left - 8} y={yAt + 4} textAnchor="end" className="chart-label">{percent(value.toString())}</text>
              </g>;
            })}
            <g mask={`url(#${returnMaskId})`}>
              {lineSegments("twr_pct", returnY).map((segment, index) =>
                segment.length > 1 && <polyline key={`return-${index}`} points={segment.join(" ")}
                  fill="none" stroke="#21745f" strokeWidth="2" />)}
              {returnKnown.map(point => <circle key={point.timestamp} cx={x(point)} cy={returnY(point.twr_pct!)} r="3" fill="#21745f">
                <title>{`${point.timestamp} · TWR ${percent(point.twr_pct)}`}</title>
              </circle>)}
            </g>
            {chartHistory.filter(point => !point.complete || point.twr_pct === null).map(point =>
              <g key={point.timestamp}>
                <line className="return-chart-gap" x1={x(point)} x2={x(point)} y1={top} y2={bottom}
                  stroke="#a63747" strokeDasharray="3 4" />
                <title>{`${point.timestamp} · ${point.gap_reason ?? "Time-weighted return unavailable."}`}</title>
              </g>)}
            <text x={left} y="246" className="chart-label">{dateLabel(chartHistory[0].timestamp)}</text>
            <text x={right} y="246" textAnchor="end" className="chart-label">{dateLabel(chartHistory[chartHistory.length - 1].timestamp)}</text>
          </svg>
          <div className="investment-chart-legend"><span><i className="investment-legend-value" />Recorded TWR</span><span><i className="investment-legend-gap" />Return unavailable</span></div>
        </figure> : <p>No time-weighted return is available in the recent history.</p>}
        <p className="position-note">The chart shows the latest {chartHistory.length} of {history.length} event clocks; the table shows the latest {latest.length}. Source-availability times and gaps are retained.</p>
        <p className="position-note">Return since previous clock compares consecutive recorded time-weighted returns, not calendar periods or benchmarks. It is unavailable when either clock lacks a return or the starting unit value is zero; no gap is bridged.</p>
        <div className="table-scroll" tabIndex={0} aria-label="Recorded growth history">
          <table>
            <thead><tr><th>Event clock (UTC)</th><th>Status and gap reason</th><th>Available at</th><th>Value</th><th>Capital basis</th><th>Gain since baseline</th><th>TWR</th><th>Return since previous clock</th><th>Maximum drawdown</th></tr></thead>
            <tbody>{latest.map(({ point, previous, interval }) => <tr key={point.timestamp}>
              <td>{point.timestamp.includes(".") ? point.timestamp : timestamp(point.timestamp)}</td><td>{point.complete ? "Complete" : `Gap: ${point.gap_reason}`}</td>
              <td>{timestamp(point.source_available_at)}</td><td>{pounds(point.equity_gbp)}</td>
              <td>{pounds(point.capital_basis_gbp)}</td><td>{pounds(point.gain_since_baseline_gbp)}</td>
              <td>{percent(point.twr_pct)}</td>
              <td>{percent(interval)}{interval !== null && previous && <div>From {previous.timestamp}</div>}</td>
              <td>{percent(point.max_drawdown_pct)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
  </section>;
}

type RecordingAccount = { id: string; name: string; kind: "broker" | "deposit" };
type RecordingProduct = { id: string; name: string; sleeve: "equity" | "bond";
  quantity_increment: string; minimum_notional_gbp: string };
type CorrectableEvent = {
  id: string; type: "contribution" | "withdrawal" | "transfer" | "interest" | "dividend" |
    "buy" | "sell" | "split" | "mark";
  account_id: string; currency: "GBP"; timestamp: string; available_at: string; evidence: string;
  amount_gbp?: string; destination_account_id?: string; product_id?: string;
  quantity?: string; price_gbp?: string; fee_gbp?: string; ratio?: string;
  role?: "core" | "dynamic" | "unassigned"; role_evidence?: string };
type RecordingSession = { enabled: boolean; csrf: string | null;
  opened: boolean; accounts: RecordingAccount[]; products: RecordingProduct[];
  latest_correctable_event: CorrectableEvent | null; correctable_events: CorrectableEvent[];
  current_balances: Record<string, string>;
  current_holdings: { account_id: string; product_id: string; quantity: string }[] };
type RecordingPreview = { preview_id: string; revision: number; event: Record<string, unknown>;
  before: { equity_gbp: string | null }; after: { equity_gbp: string | null };
  expires_at: string };
type OpeningHoldingInput = { account_id: string; product_id: string; quantity: string; price_gbp: string };
type ScopeHoldingInput = OpeningHoldingInput & { existing: boolean };

const RECORD_REVIEW_FIELDS = [
  ["action", "Action"], ["target", "Identity type"], ["account_id", "Account"],
  ["destination_account_id", "Destination account"], ["product_id", "Product"],
  ["entity_id", "Identity"], ["role", "Management role"], ["amount_gbp", "Amount"],
  ["balance_gbp", "Observed balance"], ["quantity", "Quantity"],
  ["price_gbp", "Price per unit"], ["fee_gbp", "Recorded fee"], ["ratio", "Split ratio"],
  ["currency", "Currency"], ["name", "Name"], ["source", "Source"],
  ["timestamp", "Effective / recorded at"], ["available_at", "Evidence available at"],
  ["evidence", "Evidence description"],
] as const;

function recordReviewValue(key: string, value: unknown, event: Record<string, unknown>,
  session: RecordingSession): string {
  if (typeof value !== "string" && typeof value !== "number") return "Unavailable";
  const text = String(value);
  if (key === "account_id" || key === "destination_account_id") {
    const account = session.accounts.find(item => item.id === text);
    return account ? `${account.name} (${account.id})` : text;
  }
  if (key === "product_id") {
    const product = session.products.find(item => item.id === text);
    return product ? `${product.name} (${product.id})` : text;
  }
  if (key === "entity_id") {
    const collection = event.target === "account" ? session.accounts : session.products;
    const item = collection.find(candidate => candidate.id === text);
    return item ? `${item.name} (${item.id})` : text;
  }
  if (key === "amount_gbp" || key === "balance_gbp" || key === "price_gbp" || key === "fee_gbp")
    return pounds(text, true);
  if (key === "timestamp" || key === "available_at") return timestamp(text);
  if (key === "currency") return text.toUpperCase();
  if (key === "action" || key === "target" || key === "role")
    return label(text);
  if (key === "quantity") return `${text} units`;
  return text;
}

function RecordReview({ preview, session }: { preview: RecordingPreview; session: RecordingSession }) {
  const event = preview.event;
  const kind = typeof event.type === "string" ? event.type : "record";
  const fields = RECORD_REVIEW_FIELDS.filter(([key]) => event[key] !== undefined);
  const balances = typeof event.balances_gbp === "object" && event.balances_gbp !== null
    ? event.balances_gbp as Record<string, unknown> : null;
  const holdings = Array.isArray(event.holdings)
    ? event.holdings.filter((item): item is Record<string, unknown> =>
      typeof item === "object" && item !== null) : [];
  const replacement = typeof event.replacement_event === "object" && event.replacement_event !== null
    ? event.replacement_event as Record<string, unknown> : null;
  const replacementFields = replacement
    ? RECORD_REVIEW_FIELDS.filter(([key]) => replacement[key] !== undefined) : [];
  const newAccount = typeof event.account === "object" && event.account !== null
    ? event.account as Record<string, unknown> : null;
  const newProduct = typeof event.product === "object" && event.product !== null
    ? event.product as Record<string, unknown> : null;
  return <section className="record-review" aria-label="Record summary">
    <h4>{label(kind)} — server-validated preview</h4>
    <p>Journal revision {preview.revision}; preview expires {timestamp(preview.expires_at)}.
      The summary below is from the exact event shown in the expandable details.</p>
    <dl>{fields.map(([key, title]) => <div key={key}><dt>{title}</dt>
      <dd>{recordReviewValue(key, event[key], event, session)}</dd></div>)}</dl>
    {replacement && <section aria-label="Correction replacement details">
      <h5>Replacement details</h5>
      <p>Corrects an earlier {typeof replacement.type === "string" ? label(replacement.type) : "journal"}
        {" "}record at {typeof replacement.timestamp === "string" ? timestamp(replacement.timestamp) : "an unavailable clock"}.
        Original evidence remains in the journal.</p>
      <dl>{replacementFields.map(([key, title]) => <div key={key}><dt>{title}</dt>
        <dd>{recordReviewValue(key, replacement[key], replacement, session)}</dd></div>)}</dl>
    </section>}
    {newAccount && <p>Account added to scope: {String(newAccount.name ?? "Name unavailable")}
      {" "}({String(newAccount.id ?? "ID unavailable")}), {String(newAccount.kind ?? "classification unavailable")}.</p>}
    {newProduct && <p>Product added: {String(newProduct.name ?? "Name unavailable")}
      {" "}({String(newProduct.id ?? "ID unavailable")}), {String(newProduct.sleeve ?? "classification unavailable")}.</p>}
    {balances && <section aria-label="Opening cash by account"><h5>Cash included in this record</h5><dl>
      {Object.entries(balances).map(([id, value]) => {
        const account = session.accounts.find(item => item.id === id);
        return <div key={id}><dt>{account ? `${account.name} (${id})` : id}</dt>
          <dd>{typeof value === "string" ? pounds(value, true) : "Unavailable"}</dd></div>;
      })}
    </dl></section>}
    {holdings.length > 0 && <section aria-label="Holdings included in this record"><h5>Holdings included</h5><ul>
      {holdings.map((holding, index) => {
        const account = session.accounts.find(item => item.id === holding.account_id);
        const product = session.products.find(item => item.id === holding.product_id);
        const priceValue = holding.price_gbp;
        return <li key={`${String(holding.account_id)}:${String(holding.product_id)}:${index}`}>
          {account?.name ?? String(holding.account_id ?? "Account unavailable")} / {product?.name ?? String(holding.product_id ?? "Product unavailable")}:
          {" "}{String(holding.quantity ?? "Quantity unavailable")} units; price {typeof priceValue === "string" ? pounds(priceValue, true) : "unknown — not zero"}.
        </li>;
      })}
    </ul></section>}
    <details className="recording-event">
      <summary>Exact event and evidence being appended</summary>
      <pre>{JSON.stringify(event, null, 2)}</pre>
    </details>
  </section>;
}

function localDateTime() {
  const value = new Date();
  value.setMinutes(value.getMinutes() - value.getTimezoneOffset());
  return value.toISOString().slice(0, 16);
}

function eventClock(value: string) {
  const parsed = new Date(value);
  if (!value || Number.isNaN(parsed.getTime())) throw new Error("Enter a valid local date and time.");
  return parsed.toISOString();
}

function openDisclosure(id: string) {
  const section = document.getElementById(id);
  if (section instanceof HTMLDetailsElement) section.open = true;
}

async function recordingRequest<T>(path: string, csrf?: string, payload?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store",
    headers: payload === undefined ? undefined : {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const code = typeof body === "object" && body !== null && "error" in body
      ? String(body.error) : "recording_request_failed";
    throw new Error(code);
  }
  return body as T;
}

function InvestmentRecording() {
  const [session, setSession] = useState<RecordingSession | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kind, setKind] = useState("opening");
  const [role, setRole] = useState<"core" | "dynamic" | "unassigned">("unassigned");
  const [assignmentAsset, setAssignmentAsset] = useState<"cash" | "holding">("cash");
  const [accountId, setAccountId] = useState("");
  const [destinationAccountId, setDestinationAccountId] = useState("");
  const [productId, setProductId] = useState("");
  const [amount, setAmount] = useState("");
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [fee, setFee] = useState("0");
  const [source, setSource] = useState("");
  const [evidence, setEvidence] = useState("");
  const [roleEvidence, setRoleEvidence] = useState("");
  const [effectiveAt, setEffectiveAt] = useState(localDateTime);
  const [availableAt, setAvailableAt] = useState(localDateTime);
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [openingHoldings, setOpeningHoldings] = useState<OpeningHoldingInput[]>([]);
  const [correctionEventId, setCorrectionEventId] = useState("");
  const [profileAction, setProfileAction] = useState<"add_product" | "rename" | "retire">("add_product");
  const [profileTarget, setProfileTarget] = useState<"account" | "product">("product");
  const [profileEntityId, setProfileEntityId] = useState("");
  const [profileName, setProfileName] = useState("");
  const [newProductId, setNewProductId] = useState("");
  const [newProductSleeve, setNewProductSleeve] = useState<"equity" | "bond">("equity");
  const [newProductIncrement, setNewProductIncrement] = useState("1");
  const [newProductMinimum, setNewProductMinimum] = useState("1");
  const [newAccountId, setNewAccountId] = useState("");
  const [newAccountName, setNewAccountName] = useState("");
  const [newAccountBinding, setNewAccountBinding] = useState("");
  const [newAccountKind, setNewAccountKind] = useState<"broker" | "deposit">("broker");
  const [newAccountCash, setNewAccountCash] = useState("");
  const [scopeBalances, setScopeBalances] = useState<Record<string, string>>({});
  const [scopeHoldings, setScopeHoldings] = useState<ScopeHoldingInput[]>([]);
  const [ratio, setRatio] = useState("");
  const [preview, setPreview] = useState<RecordingPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function refreshSession() {
    try {
      const result = await recordingRequest<RecordingSession>(
        "/api/investment/v1/recording/session");
      setSession(result);
      setLoadError(null);
      if (result.accounts.length && !accountId) setAccountId(result.accounts[0].id);
      if (result.products.length && !productId) setProductId(result.products[0].id);
      if (result.accounts.length > 1 && !destinationAccountId)
        setDestinationAccountId(result.accounts[1].id);
      setBalances(current => Object.keys(current).length ? current :
        Object.fromEntries(result.accounts.map(account => [account.id, ""])));
      setScopeBalances(current => Object.keys(current).length ? current :
        Object.fromEntries(result.accounts.map(account => [account.id, ""])));
      setScopeHoldings(current => current.length ? current : result.current_holdings.map(holding => ({
        ...holding, price_gbp: "", existing: true,
      })));
      if (result.correctable_events.length && !correctionEventId)
        setCorrectionEventId(result.correctable_events[result.correctable_events.length - 1].id);
      if (result.opened) setKind(current => current === "opening" ? "contribution" : current);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "recording_session_unavailable");
    }
  }

  useEffect(() => { void refreshSession(); }, []);

  async function makePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.csrf) return;
    setBusy(true);
    setMessage(null);
    try {
      const id = crypto.randomUUID();
      const timestamp = eventClock(effectiveAt);
      const available = kind === "profile_change" || kind === "scope_addition"
        ? timestamp : kind === "correction"
          ? new Date(Math.max(Date.parse(eventClock(availableAt)), Date.parse(timestamp))).toISOString()
          : eventClock(availableAt);
      const common = { id, timestamp, available_at: available, evidence };
      let value: Record<string, unknown>;
      if (kind === "opening") {
        const holdings = openingHoldings.filter(item =>
          item.account_id && item.product_id && item.quantity).map(item => ({
            ...item, price_gbp: item.price_gbp === "" ? null : item.price_gbp,
          }));
        value = { ...common, type: "opening", balances_gbp: balances, holdings };
      } else if (kind === "correction") {
        const original = session.correctable_events.find(item => item.id === correctionEventId);
        if (!original) throw new Error("Select an eligible recorded event to correct.");
        const replacement: Record<string, unknown> = {
          ...original, id: crypto.randomUUID(), available_at: available, evidence,
        };
        if (["contribution", "withdrawal", "interest", "dividend", "transfer"].includes(original.type))
          replacement.amount_gbp = amount;
        if (original.type === "transfer") replacement.destination_account_id = destinationAccountId;
        if (["buy", "sell"].includes(original.type)) {
          replacement.product_id = productId;
          replacement.quantity = quantity;
          replacement.price_gbp = price;
          replacement.fee_gbp = fee;
        }
        if (original.type === "split") {
          replacement.product_id = productId;
          replacement.ratio = ratio;
        }
        if (original.type === "mark") {
          replacement.product_id = productId;
          replacement.price_gbp = price;
        }
        value = { ...common, type: "correction",
          original_event_id: original.id, replacement_event: replacement };
      } else if (kind === "profile_change") {
        if (profileAction === "add_product") {
          value = { ...common, type: kind, action: profileAction,
            product: { id: newProductId, name: profileName, sleeve: newProductSleeve,
              currency: "GBP", quantity_increment: newProductIncrement,
              minimum_notional_gbp: newProductMinimum } };
        } else if (profileAction === "rename") {
          value = { ...common, type: kind, action: profileAction,
            target: profileTarget, entity_id: profileEntityId, name: profileName };
        } else {
          value = { ...common, type: kind, action: profileAction,
            target: profileTarget, entity_id: profileEntityId };
        }
      } else if (kind === "scope_addition") {
        const account = { id: newAccountId, name: newAccountName,
          kind: newAccountKind, binding: newAccountBinding, currency: "GBP" };
        const holdings = scopeHoldings.filter(item =>
          item.account_id && item.product_id && item.quantity && item.price_gbp)
          .map(({ existing: _existing, ...item }) => item);
        value = { ...common, type: kind, account,
          balances_gbp: { ...scopeBalances, [newAccountId]: newAccountCash }, holdings };
      } else if (kind === "cash_observation") {
        value = { ...common, type: kind, account_id: accountId, balance_gbp: amount, source };
      } else if (kind === "role_assignment") {
        if (role === "unassigned") throw new Error("Choose core or dynamic for this attribution.");
        value = { ...common, type: kind, account_id: accountId, role,
          product_id: assignmentAsset === "holding" ? productId : null, amount };
      } else {
        value = { ...common, type: kind, account_id: accountId, currency: "GBP" };
        if (role !== "unassigned" &&
            ["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell"].includes(kind))
          value.role = role;
        if (kind === "interest" || kind === "dividend") value.role_evidence = roleEvidence;
        if (["contribution", "withdrawal", "interest", "dividend", "transfer"].includes(kind))
          value.amount_gbp = amount;
        if (kind === "transfer") value.destination_account_id = destinationAccountId;
        if (kind === "dividend" || ["buy", "sell", "mark"].includes(kind))
          value.product_id = productId;
        if (kind === "buy" || kind === "sell") {
          value.quantity = quantity;
          value.price_gbp = price;
          value.fee_gbp = fee;
        }
        if (kind === "mark") value.price_gbp = price;
        if (kind === "split") value.ratio = ratio;
      }
      const result = await recordingRequest<RecordingPreview>(
        "/api/investment/v1/recording/preview", session.csrf,
        { request_id: crypto.randomUUID(), event: value });
      setPreview(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "preview_rejected");
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmPreview() {
    if (!session?.csrf || !preview) return;
    setBusy(true);
    setMessage(null);
    try {
      await recordingRequest("/api/investment/v1/recording/confirm", session.csrf,
        { preview_id: preview.preview_id });
      setPreview(null);
      setMessage("Record committed. Refreshing the recorded portfolio.");
      window.location.reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "confirmation_rejected");
    } finally {
      setBusy(false);
    }
  }

  if (loadError) return <section className="history-group" aria-label="Manual recording">
    <h2>Manual portfolio records</h2><p role="alert">Recording controls unavailable: {loadError}</p>
  </section>;
  if (!session) return <section className="history-group" aria-label="Manual recording">
    <h2>Manual portfolio records</h2><p role="status">Checking recording controls.</p>
  </section>;
  if (!session.enabled) return <section className="history-group" aria-label="Manual recording">
    <h2>Recording is disabled</h2>
    <p>Recording requires an explicitly selected journal and local opt-in. This page cannot connect to a bank or broker, place orders, or move money.</p>
    <p>Stop the dashboard and restart with <code>--investment-recording</code>. For a source checkout's default workspace,
      run <code>python -m dashboard --investment-recording</code>. If you selected a different workspace or journal,
      retain your original --workspace or --investment-database selection when restarting. Installed use outside
      a checkout requires an explicit workspace or journal selection.</p>
  </section>;
  const opened = session.opened;
  const correction = session.correctable_events.find(item => item.id === correctionEventId) ?? null;
  const recordType = kind === "correction" ? correction?.type : kind;
  const availableKinds = opened
    ? ["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell", "split",
      "mark", "cash_observation", "role_assignment", "profile_change", "scope_addition",
      ...(session.correctable_events.length ? ["correction"] : [])]
    : ["opening"];
  const selectedProduct = session.products.find(product => product.id === productId);

  return <section className="history-group investment-recording" aria-label="Manual recording">
    <h2>Manual portfolio recording</h2>
    <p>Explicit operator records only. Evidence and balances are not independently verified; this cannot place orders or move money.</p>
    {preview ? <div className="recording-preview" role="group" aria-label="Review record before confirmation">
      <h3>Review before recording</h3>
      <dl className="health-facts">
        <div><dt>Current recorded value</dt><dd>{pounds(preview.before.equity_gbp)}</dd></div>
        <div><dt>Value after this record</dt><dd>{pounds(preview.after.equity_gbp)}</dd></div>
      </dl>
      <RecordReview preview={preview} session={session} />
      <p className="position-note">Only confirm if the event, account, amount, evidence and before/after values match the source you reviewed.</p>
      <button type="button" disabled={busy} onClick={() => void confirmPreview()}>Confirm and append record</button>
      <button type="button" disabled={busy} onClick={() => setPreview(null)}>Cancel preview</button>
    </div> : <form className="recording-form" onSubmit={makePreview}>
      {opened && <label>What are you recording?<select value={kind} onChange={event => setKind(event.target.value)}>
        <optgroup label="Cash and income">
          {["contribution", "withdrawal", "transfer", "interest", "dividend", "cash_observation"]
            .filter(value => availableKinds.includes(value)).map(value =>
              <option key={value} value={value}>{label(value)}</option>)}
        </optgroup>
        <optgroup label="Holdings and attribution">
          {["buy", "sell", "mark", "split", "role_assignment"]
            .filter(value => availableKinds.includes(value)).map(value =>
              <option key={value} value={value}>{label(value)}</option>)}
        </optgroup>
        <optgroup label="Journal scope">
          {["profile_change", "scope_addition"].filter(value => availableKinds.includes(value)).map(value =>
            <option key={value} value={value}>{label(value)}</option>)}
        </optgroup>
        {availableKinds.includes("correction") && <optgroup label="Evidence correction">
          <option value="correction">Correct an eligible earlier record</option>
        </optgroup>}
      </select></label>}
      {kind === "correction" && <label>Event to correct<select required value={correctionEventId}
        onChange={event => {
          setCorrectionEventId(event.target.value);
          const item = session.correctable_events.find(candidate => candidate.id === event.target.value);
          if (item?.amount_gbp) setAmount(item.amount_gbp);
          if (item?.quantity) setQuantity(item.quantity);
          if (item?.price_gbp) setPrice(item.price_gbp);
          if (item?.fee_gbp) setFee(item.fee_gbp);
          if (item?.ratio) setRatio(item.ratio);
          if (item?.product_id) setProductId(item.product_id);
          if (item?.account_id) setAccountId(item.account_id);
          if (item?.destination_account_id) setDestinationAccountId(item.destination_account_id);
        }}>
        {session.correctable_events.map(item => <option key={item.id} value={item.id}>
          {label(item.type)} · {item.id} · {timestamp(item.timestamp)}
        </option>)}
      </select></label>}
      {["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell", "role_assignment"].includes(kind) &&
        <label>Management role<select value={role} onChange={event =>
          setRole(event.target.value as "core" | "dynamic" | "unassigned")}>
          <option value="unassigned">Unassigned</option><option value="core">Core</option>
          <option value="dynamic">Dynamic</option>
        </select></label>}
      {kind === "role_assignment" && <label>Attribution asset<select value={assignmentAsset}
        onChange={event => setAssignmentAsset(event.target.value as "cash" | "holding")}>
        <option value="cash">Cash</option><option value="holding">Holding quantity</option>
      </select></label>}
      {kind === "opening" && <>
        <fieldset><legend>Opening cash balance at the same evidence clock</legend>
          {session.accounts.map(account => <label key={account.id}>{account.name} ({label(account.kind)}) — GBP
            <input required inputMode="decimal" value={balances[account.id] ?? ""}
              onChange={event => setBalances(current => ({ ...current, [account.id]: event.target.value }))} />
          </label>)}
        </fieldset>
        <fieldset><legend>Existing long fund holdings (optional)</legend>
          {openingHoldings.map((holding, index) => <div className="recording-row" key={index}>
            <label>Account<select required value={holding.account_id}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, account_id: event.target.value } : item))}>
              <option value="">Select</option>{session.accounts.filter(account => account.kind === "broker")
                .map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
            </select></label>
            <label>Fund<select required value={holding.product_id}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, product_id: event.target.value } : item))}>
              <option value="">Select</option>{session.products.map(product =>
                <option key={product.id} value={product.id}>{product.name} ({product.id})</option>)}
            </select></label>
            <label>Quantity<input required inputMode="decimal" value={holding.quantity}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, quantity: event.target.value } : item))} /></label>
            <label>GBP price per unit (leave blank if unknown)<input inputMode="decimal" value={holding.price_gbp}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, price_gbp: event.target.value } : item))} /></label>
            <button type="button" onClick={() => setOpeningHoldings(current =>
              current.filter((_, row) => row !== index))}>Remove holding</button>
          </div>)}
          <button type="button" onClick={() => setOpeningHoldings(current => [
            ...current, { account_id: "", product_id: "", quantity: "", price_gbp: "" },
          ])}>Add existing holding</button>
        </fieldset>
      </>}
      {kind === "profile_change" && <>
        <label>Profile change<select value={profileAction} onChange={event =>
          setProfileAction(event.target.value as "add_product" | "rename" | "retire")}>
          <option value="add_product">Add product</option>
          <option value="rename">Rename existing account or product</option>
          <option value="retire">Retire account or product</option>
        </select></label>
        {profileAction !== "add_product" && <>
          <label>Identity type<select value={profileTarget} onChange={event =>
            setProfileTarget(event.target.value as "account" | "product")}>
            <option value="account">Account</option><option value="product">Product</option>
          </select></label>
          <label>Identity<select required value={profileEntityId}
            onChange={event => setProfileEntityId(event.target.value)}>
            <option value="">Select</option>
            {(profileTarget === "account" ? session.accounts : session.products).map(item =>
              <option key={item.id} value={item.id}>{item.name} ({item.id})</option>)}
          </select></label>
        </>}
        {profileAction === "add_product" && <label>New product ID<input required maxLength={200}
          value={newProductId} onChange={event => setNewProductId(event.target.value)} /></label>}
        {profileAction !== "retire" && <label>{profileAction === "add_product" ? "Product name" : "New name"}
          <input required maxLength={200} value={profileName}
            onChange={event => setProfileName(event.target.value)} /></label>}
        {profileAction === "add_product" && <>
          <label>Asset classification<select value={newProductSleeve} onChange={event =>
            setNewProductSleeve(event.target.value as "equity" | "bond")}>
            <option value="equity">Equity</option><option value="bond">Bond</option>
          </select></label>
          <label>Quantity increment<input required inputMode="decimal" value={newProductIncrement}
            onChange={event => setNewProductIncrement(event.target.value)} /></label>
          <label>Minimum notional in GBP<input required inputMode="decimal" value={newProductMinimum}
            onChange={event => setNewProductMinimum(event.target.value)} /></label>
        </>}
        <p className="position-note">Identity, account binding, currency, product classification and quantity rules remain immutable. Retired identities and evidence are retained.</p>
      </>}
      {kind === "scope_addition" && <>
        <label>New account ID<input required maxLength={200} value={newAccountId}
          onChange={event => setNewAccountId(event.target.value)} /></label>
        <label>Account name<input required maxLength={200} value={newAccountName}
          onChange={event => setNewAccountName(event.target.value)} /></label>
        <label>Account binding alias<input required maxLength={200} value={newAccountBinding}
          onChange={event => setNewAccountBinding(event.target.value)} /></label>
        <label>Account type<select value={newAccountKind} onChange={event =>
          {
            const next = event.target.value as "broker" | "deposit";
            setNewAccountKind(next);
            if (next === "deposit")
              setScopeHoldings(current => current.filter(holding => holding.existing));
          }}>
          <option value="broker">Broker</option><option value="deposit">Deposit</option>
        </select></label>
        <fieldset><legend>Complete contemporaneous GBP balances</legend>
          {session.accounts.map(account => <label key={account.id}>{account.name} ({account.id})
            <input required inputMode="decimal" value={scopeBalances[account.id] ?? ""}
              onChange={event => setScopeBalances(current => ({
                ...current, [account.id]: event.target.value,
              }))} />
          </label>)}
          <label>{newAccountName || "New account"} balance
            <input required inputMode="decimal" value={newAccountCash}
              onChange={event => setNewAccountCash(event.target.value)} />
          </label>
        </fieldset>
        <fieldset><legend>All existing and new long holdings with prices at the same clock</legend>
          {scopeHoldings.map((holding, index) => <div className="recording-row" key={`${holding.account_id}-${holding.product_id}-${index}`}>
            <label>Account<select required disabled={holding.existing} value={holding.account_id}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, account_id: event.target.value } : item))}>
              {session.accounts.filter(account => account.kind === "broker").map(account =>
                <option key={account.id} value={account.id}>{account.name}</option>)}
              {newAccountId && newAccountKind === "broker" &&
                <option value={newAccountId}>{newAccountName || newAccountId}</option>}
            </select></label>
            <label>Fund<select required disabled={holding.existing} value={holding.product_id}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, product_id: event.target.value } : item))}>
              {session.products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}
            </select></label>
            <label>Quantity<input required readOnly={holding.existing} inputMode="decimal"
              value={holding.quantity} onChange={event => setScopeHoldings(current =>
                current.map((item, row) => row === index ? { ...item, quantity: event.target.value } : item))} /></label>
            <label>GBP price per unit<input required inputMode="decimal" value={holding.price_gbp}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, price_gbp: event.target.value } : item))} /></label>
            {!holding.existing && <button type="button" onClick={() =>
              setScopeHoldings(current => current.filter((_, row) => row !== index))}>Remove holding</button>}
          </div>)}
          {newAccountKind === "broker" && <button type="button" disabled={!newAccountId || !session.products.length}
            onClick={() => setScopeHoldings(current => [
            ...current, { account_id: newAccountId, product_id: session.products[0]?.id ?? "",
              quantity: "", price_gbp: "", existing: false },
          ])}>Add new-account holding</button>}
        </fieldset>
        <p className="position-note">This is a neutral scope addition, not a contribution. Enter every included account balance and every held-product quantity and price from a complete, common-clock source. Evidence availability must match the effective clock, and a valid existing performance baseline is required.</p>
      </>}
      {kind !== "opening" && kind !== "correction" && <label>Account<select required value={accountId}
        onChange={event => setAccountId(event.target.value)}>
        {session.accounts.map(account => <option key={account.id} value={account.id}>{account.name} ({label(account.kind)})</option>)}
      </select></label>}
      {(kind === "transfer" || kind === "correction" && correction?.type === "transfer") &&
        <label>Destination account<select required value={destinationAccountId}
        onChange={event => setDestinationAccountId(event.target.value)}>
        {session.accounts.filter(account => account.id !== accountId)
          .map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select></label>}
      {(["dividend", "buy", "sell", "mark"].includes(kind) ||
        kind === "correction" && ["dividend", "buy", "sell", "mark", "split"].includes(recordType ?? "") ||
        kind === "role_assignment" && assignmentAsset === "holding") && <label>Fund<select required value={productId}
        onChange={event => setProductId(event.target.value)}>
        {session.products.map(product => <option key={product.id} value={product.id}>{product.name} ({product.id})</option>)}
      </select></label>}
      {(["contribution", "withdrawal", "interest", "dividend", "transfer", "cash_observation", "role_assignment"]
        .includes(kind) || kind === "correction" &&
        ["contribution", "withdrawal", "interest", "dividend", "transfer"].includes(recordType ?? "")) &&
        <label>{kind === "cash_observation" ? "Observed GBP balance" :
          kind === "correction" ? "Corrected GBP amount" :
          kind === "role_assignment" && assignmentAsset === "holding" ? "Holding quantity to attribute" : "GBP amount"}
          <input required inputMode="decimal"
            value={amount}
            onChange={event => setAmount(event.target.value)} />
        </label>}
      {kind === "cash_observation" && <label>Statement/source description<input required maxLength={200}
        value={source} onChange={event => setSource(event.target.value)} /></label>}
      {["interest", "dividend"].includes(kind) && <label>Evidence for this role's income allocation
        <textarea required maxLength={200} value={roleEvidence}
          onChange={event => setRoleEvidence(event.target.value)} /></label>}
      {["buy", "sell"].includes(kind) ||
        kind === "correction" && ["buy", "sell"].includes(recordType ?? "") ? <>
        <label>Quantity<input required inputMode="decimal" value={quantity}
          onChange={event => setQuantity(event.target.value)} /></label>
        <label>GBP price per unit<input required inputMode="decimal" value={price}
          onChange={event => setPrice(event.target.value)} /></label>
        <label>Recorded fee in GBP<input required inputMode="decimal" value={fee}
          onChange={event => setFee(event.target.value)} /></label>
        {selectedProduct && <small>Quantity increment: {selectedProduct.quantity_increment}</small>}
      </> : null}
      {(kind === "mark" || kind === "correction" && correction?.type === "mark") &&
        <label>GBP price per unit<input required inputMode="decimal" value={price}
        onChange={event => setPrice(event.target.value)} /></label>}
      {(kind === "split" || kind === "correction" && recordType === "split") &&
        <label>Split ratio<input required inputMode="decimal" value={ratio}
          onChange={event => setRatio(event.target.value)} /></label>}
      {(kind !== "profile_change" && kind !== "scope_addition") && <label>
        {kind === "correction" ? "Correction recorded at (your local timezone)" : "Effective time (your local timezone)"}
        <input required type="datetime-local" value={effectiveAt}
          onChange={event => setEffectiveAt(event.target.value)} />
      </label>}
      {(kind === "profile_change" || kind === "scope_addition") && <>
        <label>Evidence clock (effective and available at, your local timezone)
          <input required type="datetime-local" value={effectiveAt}
            onChange={event => setEffectiveAt(event.target.value)} />
        </label>
        <p className="position-note">Effective time and source-availability time are recorded as the same clock.</p>
      </>}
      {kind === "correction" && correction && <p className="position-note">
        Correcting {label(correction.type)} {correction.id} from {timestamp(correction.timestamp)}. The original record and availability remain in the journal. This correction will be appended now, replayed through downstream events, and makes historical performance unavailable; no new baseline is created.
      </p>}
      {kind !== "profile_change" && kind !== "scope_addition" && <label>Evidence available at (your local timezone)
        <input required type="datetime-local" value={availableAt}
          onChange={event => setAvailableAt(event.target.value)} />
      </label>}
      <label>Source evidence / correction reason<textarea required maxLength={200}
        value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      <button type="submit" disabled={busy}>Preview record</button>
    </form>}
    {message && <p role="status">{message}</p>}
  </section>;
}

export function InvestmentView({ now }: { now: number }) {
  const query = useQuery({ queryKey: ["investment"], queryFn: ({ signal }) => loadInvestment(signal), refetchInterval: 60000 });
  const accountsRef = useRef<HTMLDetailsElement>(null);
  const data = query.isError ? undefined : query.data;
  return <article className="investment-view">
    <div className="record-toolbar">
      <NavLink className="text-link" to="/investment-guide">Investment guide</NavLink>
      {data?.source_mode === "recorded" && <NavLink className="record-action" to="/investment/record">Record evidence</NavLink>}
      <button className="icon-button refresh" aria-label="Refresh investment" disabled={query.isFetching} onClick={() => void query.refetch()}>
        <RefreshCw size={19} className={query.isFetching ? "spin" : ""} />
      </button>
    </div>
    {query.isPending ? <p role="status">Loading investment records</p>
      : !data ? <div className="error-state" role="alert"><ShieldAlert size={30} /><h2>Investment data unavailable</h2>
        <p>{query.error instanceof Error ? query.error.message : "The investment source could not be reached."}</p></div>
        : <>
          <div className="demo-band"><span>{data.source_mode === "demo" ? "SYNTHETIC INVESTMENT DEMO" : "RECORDED INVESTMENT"}</span>
            <span>{data.source_mode === "demo"
              ? "Illustrative evidence and roles only; no targets, policy, or investment recommendation."
              : "Operator-supplied evidence; recording requires opt-in."}</span>
            <span>Execution disabled</span></div>
          <section id="investment-overview" aria-label="Valuation evidence">
            <p className="position-note">Valuation as of {timestamp(data.as_of)} ({evidenceAge(data.as_of, now)}{data.as_of !== null && Date.parse(data.as_of) <= now ? " ago" : ""}). Revision {data.revision}. {data.complete ? "Complete at the recorded clock." : "Incomplete valuation; no complete portfolio total."} Recorded inputs are not current broker verification.</p>
            <p className="position-note">Ages use the browser clock. No freshness threshold or current valuation is inferred.</p>
          </section>
          <section className="metrics" aria-label="Investment totals">
            <div><span>Portfolio value</span><strong>{pounds(data.equity_gbp)}</strong><small>GBP, recorded valuation</small></div>
            <div><span>Net contributions</span><strong>{pounds(data.net_contributions_gbp)}</strong><small>Deposits are not profit</small></div>
            <div><span>Gain since baseline</span><strong>{pounds(data.since_baseline_gain_gbp)}</strong><small>Net of recorded fees</small></div>
          </section>
          <nav className="investment-sections" aria-label="Investment sections">
            <div><span>Portfolio</span><a href="#investment-roles">Roles</a><a href="#investment-performance">Performance</a></div>
            <div><span>Review</span><a href="#investment-monitoring">Monitoring</a>
              <a href="#investment-accounts" onClick={() => openDisclosure("investment-accounts")}>Accounts and cash</a>
              <a href="#investment-records" onClick={() => openDisclosure("investment-records")}>Journal history</a></div>
            <div><span>More</span><a href="#investment-awareness" onClick={() => openDisclosure("investment-awareness")}>Awareness</a>
              <a href="#investment-policy" onClick={() => openDisclosure("investment-policy")}>Proposal and limits</a></div>
          </nav>
          <section className="investment-boundary" aria-label="Proposal boundary">
            <ShieldAlert size={18} />
            <p><strong>{data.proposal.status === "blocked" ? "Proposals are blocked." : "Proposal is review-only."}</strong> Recorded balances and illustrative role attribution are not approved targets or trade authority.</p>
          </section>
          <InvestmentNextSteps data={data} onReviewCashEvidence={() => {
            if (accountsRef.current) accountsRef.current.open = true;
          }} />
          {data.warnings.length > 0 && <section className="investment-warnings" role="status"><h2>Evidence and valuation warnings</h2><ul>{data.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul></section>}
          <section className="history-group" id="investment-roles" aria-label="Management role accounting">
            <h2>Management roles</h2>
            <p className="position-note">{data.source_mode === "demo"
              ? "Synthetic role attribution is shown only to demonstrate accounting. It is not a target, approved allocation, or recommendation."
              : "Roles are operator-attributed independently of account and asset class."} Unassigned funds are not assumed to be core or dynamic. Role-to-role transfers and proposals are unavailable.</p>
            <p className="position-note">Recorded role balances reflect the latest journal, not necessarily the portfolio valuation clock. Latest role change: {timestamp(data.role_accounting_at, "None")}.</p>
            <div className="table-scroll" tabIndex={0}><table>
              <thead><tr><th>Role</th><th>Cash</th><th>Recorded value</th></tr></thead>
              <tbody>{data.role_accounting.map(item => <tr key={item.role}><td>{label(item.role)}</td>
                <td>{pounds(item.cash_gbp)}</td><td>{pounds(item.value_gbp)}</td></tr>)}</tbody>
            </table></div>
            {data.role_accounting.map(item => <details key={item.role}>
              <summary>{label(item.role)} by account and holding</summary>
              <ul>
                {item.accounts.map(account => <li key={account.account_id}>
                  {account.account_id}: {pounds(account.cash_gbp)} recorded cash
                </li>)}
                {item.holdings.map(holding => <li key={`${holding.account_id}:${holding.product_id}`}>
                  {holding.account_id} · {holding.product_id}: {holding.quantity} units
                </li>)}
              </ul>
            </details>)}
            {data.role_assignments.length > 0 && <details className="investment-detail">
              <summary>Role attribution history ({data.role_assignments.length})</summary>
              <div className="table-scroll" tabIndex={0} aria-label="Role attribution history">
              <table><thead><tr><th>Attribution clock</th><th>Role</th><th>Account</th><th>Asset</th><th>Amount / quantity</th><th>Evidence</th></tr></thead>
                <tbody>{data.role_assignments.slice(-100).map(item => <tr key={item.id}>
                  <td>{timestamp(item.timestamp)}</td><td>{label(item.role)}</td><td>{item.account_id}</td>
                  <td>{item.product_id ?? "GBP cash"}</td><td>{item.amount}</td><td>{item.evidence}</td>
                </tr>)}</tbody></table>
              </div>
            </details>}
          </section>
          <PortfolioMonitoring portfolioId={data.portfolio_id} revision={data.revision} />
          <details className="investment-detail" id="investment-awareness">
            <summary>Optional local portfolio awareness</summary>
            <PortfolioAwareness portfolioId={data.portfolio_id} revision={data.revision} />
          </details>
          <details className="investment-detail" id="investment-records">
            <summary>Journal records</summary>
          <section className="history-group" aria-label="Recorded fees and income">
            <h2>Recorded fees and income</h2>
            <p className="position-note">Totals cover explicit journal events, including any before the baseline; coverage, tax and entitlement are not verified.</p>
            <dl className="health-facts">
              <div><dt>Recorded transaction fees</dt><dd>{pounds(data.recorded_fees_gbp)}</dd></div>
              <div><dt>Recorded interest</dt><dd>{pounds(data.recorded_interest_gbp)}</dd></div>
              <div><dt>Recorded dividends</dt><dd>{pounds(data.recorded_dividends_gbp)}</dd></div>
              <div><dt>Recorded income total</dt><dd>{pounds(data.recorded_income_gbp)}</dd></div>
            </dl>
            {data.recorded_fee_events.length > 0 && <>
              <h3>Recorded fill events</h3>
              <p className="position-note">Showing the latest {Math.min(data.recorded_fee_events.length, 100)} journal entries in original order.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded fill fee history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Side</th><th>Role</th><th>Account</th><th>Product</th><th>Quantity</th><th>Price</th><th>Fee</th><th>Available at</th><th>Evidence</th></tr></thead>
                  <tbody>{data.recorded_fee_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{event.account_id}</td><td>{event.product_id}</td><td>{event.quantity}</td>
                      <td>{pounds(event.price_gbp)}</td><td>{pounds(event.fee_gbp)}</td>
                      <td>{timestamp(event.available_at)}</td><td>{event.evidence}</td></tr>)}</tbody>
                </table>
              </div>
            </>}
            {data.recorded_income_events.length > 0 && <>
              <h3>Recorded income events</h3>
              <p className="position-note">Showing the latest {Math.min(data.recorded_income_events.length, 100)} journal entries in original order.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded income history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Type</th><th>Role</th><th>Account</th><th>Product</th><th>Amount</th><th>Available at</th><th>Evidence</th><th>Role allocation evidence</th></tr></thead>
                  <tbody>{data.recorded_income_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{event.account_id}</td><td>{event.product_id ?? "Not applicable"}</td>
                      <td>{pounds(event.amount_gbp)}</td><td>{timestamp(event.available_at)}</td>
                      <td>{event.evidence}</td><td>{event.role_evidence ?? "Not recorded"}</td></tr>)}</tbody>
                </table>
              </div>
            </>}
          </section>
          <p className="position-note">Baseline as of {timestamp(data.baseline_as_of)}. Lifetime gain and prior cost basis are unavailable. No valuation freshness threshold is configured.</p>
          {data.recorded_flow_events.length > 0 &&
            <section className="history-group" aria-label="Recorded capital flows">
              <h2>Recorded contributions, withdrawals and transfers</h2>
              <p className="position-note">Showing the latest {Math.min(data.recorded_flow_events.length, 100)} of {data.recorded_flow_events.length} journal entries in original order. These are operator-recorded events, not independently verified movements. Transfers between included accounts do not change net external contributions.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded capital flow history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Type</th><th>Role</th><th>Account</th><th>Other side</th><th>Recorded amount</th><th>Available at</th><th>Evidence</th></tr></thead>
                  <tbody>{data.recorded_flow_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{event.account_id}</td>
                      <td>{event.destination_account_id ??
                        (event.type === "contribution" ? "External source" : "External destination")}</td>
                      <td>{pounds(event.amount_gbp)}</td><td>{timestamp(event.available_at)}</td>
                      <td>{event.evidence}</td></tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.recorded_corrections.length > 0 &&
            <section className="history-group" aria-label="Evidence-backed corrections">
              <h2>Historical corrections</h2>
              <p className="position-note">Corrections append new evidence and replay downstream accounting. A correction is refused if the revised event breaks later accounting. Any historical correction makes performance unavailable; the system never creates a replacement baseline automatically.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Investment correction history">
                <table><thead><tr><th>Effective at (UTC)</th><th>Record type / account</th><th>Role</th><th>Original amount</th><th>Replacement amount</th><th>Evidence available at</th><th>Reason and evidence</th><th>Role allocation evidence</th></tr></thead>
                  <tbody>{data.recorded_corrections.slice(-100).map(item =>
                    <tr key={item.id}><td>{timestamp(item.timestamp)}</td>
                      <td>{label(item.replaced_type)}{item.account_id ? ` · ${item.account_id}` : ""}</td><td>{label(item.role)}</td>
                      <td>{pounds(item.original_amount_gbp)}</td>
                      <td>{pounds(item.replacement_amount_gbp)}</td>
                      <td>{timestamp(item.available_at)}</td><td>{item.evidence}</td><td>{item.role_evidence ?? "Not recorded"}</td></tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.recorded_profile_changes.length > 0 &&
            <section className="history-group" aria-label="Profile changes and scope additions">
              <h2>Account and product history</h2>
              <p className="position-note">Names can change, while account/product identities and financial semantics remain fixed. Retired entries remain visible. Funded account scope additions are unitization-neutral and are not external contributions.</p>
              <p className="position-note">Recorded scope additions: {pounds(data.recorded_scope_additions_gbp)}</p>
              <ul>{data.recorded_profile_changes.slice(-100).map(item =>
                <li key={item.id}>{label(item.action)} · {label(item.target)} {item.name ?? item.entity_id}
                  {item.name ? ` (${item.entity_id})` : ""} at {timestamp(item.timestamp)}; available at {timestamp(item.available_at)}. {item.evidence}</li>)}</ul>
              {data.recorded_scope_additions.length > 0 &&
                <ul aria-label="Unitization-neutral scope additions">
                  {data.recorded_scope_additions.slice(-100).map(item =>
                    <li key={item.id}>{item.account_id}: {pounds(item.amount_gbp)} at {timestamp(item.timestamp)}; {item.evidence}</li>)}
                </ul>}
            </section>}
          </details>
          <section className="history-group">
            <h2>Performance summary</h2>
            <dl className="health-facts">
              <div><dt>Time-weighted return</dt><dd>{percent(data.twr_pct)}</dd></div>
              <div><dt>Flow-neutral maximum drawdown</dt><dd>{percent(data.max_drawdown_pct)}</dd></div>
            </dl>
          </section>
          <div id="investment-performance"><InvestmentGrowthHistory history={data.valuation_history} /></div>
          <details ref={accountsRef} className="investment-detail" id="investment-accounts">
            <summary>Cash accounts, holdings and reconciliation evidence</summary>
          <section className="history-group"><h2>Cash accounts</h2><dl className="health-facts">
            {data.accounts.map(account => <div key={account.id}><dt>{account.name} ({label(account.kind)})
              {"status" in account && account.status === "retired" ? " · retired" : ""}</dt>
              <dd>{pounds(account.cash_gbp)}</dd></div>)}
          </dl><p className="position-note">External savings are not broker buying power. Deposit protection and product eligibility require separate evidence.</p></section>
          <CashEvidenceOverview data={data} now={now} />
          {data.accounts.some(account => account.cash_observation !== null) &&
            <section className="history-group"><h2>Observed cash balances</h2>
              <p className="position-note">Operator-entered evidence, not provider verification. Differences compare with recorded cash at that timestamp; they are not corrections or proof of a later balance.</p>
              <p className="position-note">Showing the latest {Math.min(data.cash_observations.length, 100)} of {data.cash_observations.length} recorded observations.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Cash reconciliation history">
                <table>
                  <thead><tr><th>Observed at (UTC)</th><th>Account</th><th>Balance</th><th>Difference (observed minus recorded)</th><th>Available at</th><th>Source and evidence</th></tr></thead>
                  <tbody>{[...data.cash_observations].reverse().slice(0, 100).map((observation, index) =>
                    <tr key={`${observation.account_id}:${observation.timestamp}:${index}`}>
                      <td>{timestamp(observation.timestamp)}</td><td>{observation.account_id}</td>
                      <td>{pounds(observation.balance_gbp, true)}</td><td>{pounds(observation.difference_gbp, true)}</td>
                      <td>{timestamp(observation.available_at)}</td>
                      <td>{observation.source} · {observation.evidence}</td>
                    </tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.accounts.some(account => account.terms !== null) &&
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
              <thead><tr><th>Fund</th><th>Account</th><th>Sleeve</th><th>Quantity</th><th>Value</th><th>Mark as of</th><th>Mark age</th><th>Available at</th></tr></thead>
              <tbody>{data.holdings.map(holding => <tr key={`${holding.account_id}:${holding.product_id}`}><td>{holding.name}</td><td>{holding.account_id}</td><td>{label(holding.sleeve)}</td><td>{holding.quantity}</td><td>{pounds(holding.value_gbp)}</td>{"mark_at" in holding && <><td>{timestamp(holding.mark_at)}</td><td>{evidenceAge(holding.mark_at, now)}</td><td>{timestamp(holding.mark_available_at)}</td></>}</tr>)}</tbody>
            </table></div>
          </section>
          </details>
          <details className="investment-detail" id="investment-policy">
            <summary>Proposal status and capability limits</summary>
          <section className="history-group"><h2>Allocation proposal: {label(data.proposal.status)}</h2>
            <p>{data.proposal.reason}</p><p className="position-note">No order approval or execution is available here. Proposal expiry: {timestamp(data.proposal.expires_at, "Not issued")}.</p>
          </section>
          <section className="history-group"><h2>Capability limits</h2><ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul>
            <p>No market investment guarantees profit or a maximum loss. This is a measurement and discipline tool, not evidence of future outperformance.</p>
          </section>
          </details>
        </>}
  </article>;
}

export function InvestmentRecordingPage() {
  return <article className="investment-view">
    <div className="record-toolbar">
      <NavLink className="text-link" to="/investment">Back to investment overview</NavLink>
    </div>
    <section className="investment-boundary" aria-label="Manual recording boundary">
      <ShieldAlert size={18} />
      <p>Only operator-entered evidence is recorded. Each change requires a fresh revision-bound preview and explicit confirmation. No bank or broker is contacted, and no order or transfer can be made.</p>
    </section>
    <InvestmentRecording />
  </article>;
}

export function InvestmentGuide() {
  return <article className="trading-guide">
    <p className="guide-intro">GoldScout's investment path supports a long-term, human-chosen portfolio of global funds, GBP savings and high-quality bonds. It measures recorded holdings; proposals are blocked and it does not trade.</p>
    <section><h2>Ownership, not short-term prediction</h2><p>Broad diversified funds spread company exposure, but still face market declines and concentration. GBP dealing does not remove underlying currency exposure. Bonds can fall when interest rates or credit conditions change; they are not protected savings.</p></section>
    <section><h2>Contributions and rebalancing</h2><p>Proposals are blocked until role-aware policy, products, weights and loss/transfer rules are approved and implemented. Monitoring review actions do not approve trades or resolve discrepancies. Future fees and settlement require human verification. No trade is a valid outcome.</p></section>
    <section><h2>Accurate GBP measurement</h2><p>Deposits are not profit. Internal transfers do not change total contributed capital. Recorded fills, costs and price observations determine the supported valuation; missing or stale evidence never grants permission to trade. External savings cannot be spent as broker cash.</p></section>
    <section><h2>Backup and recovery</h2>
      <p>This dashboard does not check whether a backup exists or is current. Git ignore is not encryption, access control or protection from deletion.
        Protect the journal, supporting evidence and receipts separately, including an off-machine copy.</p>
      <p>For explicit maintenance, stop writers and follow <code>docs/investment-journal.md</code> in the source checkout.
        It describes validated, non-overwriting backup and restore to new operator-controlled paths, receipt comparison and a restore rehearsal.
        Never replace an existing journal automatically or assume an older valid copy contains the latest evidence.</p>
    </section>
    <section><h2>Separate safety boundaries</h2><p>The dashboard is read-only by default; recording requires explicit local opt-in. There is no user login, so keep the service on its loopback address. Broker execution, licensed portfolio research, money-weighted return, inflation and full multi-currency accounting require further validated capabilities. News and AI remain advisory only.</p></section>
    <div className="guide-warning"><strong>Losses can be large, prolonged or permanent.</strong> Diversification, lower costs and a long horizon do not guarantee returns. Exact products, savings protection, tax wrappers and provider access require current verification.</div>
  </article>;
}
