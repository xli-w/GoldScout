import { useEffect, useRef, useState } from "react";
import { timestamp } from "./api";
import { EToroJournalBridge } from "./EToroJournalBridge";
import { canReviewEToro } from "./etoro-bridge-model";
import { activationReceiptMessage, hasUnrevokedEToroConnection, hasUncertainEToroRead,
  isActivationReceipt, recordEToroUncertainty, retainActivationReceipt,
  waitingForEToroStartup, type ActivationReceipt, type UncertainOutcome } from "./etoro-controls-model";
import type { InvestmentSnapshot } from "./InvestmentView";

type Instrument = {
  instrument_id: number; asset_currency: string; netUnits: string;
  netContracts: string; netCurrentExposureAssetCurrency: string;
  netCurrentExposureAccountCurrency: string; avgLeverage: string;
};
type ClosedTrade = {
  positionId: number; instrumentId: number; closeTimestamp: string;
  isBuy: boolean; units: string;
  orderId?: number;
  socialTradeId?: number; parentPositionId?: number;
  netProfit?: string; closeRate?: string; leverage?: string; openRate?: string;
  stopLossRate?: string | null; takeProfitRate?: string | null;
  investment?: string; initialInvestment?: string; fees?: string;
  openTimestamp?: string; trailingStopLoss?: boolean;
};
type ClosedTradeHistory = {
  status: "not_requested" | "available" | "unavailable";
  from_date: string | null; through_date: string | null; received_at: string | null;
  trades: ClosedTrade[]; error_code?: string;
};
type Mirror = {
  mirror_id: number; positions: Instrument[]; mirrorAvailableCash?: string;
  totals?: Record<string, string>;
};
export type EToroSnapshot = {
  schema_version: 1; environment: "real" | "demo"; account_id: number;
  source_as_of: string; account_currency: string; account_totals: Record<string, string>;
  direct_positions: Instrument[]; mirrors: Mirror[];
  closed_trade_history: ClosedTradeHistory;
  coverage: Record<string, string>;
  gbp_balance: null | {
    status: string; amount_gbp?: string; source_balance?: string;
    source_currency?: string; display_currency?: string; exchange_rate?: string;
    source_as_of: string | null; received_at: string; error_code?: string; meaning?: string;
  };
};
type Connection = {
  environment: "real" | "demo"; account_id: number;
  revoked_at: string | null; revision: number; reauthorization_required?: boolean;
};
type Session = {
  enabled: boolean; csrf?: string | null; state: string; last_status: string;
  snapshot_available: boolean; connection?: Connection | null; message?: string;
  read_generation?: number | null;
  connection_revision?: number | null;
  startup_pending?: boolean | null;
};
type PortfolioState = {
  state: string; snapshot: EToroSnapshot | null; connection?: Connection | null;
  last_status: string; message?: string;
  startup_pending?: boolean | null;
};
type StatusResult = { session: Session | null; portfolio: PortfolioState | null; error: string };
class EToroRequestError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message); }
}
let uncertainOutcome: UncertainOutcome = null;
let lastRevocationSession: Session | null = null;
let refreshInFlight = false;
let connectionReadInFlight = false;
let statusGeneration = 0;
type ActivationRequest = {
  request_id: string; environment: "real" | "demo";
  read_generation: number; connection_revision: number | null;
};
let unresolvedActivation: ActivationRequest | null = null;

const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function isInstrument(value: unknown): value is Instrument {
  return object(value) && Number.isSafeInteger(value.instrument_id) &&
    typeof value.asset_currency === "string" && typeof value.netUnits === "string" &&
    typeof value.netContracts === "string" &&
    typeof value.netCurrentExposureAssetCurrency === "string" &&
    typeof value.netCurrentExposureAccountCurrency === "string" &&
    typeof value.avgLeverage === "string";
}

function isClosedTrade(value: unknown): value is ClosedTrade {
  if (!object(value) || typeof value.positionId !== "number" ||
      !Number.isSafeInteger(value.positionId) || value.positionId <= 0 ||
      typeof value.instrumentId !== "number" || !Number.isSafeInteger(value.instrumentId) ||
      value.instrumentId <= 0 || !hasTimezone(value.closeTimestamp) ||
      typeof value.isBuy !== "boolean" || typeof value.units !== "string") return false;
  for (const key of ["positionId", "instrumentId", "orderId", "socialTradeId", "parentPositionId"]) {
    const id = value[key];
    if (key in value && (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0)) return false;
  }
  for (const key of [
    "netProfit", "closeRate", "leverage", "openRate", "investment",
    "initialInvestment", "fees", "units",
  ]) {
    if (key in value && typeof value[key] !== "string") return false;
  }
  for (const key of ["stopLossRate", "takeProfitRate"]) {
    if (key in value && value[key] !== null && typeof value[key] !== "string") return false;
  }
  for (const key of ["openTimestamp"]) {
    if (key in value && !hasTimezone(value[key])) return false;
  }
  for (const key of ["isBuy", "trailingStopLoss"]) {
    if (key in value && typeof value[key] !== "boolean") return false;
  }
  return true;
}

