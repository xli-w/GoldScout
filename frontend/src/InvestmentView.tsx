import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";
import validate from "./investment-validator.js";
import validateV2 from "./investment-validator-v2.js";
import { label, timestamp } from "./api";
import { PortfolioAwareness } from "./PortfolioAwareness";

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
    replaced_type: "contribution" | "withdrawal" | "interest"; account_id: string;
    role: string; role_evidence: string | null; original_amount_gbp: string; replacement_amount_gbp: string; timestamp: string;
    available_at: string; evidence: string }[];
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
type RecordingProduct = { id: string; name: string; sleeve: "equity" | "bond"; quantity_increment: string };
type CorrectableEvent = { id: string; type: "contribution" | "withdrawal" | "interest";
  account_id: string; currency: "GBP"; amount_gbp: string; timestamp: string; available_at: string; evidence: string;
  role?: "core" | "dynamic" | "unassigned"; role_evidence?: string };
type RecordingSession = { enabled: boolean; authenticated: boolean; csrf: string | null;
  opened: boolean; accounts: RecordingAccount[]; products: RecordingProduct[];
  latest_correctable_event: CorrectableEvent | null };
type RecordingPreview = { preview_id: string; revision: number; event: Record<string, unknown>;
  before: { equity_gbp: string | null }; after: { equity_gbp: string | null };
  expires_at: string };