function isClosedTradeHistory(value: unknown): value is ClosedTradeHistory {
  if (!object(value) ||
      !["not_requested", "available", "unavailable"].includes(String(value.status)) ||
      !(value.from_date === null || typeof value.from_date === "string") ||
      !(value.through_date === null || typeof value.through_date === "string") ||
      !(value.received_at === null || hasTimezone(value.received_at)) ||
      !Array.isArray(value.trades) || !value.trades.every(isClosedTrade) ||
      value.error_code !== undefined && typeof value.error_code !== "string") return false;
  if (value.status === "available")
    return typeof value.from_date === "string" && typeof value.through_date === "string" &&
      hasTimezone(value.received_at) && value.error_code === undefined;
  if (value.status === "unavailable") return typeof value.error_code === "string";
  return value.from_date === null && value.through_date === null &&
    value.received_at === null && value.trades.length === 0 && value.error_code === undefined;
}

function isSnapshot(value: unknown): value is EToroSnapshot {
  return object(value) && value.schema_version === 1 &&
    (value.environment === "real" || value.environment === "demo") &&
    Number.isSafeInteger(value.account_id) && typeof value.source_as_of === "string" &&
    typeof value.account_currency === "string" && object(value.account_totals) &&
    Array.isArray(value.direct_positions) && value.direct_positions.every(isInstrument) &&
    Array.isArray(value.mirrors) && value.mirrors.every(mirror => object(mirror) &&
      Number.isSafeInteger(mirror.mirror_id) && Array.isArray(mirror.positions) &&
      mirror.positions.every(isInstrument)) &&
    object(value.coverage) && isClosedTradeHistory(value.closed_trade_history) &&
    (value.gbp_balance === null || object(value.gbp_balance));
}

async function responseJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length > 2_000_000) throw new Error("eToro response exceeded the supported bound.");
  try { return JSON.parse(text) as unknown; }
  catch { throw new Error("eToro response was invalid. Check the connection status before retrying."); }
}

async function get<T>(path: string, guard: (value: unknown) => value is T): Promise<T> {
  const response = await fetch(path, {
    method: "GET", credentials: "same-origin", cache: "no-store",
    redirect: "error", signal: AbortSignal.timeout(10000),
  });
  const value = await responseJson(response);
  if (!response.ok || !guard(value)) throw new Error("eToro connection status is unavailable.");
  return value;
}

async function post(path: string, csrf: string, body: unknown): Promise<unknown> {
  const response = await fetch(path, {
    method: "POST", credentials: "same-origin", cache: "no-store",
    redirect: "error",     signal: AbortSignal.timeout(70000),
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
  const value = await responseJson(response);
  if (!response.ok) {
    const detail = object(value) && typeof value.error_code === "string"
      ? ` (${value.error_code})`
      : object(value) && typeof value.message === "string" ? ` ${value.message}` : "";
    throw new EToroRequestError(`eToro request was refused or unavailable.${detail}`,
      response.status, object(value) && typeof value.error_code === "string" ? value.error_code :
        object(value) && typeof value.error === "string" ? value.error : "");
  }
  return value;
}

function isConnection(value: unknown): value is Connection {
  return object(value) && (value.environment === "real" || value.environment === "demo") &&
    Number.isSafeInteger(value.account_id) && Number.isSafeInteger(value.revision) &&
    (value.revoked_at === null || typeof value.revoked_at === "string") &&
    (value.reauthorization_required === undefined || typeof value.reauthorization_required === "boolean");
}

function isSession(value: unknown): value is Session {
  return object(value) && typeof value.enabled === "boolean" &&
    typeof value.state === "string" && typeof value.last_status === "string" &&
    typeof value.snapshot_available === "boolean" &&
    (value.read_generation === undefined || value.read_generation === null ||
      typeof value.read_generation === "number" && Number.isSafeInteger(value.read_generation) &&
      value.read_generation >= 0) &&
    (value.connection_revision === undefined || value.connection_revision === null ||
      typeof value.connection_revision === "number" && Number.isSafeInteger(value.connection_revision) &&
      value.connection_revision > 0) &&
    (value.startup_pending === undefined || value.startup_pending === null ||
      typeof value.startup_pending === "boolean") &&
    (value.csrf === undefined || value.csrf === null || typeof value.csrf === "string") &&
    (value.connection === undefined || value.connection === null || isConnection(value.connection)) &&
    (value.message === undefined || typeof value.message === "string");
}

function isPortfolioState(value: unknown): value is PortfolioState {
  return object(value) && typeof value.state === "string" &&
    typeof value.last_status === "string" &&
    (value.startup_pending === undefined || value.startup_pending === null ||
      typeof value.startup_pending === "boolean") &&
    (value.snapshot === null || isSnapshot(value.snapshot)) &&
    (value.connection === undefined || value.connection === null || isConnection(value.connection)) &&
    (value.message === undefined || typeof value.message === "string");
}

function matchingConnections(left: Connection | null | undefined, right: Connection | null | undefined) {
  if (left === undefined || right === undefined) return left === right;
  if (left === null || right === null) return left === right;
  return left.environment === right.environment && left.account_id === right.account_id &&
    left.revision === right.revision &&
    left.revoked_at === right.revoked_at &&
    left.reauthorization_required === right.reauthorization_required;
}

async function readStatus(): Promise<StatusResult> {
  const [session, portfolio] = await Promise.allSettled([
    get("/api/investment/v1/etoro/session", isSession),
    get("/api/investment/v1/etoro/portfolio", isPortfolioState),
  ]);
  const errors = [
    session.status === "rejected" || session.value.state === "unavailable"
      ? "eToro connection status is unavailable." : "",
    portfolio.status === "rejected" || portfolio.value.state === "unavailable"
      ? "eToro portfolio status is unavailable." : "",
  ].filter(Boolean);
  if (session.status === "fulfilled" && portfolio.status === "fulfilled") {
    if (session.value.state !== portfolio.value.state ||
        !matchingConnections(session.value.connection, portfolio.value.connection) ||
        session.value.last_status !== portfolio.value.last_status ||
        session.value.snapshot_available !== Boolean(portfolio.value.snapshot))
      errors.push("eToro status reads disagree. Retry status.");
    else if (["connected", "startup_refresh_enabled", "expired", "revoked", "reauthorization_required"].includes(session.value.state) &&
        (!session.value.connection || !portfolio.value.connection))
      errors.push("eToro connection details are incomplete. Retry status.");
    else if (session.value.state === "not_connected" &&
        (session.value.connection !== null || portfolio.value.connection !== null))
      errors.push("eToro connection details are incomplete. Retry status.");
  }
  return {
    session: session.status === "fulfilled" ? session.value : null,
    portfolio: portfolio.status === "fulfilled" ? portfolio.value : null,
    error: errors.join(" "),
  };
}

function hasTimezone(value: unknown): value is string {
  return typeof value === "string" && /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) &&
    Number.isFinite(Date.parse(value));
}

function providerTimestamp(value: unknown) {
  return hasTimezone(value) ? timestamp(value) : "Unavailable (invalid timestamp)";
}

function PositionRows({ label, positions }: { label: string; positions: Instrument[] }) {
  return <section className="etoro-section" aria-label={label}>
    <h4>{label}</h4>
    {positions.length === 0 ? <p>No instrument aggregates reported.</p> : <div className="table-scroll">
      <table><thead><tr>
        <th>Instrument ID</th><th>Net units</th><th>Net contracts</th>
        <th>Exposure (asset currency)</th><th>Exposure (account currency)</th><th>Average leverage</th>
      </tr></thead><tbody>{positions.map(position => <tr key={position.instrument_id}>
        <td>{position.instrument_id}</td><td>{position.netUnits}</td><td>{position.netContracts}</td>
        <td>{position.netCurrentExposureAssetCurrency} {position.asset_currency}</td>
        <td>{position.netCurrentExposureAccountCurrency}</td><td>{position.avgLeverage}</td>
      </tr>)}</tbody></table>
    </div>}
  </section>;
}

function SnapshotView({ snapshot, now }: { snapshot: EToroSnapshot; now: number }) {
  const totals = snapshot.account_totals;
  const gbp = snapshot.gbp_balance;
  const sourceTime = hasTimezone(snapshot.source_as_of) ? Date.parse(snapshot.source_as_of) : NaN;
  const ageMinutes = Number.isFinite(sourceTime) && sourceTime <= now
    ? Math.floor((now - sourceTime) / 60000) : null;
  const rate = gbp?.exchange_rate;
  const reportedGBP = gbp?.status === "provider_reported" &&
    typeof gbp.amount_gbp === "string" && /^-?\d+(?:\.\d+)?$/.test(gbp.amount_gbp) &&
    typeof gbp.source_balance === "string" && /^-?\d+(?:\.\d+)?$/.test(gbp.source_balance) &&
    gbp.source_currency === snapshot.account_currency && gbp.display_currency === "GBP" &&
    typeof rate === "string" && /^\d+(?:\.\d+)?$/.test(rate) && /[1-9]/.test(rate);
  return <div className="etoro-snapshot">
    <p><strong>{snapshot.environment === "real" ? "Real account" : "Demo account"}</strong>
      {" · "}Account ID {snapshot.account_id} · Source snapshot {providerTimestamp(snapshot.source_as_of)}
      {" · "}Account currency {snapshot.account_currency}</p>
    <p role="status">{ageMinutes === null ? "Source age unavailable; check the provider clock."
      : `Provider snapshot age: ${ageMinutes} ${ageMinutes === 1 ? "minute" : "minutes"}.`}
      {" "}Age does not establish that the account is complete or currently accurate.</p>
    <div className="etoro-warning" role="note">
      Read-only provider evidence is not custody verification or buying-power approval.
    </div>
    <section className="etoro-section" aria-label="Account totals">
      <h4>Provider account totals ({snapshot.account_currency})</h4>
      <dl className="etoro-totals">
        <div><dt>Account value</dt><dd>{totals.accountTotalValue}</dd></div>
        <div><dt>Account balance</dt><dd>{totals.accountBalance}</dd></div>
        <div><dt>Available cash</dt><dd>{totals.accountAvailableCash}</dd></div>
        <div><dt>Cash reserved for pending open orders</dt><dd>{totals.accountFrozenCash}</dd></div>
        <div><dt>Used margin</dt><dd>{totals.accountTotalUsedMargin}</dd></div>
        <div><dt>Current unrealized P&amp;L</dt><dd>{totals.accountCurrentPnl}</dd></div>
      </dl>
      <p className="position-note">Account value includes portfolio value and is not spendable cash.
        Pending-order details are not included; reserved cash is shown once and is not added to the balance.</p>
    </section>
    {reportedGBP ? <section className="etoro-section" aria-label="Provider reported GBP balance">
      <h4>Provider-reported GBP balance</h4>
      <p><strong>GBP {gbp.amount_gbp}</strong> · Source balance {gbp.source_balance} {gbp.source_currency}
        {" · "}Provider rate {gbp.exchange_rate} {gbp.display_currency} per {gbp.source_currency}</p>
      <p className="position-note">eToro documents a Trading account balance as total account value, not spendable cash.
        This separate read has no source timestamp, is not synchronized to the portfolio snapshot,
        and is not used to value individual holdings or populate the GBP journal.</p>
    </section> : <section className="etoro-section" aria-label="GBP balance unavailable">
      <h4>GBP balance unavailable</h4>
      <p>The provider did not return a usable GBP account balance. No conversion was estimated.
        {gbp?.status === "provider_reported" ? " The provider returned an invalid GBP balance or exchange rate."
          : gbp?.error_code && ` Reason: ${gbp.error_code}.`}</p>
    </section>}
    <PositionRows label="Direct instrument aggregates" positions={snapshot.direct_positions} />
    {snapshot.mirrors.map(mirror => <section key={mirror.mirror_id} className="etoro-section"
      aria-label={`Copy relationship ${mirror.mirror_id}`}>
      <h4>Copy relationship {mirror.mirror_id}</h4>
      {mirror.mirrorAvailableCash !== undefined &&
        <p>Mirror available cash ({snapshot.account_currency}): {mirror.mirrorAvailableCash}</p>}
      {mirror.totals && <dl className="etoro-totals">{Object.entries(mirror.totals).map(([name, value]) =>
        <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>}
      <PositionRows label={`Instrument aggregates for copy relationship ${mirror.mirror_id}`}
        positions={mirror.positions} />
    </section>)}
  </div>;
}

function ClosedTradeHistoryView({ history }: { history: ClosedTradeHistory }) {
  return <section className="etoro-section" aria-label="Closed trade history preview">
    <h3>Closed-trade history preview</h3>
    {history.status === "not_requested" && <p role="status">
      No closed-trade history has been requested in this dashboard session.
      Choose “Refresh once now” to request it.
    </p>}
    {history.status === "unavailable" && <p role="status">
      Closed-trade history is unavailable ({history.error_code}). The current portfolio snapshot remains separate;
      no history rows are assumed.
    </p>}
    {history.status === "available" && <>
      <p>eToro returned {history.trades.length} closed trades from {history.from_date} through {history.through_date};
        received {providerTimestamp(history.received_at)}. Newest close first; the read is limited to 1,000 rows.</p>
      {history.trades.length === 0 ? <p>No closed trades were returned in this window. This does not describe
        open positions or activity outside the requested period.</p> : <div className="table-scroll">
        <table><thead><tr>
          <th>Opened (UTC)</th><th>Closed (UTC)</th><th>Instrument / position</th>
          <th>Side</th><th>Units</th><th>Open / close rate (quote currency unknown)</th>
          <th>Leverage</th><th>Investment (currency unspecified)</th>
          <th>Fees (currency unspecified)</th><th>Net profit (currency unspecified)</th>
        </tr></thead><tbody>{history.trades.map((trade, index) =>
          <tr key={`${trade.positionId ?? "unknown"}:${trade.orderId ?? "unknown"}:${index}`}>
            <td>{trade.openTimestamp ? providerTimestamp(trade.openTimestamp) : "Not returned"}</td>
            <td>{providerTimestamp(trade.closeTimestamp)}</td>
            <td>{trade.instrumentId ?? "Not returned"} / {trade.positionId ?? "Not returned"}</td>
            <td>{trade.isBuy === undefined ? "Not returned" : trade.isBuy ? "Buy" : "Sell"}</td>
            <td>{trade.units ?? "Not returned"}</td>
            <td>{trade.openRate ?? "Not returned"} / {trade.closeRate ?? "Not returned"}</td>
            <td>{trade.leverage ?? "Not returned"}</td>
            <td>{trade.investment ?? "Not returned"}</td>
            <td>{trade.fees ?? "Not returned"}</td>
            <td>{trade.netProfit ?? "Not returned"}</td>
          </tr>)}</tbody></table>
      </div>}
      <p className="position-note">This endpoint reports closed trades only and does not provide row-level currency,
        instrument type or an account identifier. Its association with the selected portfolio account is not
        independently verified. Amounts are not GBP values, are not mapped to journal events, and do not update
        the journal.</p>
    </>}
  </section>;
}

export function EToroPortfolioPanel({ revokeOnly = false, journal, now: journalNow, viewActive = true }: {
  revokeOnly?: boolean; journal?: InvestmentSnapshot; now?: number; viewActive?: boolean;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
  const [revocationSession, setRevocationSession] = useState<Session | null>(lastRevocationSession);
  const [environment, setEnvironment] = useState<"" | "real" | "demo">("");
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState("");
  const [activationMessage, setActivationMessage] = useState("");
  const [activationReceipt, setActivationReceipt] = useState<ActivationReceipt | null>(null);
  const [receiptStatusLoading, setReceiptStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [reconciliationRequired, setReconciliationRequired] = useState(Boolean(uncertainOutcome));
  const [now, setNow] = useState(Date.now());
  const pendingRequest = useRef<string | null>(null);
  const busyRef = useRef(false);
  const readingRef = useRef(false);
  const revokingRef = useRef(false);
  const statusPollEpoch = useRef(0);
  const mounted = useRef(true);

  const applyStatus = (status: StatusResult) => {
    if (hasUncertainEToroRead(status.session?.last_status, status.portfolio?.last_status)) {
      if (!uncertainOutcome) uncertainOutcome = "provider_read";
      setReconciliationRequired(true);
    }
    setSession(status.session);
    setPortfolio(status.portfolio);
    setStatusError(status.error);
    if (!status.error) setReceiptStatusLoading(false);
    if (status.session) {
      if (!status.session.enabled || !status.session.csrf) lastRevocationSession = null;
      else if (hasUnrevokedEToroConnection(status.session) ||
          uncertainOutcome === "connection" || uncertainOutcome === "provider_read" ||
          status.session.state === "startup_refresh_enabled" ||
          status.portfolio?.state === "startup_refresh_enabled" ||
          status.session.state === "unavailable" || status.portfolio?.state === "unavailable")
        lastRevocationSession = status.session;
      else if (!status.error && !uncertainOutcome) lastRevocationSession = null;
      setRevocationSession(lastRevocationSession);
    }
    if (!status.error && !uncertainOutcome) setReconciliationRequired(false);
  };
  const load = async (generation = statusGeneration): Promise<StatusResult> => {
    statusPollEpoch.current += 1;
    const status = await readStatus();
    if (generation === statusGeneration) applyStatus(status);
    return status;
  };
  const loadAfterChange = async (completed: string, generation = statusGeneration) => {
    const status = await load(generation);
    if (generation === statusGeneration && status.error)
      throw new Error(`${completed}, but updated status could not be loaded. Retry status before starting another read.`);
  };
  useEffect(() => {
    let active = true;
    mounted.current = true;
    const generation = statusGeneration;
    void readStatus().then(status => {
      if (active && generation === statusGeneration) applyStatus(status);
    });
    return () => {
      active = false;
      mounted.current = false;
      if (revokingRef.current)
        uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "revocation");
      else if (readingRef.current)
        uncertainOutcome = recordEToroUncertainty(uncertainOutcome, refreshInFlight ? "refresh" : "connection");
    };
  }, []);
  useEffect(() => {
    if (!session?.enabled || !viewActive) return;
    let active = true;
    let polling = false;
    const check = async () => {
      if (polling || busyRef.current || revokingRef.current || document.hidden) return;
      polling = true;
      const epoch = ++statusPollEpoch.current;
      const generation = statusGeneration;
      try {
        const status = await readStatus();
        if (active && epoch === statusPollEpoch.current && generation === statusGeneration &&
            !busyRef.current && !revokingRef.current) applyStatus(status);
      } finally { polling = false; }
    };
    const timer = window.setInterval(() => void check(), 5000);
    const onFocus = () => void check();
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [session?.enabled, viewActive]);
  useEffect(() => {
    if (busy || revoking || !waitingForEToroStartup(session, portfolio)) return;
    let active = true;
    const generation = statusGeneration;
    const timer = window.setTimeout(async () => {
      const status = await readStatus();
      if (active && generation === statusGeneration) applyStatus(status);
    }, 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session, portfolio, busy, revoking]);
  useEffect(() => {
    if (!portfolio?.snapshot) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 60000);
    return () => window.clearInterval(timer);
  }, [portfolio?.snapshot]);

  const run = async (action: () => Promise<void>) => {
    if (busyRef.current || revokingRef.current) return;
    busyRef.current = true;
    const generation = statusGeneration;
    setBusy(true); setError("");
    try { await action(); }
    catch (failure) {
      if (generation === statusGeneration) {
        if (failure instanceof EToroRequestError &&
            failure.code === "connection_read_outcome_uncertain") {
          uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "connection");
          setReconciliationRequired(true);
          setError("A provider request may have been sent; the connection read outcome is uncertain.");
        } else {
          setError(failure instanceof Error ? failure.message : "eToro request was unavailable.");
        }
      }
    }
    finally { busyRef.current = false; setBusy(false); }
  };
  const begin = () => void run(async () => {
    if (refreshInFlight || connectionReadInFlight) throw new Error("Wait for the current eToro read before starting another.");
    if (revokeOnly || reconciliationRequired || statusError || !portfolio)
      throw new Error("Check eToro status before starting a new read.");
    if (!session?.csrf || !environment) throw new Error("Choose an eToro environment and load the local session first.");
    if (typeof session.read_generation !== "number")
      throw new Error("Reload eToro status before starting a connection read.");
    if (session.connection_revision === undefined)
      throw new Error("Reload eToro status before enabling the connection.");
    setActivationMessage("");
    setActivationReceipt(null);
    statusPollEpoch.current += 1;
    const requestId = pendingRequest.current ?? crypto.randomUUID();
    pendingRequest.current = requestId;
    const generation = statusGeneration;
    const request: ActivationRequest = {
      request_id: requestId, environment, read_generation: session.read_generation,
      connection_revision: session.connection_revision,
    };
    unresolvedActivation = request;
    connectionReadInFlight = true; readingRef.current = true; setReading(true);
    try {
      let result: unknown;
      let recoveredReceipt: ActivationReceipt | null = null;
      try {
        result = await post("/api/investment/v1/etoro/connect", session.csrf, request);
        if (!object(result) || typeof result.state !== "string")
          throw new Error("Invalid activation response.");
        const receipt = isActivationReceipt(result.activation_receipt, environment)
          ? result.activation_receipt : null;
        if (result.state !== "startup_refresh_enabled" || !isConnection(result.connection)) {
          if (!receipt || !["reauthorization_required", "revoked", "not_connected", "unavailable"].includes(result.state))
            throw new Error("Invalid activation response.");
          recoveredReceipt = receipt;
        } else if (receipt && receipt.revision !== result.connection.revision) {
          recoveredReceipt = receipt;
        }
      } catch (failure) {
        if (!(failure instanceof EToroRequestError) || hasUncertainEToroRead(failure.code)) {
          uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "connection");
          setReconciliationRequired(true);
          throw new Error("Activation may have completed. Check status and the dashboard's logs before another read.");
        }
        unresolvedActivation = null;
        pendingRequest.current = null;
        throw failure;
      }
      if (retainActivationReceipt(mounted.current, generation, statusGeneration)) return;
      unresolvedActivation = null;
      pendingRequest.current = null;
      if (recoveredReceipt) {
        setReceiptStatusLoading(true);
        setActivationReceipt(recoveredReceipt);
      }
      await loadAfterChange("The connection was enabled", generation);
    } finally {
      connectionReadInFlight = false; readingRef.current = false; setReading(false);
    }
  });
  const checkActivationReceipt = () => void run(async () => {
    const request = unresolvedActivation;
    const csrf = session?.csrf || revocationSession?.csrf;
    if (!request || !csrf) throw new Error("The activation receipt or local session is unavailable.");
    const result = await post("/api/investment/v1/etoro/activation-receipt", csrf, request);
    if (!object(result)) throw new Error("The activation receipt response was invalid.");
    if (result.receipt_status === "unknown") {
      setActivationMessage("No completed activation receipt is available. The read outcome remains unverified.");
      return;
    }
    if (result.receipt_status !== "committed" ||
        !isActivationReceipt(result.activation_receipt, request.environment) ||
        typeof result.state !== "string")
      throw new Error("The activation receipt response was invalid.");
    setActivationMessage("");
    setReceiptStatusLoading(true);
    setActivationReceipt(result.activation_receipt);
    unresolvedActivation = null;
    pendingRequest.current = null;
    if (uncertainOutcome === "connection") {
      uncertainOutcome = null;
      setReconciliationRequired(false);
    }
    await loadAfterChange("The activation receipt was checked");
  });
  const revoke = async () => {
    if (revokingRef.current || (busyRef.current && !readingRef.current)) return;
    const csrf = session?.csrf || revocationSession?.csrf;
    if (!csrf) { setError("The local eToro session is unavailable."); return; }
    revokingRef.current = true;
    statusPollEpoch.current += 1;
    setRevoking(true); setError("");
    statusGeneration += 1;
    if (refreshInFlight) {
      uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "refresh");
      setReconciliationRequired(true);
    } else if (connectionReadInFlight) {
      uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "connection");
      setReconciliationRequired(true);
    }
    try {
      try {
        const result = await post("/api/investment/v1/etoro/revoke", csrf, {});
        if (!object(result) || !["revoked", "not_connected"].includes(String(result.state)))
          throw new Error("Revocation response was invalid.");
      } catch {
        uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "revocation");
        pendingRequest.current = null;
        setSession(null); setPortfolio(null); setStatusError(""); setReconciliationRequired(true);
        throw new Error("Revocation could not be verified and may already have taken effect. Retry status before trying again.");
      }
      pendingRequest.current = null;
      await loadAfterChange("The connection was disabled");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "eToro request was unavailable.");
    } finally {
      revokingRef.current = false; setRevoking(false);
    }
  };
  const refresh = () => void run(async () => {
    if (refreshInFlight || connectionReadInFlight) throw new Error("Wait for the current eToro read before starting another.");
    if (revokeOnly || reconciliationRequired || statusError || !portfolio)
      throw new Error("Check eToro status before requesting another read.");
    if (!session?.csrf || typeof session.read_generation !== "number" || !session.connection)
      throw new Error("The local eToro session is unavailable; reload status.");
    statusPollEpoch.current += 1;
    const generation = statusGeneration;
    refreshInFlight = true;
    readingRef.current = true; setReading(true);
    try {
      let failedCode = "";
      try {
        const result = await post("/api/investment/v1/etoro/refresh", session.csrf, {
          read_generation: session.read_generation,
          revision: session.connection.revision, account_id: session.connection.account_id,
          environment: session.connection.environment,
        });
        if (!object(result) || !["refreshed", "refresh_failed"].includes(String(result.state)))
          throw new Error("Refresh response was invalid.");
        if (result.state === "refresh_failed")
          failedCode = typeof result.error_code === "string" ? result.error_code : "unknown";
        if (failedCode === "connection_read_outcome_uncertain")
          throw new Error("Read outcome uncertain.");
      } catch (failure) {
        if (generation !== statusGeneration) return;
        if (failure instanceof EToroRequestError && failure.status === 409 &&
            failure.code === "etoro_refresh_rejected") {
          await load(generation);
          throw failure;
        }
        uncertainOutcome = recordEToroUncertainty(uncertainOutcome, "refresh");
        setSession(null); setPortfolio(null); setStatusError(""); setReconciliationRequired(true);
        throw new Error("The refresh result could not be verified. Check status and the dashboard's logs before requesting another read.");
      }
      if (generation !== statusGeneration) return;
      setReconciliationRequired(true);
      await loadAfterChange("The refresh request completed", generation);
      if (failedCode) throw new Error(`The eToro read failed (${failedCode}). The previous snapshot, if any, was not refreshed.`);
    } finally {
      refreshInFlight = false; readingRef.current = false; setReading(false);
    }
  });

  const missingReportedSnapshot = Boolean(session?.state === "startup_refresh_enabled" &&
    portfolio && !portfolio.snapshot && !waitingForEToroStartup(session, portfolio) &&
    (session.last_status === "success" || portfolio.last_status === "success"));
  const snapshot = portfolio?.snapshot;
  const bridgeReady = canReviewEToro({
    revokeOnly, preview: false, busy, revoking, statusError, reconciliationRequired,
    session, portfolioStatus: portfolio?.last_status, snapshot, journal, now: journalNow,
  });
  const bridgeKey = snapshot && journal
    ? JSON.stringify([snapshot, journal.portfolio_id, journal.revision]) : "";
  return <section className="etoro-panel" id="investment-etoro" aria-label="eToro portfolio" aria-busy={busy || revoking}>
    <h2>eToro</h2>
    <p className="etoro-intro">Optional provider view, separate from your journal and its totals.
      Provider coverage is partial and not yet live-validated.</p>
    <details className="help-disclosure">
      <summary>About eToro data</summary>
      <p>Instrument types and individual position legs are not provided. Exposure is net by instrument;
        copied positions appear separately. Pending-order details are unavailable beyond reserved cash.</p>
      <p>Closed-trade history is fetched only by manual refresh, for up to 365 calendar
        days and 1,000 rows. Startup refresh reads the current portfolio only. History is kept in memory for
        this dashboard session and is not saved.</p>
      <p>The closed-trade response does not identify row-level currency, instrument type or account. It is a
        separate provider view, not a transaction import or journal update.</p>
      <p>The GBP balance is a separate, unsynchronized read with unverified coverage and source time.
        No holdings valuation, conversion or journal import is inferred.</p>
      <p>Keys stay in private server-side settings and account evidence is never sent to hosted AI.
        Only activation, environment and account ID are saved; the portfolio remains in memory for this dashboard run.</p>
      <p>Revocation cannot guarantee cancellation of an already sent read or prevent cross-instance races.
        Refresh checks the account binding before the separate balance and history requests; the initial connection
        read establishes the binding from the returned account.</p>
    </details>
    {error && <p role="alert" className="investment-warnings">{error}</p>}
    {activationMessage && <p role="status">{activationMessage}</p>}
    {activationReceipt && <p role="status">{activationReceiptMessage(activationReceipt, session, Boolean(statusError) || revoking || receiptStatusLoading)}</p>}
    {statusError && <p role="alert" className="investment-warnings">{statusError}</p>}
    {session?.message && <p role="alert" className="investment-warnings">{session.message}</p>}
    {!session && !error && !statusError && <p role="status">Checking eToro status.</p>}
    {(error || statusError || reconciliationRequired || missingReportedSnapshot) &&
      <button type="button" disabled={busy || revoking} onClick={() => void run(async () => { await load(); })}>Retry status</button>}
    {unresolvedActivation && reconciliationRequired && <button type="button" disabled={busy || revoking || !(session?.csrf || revocationSession?.csrf)}
      onClick={checkActivationReceipt}>Check activation receipt</button>}
    {reconciliationRequired && <p role="status">{uncertainOutcome === "refresh"
      ? "Refresh is still unverified. Status is not a request-specific receipt; check the dashboard's logs before requesting another read."
      : uncertainOutcome === "connection"
      ? "The connection read is still unverified. Check the dashboard's logs before requesting another read."
      : uncertainOutcome === "revocation"
      ? "Revocation or a read is still unverified. Status is not a request-specific receipt; check the dashboard's logs before requesting another read."
      : uncertainOutcome === "provider_read"
      ? "A provider read is still unverified. Check the dashboard's logs before requesting another read."
      : "Check both eToro status reads before starting another read."}</p>}
    {revokeOnly && <p role="note">While portfolio status needs attention, you can check eToro status,
      or disable the connection, but cannot start new reads here.</p>}
    {!session && revocationSession && <p role="note">Connection status is unavailable. Revocation can use the
      last available local session, but its result must be checked.</p>}
    {session && !session.enabled && <p role="status">eToro is not enabled for this session.
      Open GoldScout with a workspace or ask the person who starts it to select a separate eToro connection file.
      Opening this view does not request a read.</p>}
    {session?.enabled && !session.csrf && <p role="alert">eToro controls are unavailable because the local session
      could not be verified. Reload the page to retry.</p>}
    {!statusError && session?.state === "not_connected" && <p role="status">Not connected. Enable eToro to read and activate startup refresh.</p>}
    {!statusError && session?.state === "reauthorization_required" &&
      <p role="status">A legacy connection or changed credentials require explicit activation before another read.
        Any displayed portfolio snapshot is from an earlier read.</p>}
    {portfolio && !portfolio.snapshot && session?.state === "startup_refresh_enabled" &&
      <p role="status">{waitingForEToroStartup(session, portfolio)
        ? "Waiting for the startup read. This screen does not initiate a request."
        : missingReportedSnapshot
        ? "The startup read was reported, but its snapshot is unavailable. Retry status before requesting another read."
        : session.last_status === "not_collected" && portfolio.last_status === "not_collected"
        ? "No startup read is pending in this dashboard instance. You can refresh now."
        : `The automatic startup read did not complete (${session.last_status}). It will not be retried automatically.`}</p>}
    {session?.state === "revoked" && <p role="status">{statusError
      ? "Connection status reports revocation; check the unavailable or conflicting status."
      : "eToro connection is disabled."}</p>}
    {portfolio?.snapshot && <div className="recording-preview">
      <h3>Latest eToro read</h3>
      {portfolio.last_status !== "success" && <p role="status">Last refresh status: {portfolio.last_status}.
        The displayed source snapshot is not refreshed.</p>}
      <SnapshotView snapshot={portfolio.snapshot} now={now} />
      <ClosedTradeHistoryView history={portfolio.snapshot.closed_trade_history} />
      {bridgeReady && viewActive && snapshot && journal &&
        <EToroJournalBridge key={bridgeKey} snapshot={snapshot} journal={journal} />}
      {!bridgeReady && journal?.source_mode === "recorded" && <p className="position-note">
        Side-by-side review is unavailable until a valid, bound real-account read and journal are available.
        No records have been changed.</p>}
    </div>}
    {session?.connection &&
      <p className="position-note">Account ID {session.connection.account_id} · {session.connection.environment} environment.</p>}
    {(hasUnrevokedEToroConnection(session) || session?.state === "startup_refresh_enabled" || revocationSession ||
      (reconciliationRequired && session?.enabled && session.csrf) ||
      (connectionReadInFlight && session?.enabled && session.csrf)) && <div className="etoro-actions">
      {!revokeOnly && session?.state === "startup_refresh_enabled" && <button type="button"
        disabled={busy || revoking || !session?.csrf || !portfolio || Boolean(statusError) || reconciliationRequired ||
          waitingForEToroStartup(session, portfolio)} onClick={refresh}>Refresh once now</button>}
      {(hasUnrevokedEToroConnection(session) || session?.state === "startup_refresh_enabled" || revocationSession || reading || reconciliationRequired && session?.enabled && session.csrf) && <button type="button" disabled={revoking || (busy && !reading) || !(session?.csrf || revocationSession?.csrf)}
        onClick={() => void revoke()}>{hasUnrevokedEToroConnection(session) || session?.state === "startup_refresh_enabled" || revocationSession
          || !reading ? "Disable eToro" : "Stop current read"}</button>}
      <p className="position-note">An already sent read may still finish.</p>
    </div>}
    {!revokeOnly && !reconciliationRequired && !statusError && portfolio &&
      session?.enabled && session.csrf && session.state !== "unavailable" &&
      session.state !== "startup_refresh_enabled" && <details className="help-disclosure">
      <summary>{session.connection ? "Update eToro connection" : "Connect eToro"}</summary>
      <p className="etoro-intro">Keys stay in private server-side settings. Opening this panel does not request a read.</p>
      <form className="recording-form etoro-form" onSubmit={event => { event.preventDefault(); begin(); }}>
      <label>eToro environment
        <select value={environment} required onChange={event =>
          setEnvironment(event.target.value as "" | "real" | "demo")}>
          <option value="">Choose explicitly</option><option value="real">Real</option><option value="demo">Demo</option>
        </select>
      </label>
      <p>Enabling reads the selected environment now and at each server startup, until disabled.
        Either key scope can be used; credentials with trading permissions can place, modify or cancel orders if misused.</p>
      <button type="submit" disabled={busy || revoking}>Enable eToro</button>
      </form>
    </details>}
  </section>;
}