type OpeningHoldingInput = { account_id: string; product_id: string; quantity: string; price_gbp: string };

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
  const [password, setPassword] = useState("");
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
      if (result.opened) setKind(current => current === "opening" ? "contribution" : current);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "recording_session_unavailable");
    }
  }

  useEffect(() => { void refreshSession(); }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.csrf) return;
    setBusy(true);
    setMessage(null);
    try {
      await recordingRequest("/api/investment/v1/recording/login", session.csrf, { password });
      setPassword("");
      await refreshSession();
      setMessage("Recording access enabled for this local session.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "login_failed");
    } finally {
      setBusy(false);
    }
  }

  async function makePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.csrf) return;
    setBusy(true);
    setMessage(null);
    try {
      const id = crypto.randomUUID();
      const timestamp = kind === "correction" && session.latest_correctable_event
        ? session.latest_correctable_event.timestamp : eventClock(effectiveAt);
      const available = eventClock(availableAt);
      const common = { id, timestamp, available_at: available, evidence };
      let value: Record<string, unknown>;
      if (kind === "opening") {
        const holdings = openingHoldings.filter(item =>
          item.account_id && item.product_id && item.quantity).map(item => ({
            ...item, price_gbp: item.price_gbp === "" ? null : item.price_gbp,
          }));
        value = { ...common, type: "opening", balances_gbp: balances, holdings };
      } else if (kind === "correction") {
        const original = session.latest_correctable_event;
        if (!original) throw new Error("No eligible latest cash record is available to correct.");
        const replacement = {
          ...original, id: crypto.randomUUID(), amount_gbp: amount || original.amount_gbp,
          available_at: available, evidence,
        };
        value = { ...common, type: "correction", timestamp: original.timestamp,
          original_event_id: original.id, replacement_event: replacement };
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
  if (!session.enabled) return null;
  if (!session.authenticated) return <section className="history-group investment-recording" aria-label="Manual recording">
    <h2>Manual portfolio recording</h2>
    <p>Local-only, opt-in record editing. No bank or broker is contacted, and no order or transfer can be made.</p>
    <form className="recording-form" onSubmit={login}>
      <label>Local recording password<input type="password" autoComplete="current-password"
        minLength={12} maxLength={256} required value={password}
        onChange={event => setPassword(event.target.value)} /></label>
      <button type="submit" disabled={busy}>Unlock recording</button>
    </form>
    {message && <p role="status">{message}</p>}
  </section>;

  const opened = session.opened;
  const correction = session.latest_correctable_event;
  const availableKinds = opened
    ? ["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell",
      "mark", "cash_observation", "role_assignment", ...(correction ? ["correction"] : [])]
    : ["opening"];
  const selectedProduct = session.products.find(product => product.id === productId);

  return <section className="history-group investment-recording" aria-label="Manual recording">
    <h2>Manual portfolio recording</h2>
    <p>Explicit operator records only. Evidence and balances are not independently verified; this cannot place orders or move money.</p>
    {preview ? <div className="recording-preview" role="group" aria-label="Review record before confirmation">
      <h3>Review before recording</h3>
      <p>{String(preview.event.type)} at the recorded portfolio revision {preview.revision}. This preview expires at {timestamp(preview.expires_at)}.</p>
      <dl className="health-facts">
        <div><dt>Current recorded value</dt><dd>{pounds(preview.before.equity_gbp)}</dd></div>
        <div><dt>Value after this record</dt><dd>{pounds(preview.after.equity_gbp)}</dd></div>
        <div><dt>Effective time</dt><dd>{timestamp(String(preview.event.timestamp))}</dd></div>
      </dl>
      <details className="recording-event" open aria-label="Full event being appended">
        <summary>Full event and evidence being appended</summary>
        <pre>{JSON.stringify(preview.event, null, 2)}</pre>
      </details>
      <p className="position-note">Only confirm if the event, account, amount, evidence and before/after values match the source you reviewed.</p>
      <button type="button" disabled={busy} onClick={() => void confirmPreview()}>Confirm and append record</button>
      <button type="button" disabled={busy} onClick={() => setPreview(null)}>Cancel preview</button>
    </div> : <form className="recording-form" onSubmit={makePreview}>
      {opened && <label>Record type<select value={kind} onChange={event => setKind(event.target.value)}>
        {availableKinds.map(value => <option key={value} value={value}>{label(value)}</option>)}
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
      {kind !== "opening" && kind !== "correction" && <label>Account<select required value={accountId}
        onChange={event => setAccountId(event.target.value)}>
        {session.accounts.map(account => <option key={account.id} value={account.id}>{account.name} ({label(account.kind)})</option>)}
      </select></label>}
      {kind === "transfer" && <label>Destination account<select required value={destinationAccountId}
        onChange={event => setDestinationAccountId(event.target.value)}>
        {session.accounts.filter(account => account.id !== accountId)
          .map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select></label>}
      {(["dividend", "buy", "sell", "mark"].includes(kind) ||
        kind === "role_assignment" && assignmentAsset === "holding") && <label>Fund<select required value={productId}
        onChange={event => setProductId(event.target.value)}>
        {session.products.map(product => <option key={product.id} value={product.id}>{product.name} ({product.id})</option>)}
      </select></label>}
      {["contribution", "withdrawal", "interest", "dividend", "transfer", "cash_observation", "correction", "role_assignment"]
        .includes(kind) && <label>{kind === "cash_observation" ? "Observed GBP balance" :
          kind === "correction" ? "Corrected GBP amount" :
          kind === "role_assignment" && assignmentAsset === "holding" ? "Holding quantity to attribute" : "GBP amount"}
          <input required inputMode="decimal"
            value={kind === "correction" && !amount && correction ? correction.amount_gbp : amount}
            onChange={event => setAmount(event.target.value)} />
        </label>}
      {kind === "cash_observation" && <label>Statement/source description<input required maxLength={200}
        value={source} onChange={event => setSource(event.target.value)} /></label>}
      {["interest", "dividend"].includes(kind) && <label>Evidence for this role's income allocation
        <textarea required maxLength={200} value={roleEvidence}
          onChange={event => setRoleEvidence(event.target.value)} /></label>}
      {["buy", "sell"].includes(kind) && <>
        <label>Quantity<input required inputMode="decimal" value={quantity}
          onChange={event => setQuantity(event.target.value)} /></label>
        <label>GBP price per unit<input required inputMode="decimal" value={price}
          onChange={event => setPrice(event.target.value)} /></label>
        <label>Recorded fee in GBP<input required inputMode="decimal" value={fee}
          onChange={event => setFee(event.target.value)} /></label>
        {selectedProduct && <small>Quantity increment: {selectedProduct.quantity_increment}</small>}
      </>}
      {kind === "mark" && <label>GBP price per unit<input required inputMode="decimal" value={price}
        onChange={event => setPrice(event.target.value)} /></label>}
      {kind !== "correction" && <label>Effective time (your local timezone)
        <input required type="datetime-local" value={effectiveAt}
          onChange={event => setEffectiveAt(event.target.value)} />
      </label>}
      {kind === "correction" && correction && <p className="position-note">
        Correcting latest {label(correction.type)} {correction.id}, originally recorded as {pounds(correction.amount_gbp)} at {timestamp(correction.timestamp)}. The original remains in the audit history.
      </p>}
      <label>Evidence available at (your local timezone)
        <input required type="datetime-local" value={availableAt}
          onChange={event => setAvailableAt(event.target.value)} />
      </label>
      <label>Source evidence / correction reason<textarea required maxLength={200}
        value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      <button type="submit" disabled={busy}>Preview record</button>
    </form>}
    {message && <p role="status">{message}</p>}
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
          <div className="demo-band"><span>{data.source_mode === "demo" ? "SYNTHETIC INVESTMENT DEMO" : "RECORDED INVESTMENT"}</span><span>Recording is opt-in / execution disabled</span></div>
          <p className="position-note">Valuation as of {timestamp(data.as_of)}. Revision {data.revision}. {data.complete ? "Complete at the recorded clock." : "Incomplete valuation; no complete portfolio total."} Recorded inputs are not current broker verification.</p>
          <section className="metrics" aria-label="Investment totals">
            <div><span>Portfolio value</span><strong>{pounds(data.equity_gbp)}</strong><small>GBP, recorded valuation</small></div>
            <div><span>Net contributions</span><strong>{pounds(data.net_contributions_gbp)}</strong><small>Deposits are not profit</small></div>
            <div><span>{data.schema_version === 2 ? "Gain since baseline" : "Investment gain"}</span><strong>{pounds(data.schema_version === 2 ? data.since_baseline_gain_gbp : data.gain_gbp)}</strong><small>Net of recorded fees</small></div>
          </section>
          <PortfolioAwareness portfolioId={data.portfolio_id} revision={data.revision} />
          {data.schema_version === 2 && <section className="history-group" aria-label="Management role accounting">
            <h2>Management roles</h2>
            <p className="position-note">Roles are operator-attributed independently of account and asset class. Unassigned funds are not assumed to be core or dynamic. Role-to-role transfers and proposals are unavailable.</p>
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
            {data.role_assignments.length > 0 && <div className="table-scroll" tabIndex={0} aria-label="Role attribution history">
              <table><thead><tr><th>Attribution clock</th><th>Role</th><th>Account</th><th>Asset</th><th>Amount / quantity</th><th>Evidence</th></tr></thead>
                <tbody>{data.role_assignments.slice(-100).map(item => <tr key={item.id}>
                  <td>{timestamp(item.timestamp)}</td><td>{label(item.role)}</td><td>{item.account_id}</td>
                  <td>{item.product_id ?? "GBP cash"}</td><td>{item.amount}</td><td>{item.evidence}</td>
                </tr>)}</tbody></table>
            </div>}
          </section>}
          {data.schema_version === 2 && <section className="history-group" aria-label="Recorded fees and income">
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
          </section>}
          {data.schema_version === 2 && <p className="position-note">Baseline as of {timestamp(data.baseline_as_of)}. Lifetime gain and prior cost basis are unavailable. No valuation freshness threshold is configured.</p>}
          {data.schema_version === 2 && data.source_mode === "recorded" && <InvestmentRecording />}
          {data.schema_version === 2 && data.recorded_flow_events.length > 0 &&
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
          {data.schema_version === 2 && data.recorded_corrections.length > 0 &&
            <section className="history-group" aria-label="Evidence-backed corrections">
              <h2>Latest-record corrections</h2>
              <p className="position-note">Corrections retain the original record and are available only for the latest contribution, withdrawal or interest amount. Cash observations, opening balances and older events are not overwritten.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Investment correction history">
                <table><thead><tr><th>Effective at (UTC)</th><th>Record type / account</th><th>Role</th><th>Original amount</th><th>Replacement amount</th><th>Evidence available at</th><th>Reason and evidence</th><th>Role allocation evidence</th></tr></thead>
                  <tbody>{data.recorded_corrections.slice(-100).map(item =>
                    <tr key={item.id}><td>{timestamp(item.timestamp)}</td>
                      <td>{label(item.replaced_type)} · {item.account_id}</td><td>{label(item.role)}</td>
                      <td>{pounds(item.original_amount_gbp)}</td>
                      <td>{pounds(item.replacement_amount_gbp)}</td>
                      <td>{timestamp(item.available_at)}</td><td>{item.evidence}</td><td>{item.role_evidence ?? "Not recorded"}</td></tr>)}</tbody>
                </table>
              </div>
            </section>}
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
            <p>{data.proposal.reason}</p><p className="position-note">No order approval or execution is available here. Proposal expiry: {timestamp(data.proposal.expires_at, "Not issued")}.</p>
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
    <p className="guide-intro">GoldScout's investment path supports a long-term, human-chosen portfolio of global funds, GBP savings and high-quality bonds. It measures recorded holdings; proposals are blocked and it does not trade.</p>
    <section><h2>Ownership, not short-term prediction</h2><p>Broad diversified funds spread company exposure, but still face market declines and concentration. GBP dealing does not remove underlying currency exposure. Bonds can fall when interest rates or credit conditions change; they are not protected savings.</p></section>
    <section><h2>Contributions and rebalancing</h2><p>Proposals are blocked until role-aware policy, products, weights and loss/transfer rules are approved and implemented. Review acknowledgements are recorded through the offline CLI, not the dashboard; five-point drift remains a trigger. They do not approve trades. Future fees and settlement require human verification. No trade is a valid outcome.</p></section>
    <section><h2>Accurate GBP measurement</h2><p>Deposits are not profit. Internal transfers do not change total contributed capital. Recorded fills, costs and price observations determine the supported valuation; missing or stale evidence never grants permission to trade. External savings cannot be spent as broker cash.</p></section>
    <section><h2>Separate safety boundaries</h2><p>The dashboard is read-only by default; v2 recording requires explicit local opt-in and authentication. Broker execution, licensed portfolio research, money-weighted return, inflation and full multi-currency accounting require further validated capabilities. Existing legacy trading stops and recovery are not removed or changed by the investment path. News and AI remain advisory only.</p></section>
    <div className="guide-warning"><strong>Losses can be large, prolonged or permanent.</strong> Diversification, lower costs and a long horizon do not guarantee returns. Exact products, savings protection, tax wrappers and provider access require current verification.</div>
  </article>;
}
