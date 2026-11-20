import { useEffect, useRef, useState } from "react";
import { timestamp } from "./api";
import { EToroJournalBridge } from "./EToroJournalBridge";
import { canReviewEToro } from "./etoro-bridge-model";
import type { InvestmentSnapshot } from "./InvestmentView";

type Instrument = {
  instrument_id: number; asset_currency: string; netUnits: string;
  netContracts: string; netCurrentExposureAssetCurrency: string;
  netCurrentExposureAccountCurrency: string; avgLeverage: string;
};
type Mirror = {
  mirror_id: number; positions: Instrument[]; mirrorAvailableCash?: string;
  totals?: Record<string, string>;
};
export type EToroSnapshot = {
  schema_version: 1; environment: "real" | "demo"; account_id: number;
  source_as_of: string; account_currency: string; account_totals: Record<string, string>;
  direct_positions: Instrument[]; mirrors: Mirror[];
  coverage: Record<string, string>;
  gbp_balance: null | {
    status: string; amount_gbp?: string; source_balance?: string;
    source_currency?: string; display_currency?: string; exchange_rate?: string;
    source_as_of: string | null; received_at: string; error_code?: string; meaning?: string;
  };
};
type Connection = {
  environment: "real" | "demo"; account_id: number; expires_at: string | null;
  revoked_at: string | null; revision: number;
};
type Session = {
  enabled: boolean; csrf?: string | null; state: string; last_status: string;
  snapshot_available: boolean; connection?: Connection | null; message?: string;
  read_generation?: number | null;
};
type Preview = { preview_id: string; expires_at: string; snapshot: EToroSnapshot };
type PortfolioState = {
  state: string; snapshot: EToroSnapshot | null; connection?: Connection | null;
  last_status: string; message?: string;
};
type StatusResult = { session: Session | null; portfolio: PortfolioState | null; error: string };
class EToroRequestError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message); }
}
let uncertainOutcome: "confirmation" | "refresh" | "connection" | "revocation" | null = null;
let lastRevocationSession: Session | null = null;
let refreshInFlight = false;
let connectionReadInFlight = false;
let statusGeneration = 0;

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

function isSnapshot(value: unknown): value is EToroSnapshot {
  return object(value) && value.schema_version === 1 &&
    (value.environment === "real" || value.environment === "demo") &&
    Number.isSafeInteger(value.account_id) && typeof value.source_as_of === "string" &&
    typeof value.account_currency === "string" && object(value.account_totals) &&
    Array.isArray(value.direct_positions) && value.direct_positions.every(isInstrument) &&
    Array.isArray(value.mirrors) && value.mirrors.every(mirror => object(mirror) &&
      Number.isSafeInteger(mirror.mirror_id) && Array.isArray(mirror.positions) &&
      mirror.positions.every(isInstrument)) &&
    object(value.coverage) && (value.gbp_balance === null || object(value.gbp_balance));
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
    redirect: "error", signal: AbortSignal.timeout(35000),
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(body),
  });
  const value = await responseJson(response);
  if (!response.ok) {
    const detail = object(value) && typeof value.error_code === "string"
      ? ` (${value.error_code})`
      : object(value) && typeof value.message === "string" ? ` ${value.message}` : "";
    throw new EToroRequestError(`eToro request was refused or unavailable.${detail}`,
      response.status, object(value) && typeof value.error === "string" ? value.error : "");
  }
  return value;
}

function isConnection(value: unknown): value is Connection {
  return object(value) && (value.environment === "real" || value.environment === "demo") &&
    Number.isSafeInteger(value.account_id) && Number.isSafeInteger(value.revision) &&
    (value.expires_at === null || typeof value.expires_at === "string") &&
    (value.revoked_at === null || typeof value.revoked_at === "string");
}

function isSession(value: unknown): value is Session {
  return object(value) && typeof value.enabled === "boolean" &&
    typeof value.state === "string" && typeof value.last_status === "string" &&
    typeof value.snapshot_available === "boolean" &&
    (value.read_generation === undefined || value.read_generation === null ||
      typeof value.read_generation === "number" && Number.isSafeInteger(value.read_generation) &&
      value.read_generation >= 0) &&
    (value.csrf === undefined || value.csrf === null || typeof value.csrf === "string") &&
    (value.connection === undefined || value.connection === null || isConnection(value.connection)) &&
    (value.message === undefined || typeof value.message === "string");
}

function isPortfolioState(value: unknown): value is PortfolioState {
  return object(value) && typeof value.state === "string" &&
    typeof value.last_status === "string" &&
    (value.snapshot === null || isSnapshot(value.snapshot)) &&
    (value.connection === undefined || value.connection === null || isConnection(value.connection)) &&
    (value.message === undefined || typeof value.message === "string");
}

function matchingConnections(left: Connection | null | undefined, right: Connection | null | undefined) {
  if (left === undefined || right === undefined) return left === right;
  if (left === null || right === null) return left === right;
  return left.environment === right.environment && left.account_id === right.account_id &&
    left.revision === right.revision && left.expires_at === right.expires_at && left.revoked_at === right.revoked_at;
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
    else if (["connected", "startup_refresh_enabled", "expired", "revoked"].includes(session.value.state) &&
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
      <p className="position-note">This is a separate account-balance read; balance coverage and source time are unverified.
        It is not synchronized to the portfolio snapshot and is not used to value individual holdings
        or populate the GBP journal.</p>
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

function waitingForStartup(session: Session | null, portfolio: PortfolioState | null) {
  return Boolean(session?.enabled && session.state === "startup_refresh_enabled" &&
    portfolio && !portfolio.snapshot &&
    (session.last_status === "not_collected" || portfolio.last_status === "not_collected"));
}

export function EToroPortfolioPanel({ revokeOnly = false, journal, now: journalNow }: {
  revokeOnly?: boolean; journal?: InvestmentSnapshot; now?: number;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
  const [revocationSession, setRevocationSession] = useState<Session | null>(lastRevocationSession);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [environment, setEnvironment] = useState<"" | "real" | "demo">("");
  const [readScope, setReadScope] = useState(false);
  const [tradeRisk, setTradeRisk] = useState(false);
  const [eligibility, setEligibility] = useState(false);
  const [retention, setRetention] = useState(false);
  const [binding, setBinding] = useState(false);
  const [startupRefresh, setStartupRefresh] = useState(false);
  const [refreshDays, setRefreshDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [error, setError] = useState("");
  const [statusError, setStatusError] = useState("");
  const [reconciliationRequired, setReconciliationRequired] = useState(Boolean(uncertainOutcome));
  const [now, setNow] = useState(Date.now());
  const pendingRequest = useRef<string | null>(null);
  const busyRef = useRef(false);
  const readingRef = useRef(false);
  const revokingRef = useRef(false);

  const applyStatus = (status: StatusResult) => {
    setSession(status.session);
    setPortfolio(status.portfolio);
    setStatusError(status.error);
    if (status.session) {
      if (!status.session.enabled || !status.session.csrf) lastRevocationSession = null;
      else if (status.session.state === "startup_refresh_enabled" ||
          status.portfolio?.state === "startup_refresh_enabled" ||
          status.session.state === "unavailable" || status.portfolio?.state === "unavailable")
        lastRevocationSession = status.session;
      else if (!status.error && !uncertainOutcome) lastRevocationSession = null;
      setRevocationSession(lastRevocationSession);
    }
    if (!status.error && !uncertainOutcome) setReconciliationRequired(false);
  };
  const load = async (generation = statusGeneration): Promise<StatusResult> => {
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
    const generation = statusGeneration;
    void readStatus().then(status => {
      if (active && generation === statusGeneration) applyStatus(status);
    });
    return () => {
      active = false;
      if (revokingRef.current)
        uncertainOutcome = "revocation";
      else if (readingRef.current)
        uncertainOutcome = refreshInFlight ? "refresh" : "connection";
    };
  }, []);
  useEffect(() => {
    if (busy || revoking || preview || !waitingForStartup(session, portfolio)) return;
    let active = true;
    const generation = statusGeneration;
    const timer = window.setTimeout(async () => {
      const status = await readStatus();
      if (active && generation === statusGeneration) applyStatus(status);
    }, 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session, portfolio, busy, revoking, preview]);
  useEffect(() => {
    if (!preview && !portfolio?.snapshot) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), preview ? 1000 : 60000);
    return () => window.clearInterval(timer);
  }, [preview, portfolio?.snapshot]);

  const run = async (action: () => Promise<void>) => {
    if (busyRef.current || revokingRef.current) return;
    busyRef.current = true;
    const generation = statusGeneration;
    setBusy(true); setError("");
    try { await action(); }
    catch (failure) {
      if (generation === statusGeneration)
        setError(failure instanceof Error ? failure.message : "eToro request was unavailable.");
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
    if (readScope === tradeRisk) throw new Error("Verify Read scope or accept broader-key risk for this one-time read.");
    if (!eligibility || !retention) throw new Error("Confirm account eligibility and permitted local display/retention.");
    const requestId = pendingRequest.current ?? crypto.randomUUID();
    pendingRequest.current = requestId;
    const generation = statusGeneration;
    connectionReadInFlight = true; readingRef.current = true; setReading(true);
    try {
      const result = await post("/api/investment/v1/etoro/connect", session.csrf, {
        request_id: requestId, environment, read_scope_verified: readScope,
        trade_capable_key_risk_accepted: tradeRisk,
        eligibility_verified: eligibility, retention_rights_verified: retention,
        read_generation: session.read_generation,
      });
      if (generation !== statusGeneration) return;
      if (!object(result) || typeof result.preview_id !== "string" ||
          !hasTimezone(result.expires_at) || !isSnapshot(result.snapshot))
        throw new Error("eToro preview failed validation. Nothing was saved.");
      setPreview({ preview_id: result.preview_id, expires_at: result.expires_at, snapshot: result.snapshot });
      setBinding(false); setStartupRefresh(false); setNow(Date.now());
    } finally {
      connectionReadInFlight = false; readingRef.current = false; setReading(false);
    }
  });
  const confirm = () => void run(async () => {
    if (revokeOnly || reconciliationRequired || statusError || !portfolio)
      throw new Error("Check eToro status before confirming a connection.");
    if (!session?.csrf || !preview) throw new Error("The eToro preview is unavailable.");
    if (!binding) throw new Error("Confirm that the returned account identifier is the account you selected.");
    if (startupRefresh && !readScope)
      throw new Error("Automatic startup refresh requires verified Read-only key scope.");
    try {
      const result = await post("/api/investment/v1/etoro/confirm", session.csrf, {
        preview_id: preview.preview_id, account_binding_confirmed: binding,
        startup_refresh: startupRefresh, refresh_days: startupRefresh ? refreshDays : null,
      });
      if (!object(result) || result.state !== (startupRefresh ? "startup_refresh_enabled" : "connected"))
        throw new Error("Confirmation response was invalid.");
    } catch {
      uncertainOutcome = "confirmation";
      setPreview(null); pendingRequest.current = null; setSession(null); setPortfolio(null);
      setStatusError(""); setReconciliationRequired(true); setStartupRefresh(false);
      throw new Error("Connection confirmation could not be verified and may already be saved. Check status and the dashboard's logs before reconnecting.");
    }
    setPreview(null); pendingRequest.current = null; setStartupRefresh(false);
    setReconciliationRequired(true);
    await loadAfterChange("The connection was saved");
  });
  const discard = () => void run(async () => {
    if (!session?.csrf || !preview) throw new Error("The eToro preview is unavailable.");
    const result = await post("/api/investment/v1/etoro/discard", session.csrf, {
      preview_id: preview.preview_id,
    });
    if (!object(result) || typeof result.discarded !== "boolean")
      throw new Error("The eToro preview could not be discarded.");
    setPreview(null); pendingRequest.current = null; setBinding(false); setStartupRefresh(false);
  });
  const revoke = async () => {
    if (revokingRef.current || (busyRef.current && !readingRef.current)) return;
    const csrf = session?.csrf || revocationSession?.csrf;
    if (!csrf) { setError("The local eToro session is unavailable."); return; }
    if (!window.confirm((connectionReadInFlight || refreshInFlight) && session?.state !== "startup_refresh_enabled"
      ? "Stop the current eToro read and revoke the connection? A request already sent may finish; reconnect before another read."
      : "Revoke automatic eToro startup refresh? The current journal will not change.")) return;
    revokingRef.current = true;
    setRevoking(true); setError("");
    statusGeneration += 1;
    if (refreshInFlight) {
      uncertainOutcome = "refresh";
      setReconciliationRequired(true);
    } else if (connectionReadInFlight) {
      uncertainOutcome = "connection";
      setReconciliationRequired(true);
    }
    try {
      try {
        const result = await post("/api/investment/v1/etoro/revoke", csrf, {});
        if (!object(result) || !["revoked", "not_connected"].includes(String(result.state)))
          throw new Error("Revocation response was invalid.");
      } catch {
        setPreview(null); pendingRequest.current = null; setStartupRefresh(false);
        setSession(null); setPortfolio(null); setStatusError(""); setReconciliationRequired(true);
        throw new Error("Revocation could not be verified and may already have taken effect. Retry status before trying again.");
      }
      setPreview(null); pendingRequest.current = null; setStartupRefresh(false);
      await loadAfterChange("Startup refresh was revoked");
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
    if (!window.confirm(`Request one eToro read for ${session.connection.environment} account ${session.connection.account_id} now? Verify account eligibility and permission to display the data. A key with trading permissions can place, modify or cancel orders if exposed or misused. This read does not update the journal.`)) return;
    const generation = statusGeneration;
    refreshInFlight = true;
    readingRef.current = true; setReading(true);
    try {
      let failedCode = "";
      try {
        const result = await post("/api/investment/v1/etoro/refresh", session.csrf, {
          confirm_read: true, read_generation: session.read_generation,
          revision: session.connection.revision, account_id: session.connection.account_id,
          environment: session.connection.environment,
        });
        if (!object(result) || !["refreshed", "refresh_failed"].includes(String(result.state)))
          throw new Error("Refresh response was invalid.");
        if (result.state === "refresh_failed")
          failedCode = typeof result.error_code === "string" ? result.error_code : "unknown";
      } catch (failure) {
        if (generation !== statusGeneration) return;
        if (failure instanceof EToroRequestError && failure.status === 409 &&
            failure.code === "etoro_refresh_rejected") {
          await load(generation);
          throw failure;
        }
        uncertainOutcome = "refresh";
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

  const missingReportedSnapshot = Boolean(!preview && session?.state === "startup_refresh_enabled" &&
    portfolio && !portfolio.snapshot && !waitingForStartup(session, portfolio) &&
    (session.last_status === "success" || portfolio.last_status === "success"));
  const previewExpired = preview !== null && Date.parse(preview.expires_at) <= now;
  const snapshot = portfolio?.snapshot;
  const bridgeReady = canReviewEToro({
    revokeOnly, preview: preview !== null, busy, revoking, statusError, reconciliationRequired,
    session, portfolioStatus: portfolio?.last_status, snapshot, journal, now: journalNow,
  });
  const bridgeKey = snapshot && journal
    ? JSON.stringify([snapshot, journal.portfolio_id, journal.revision]) : "";
  return <section className="etoro-panel" id="investment-etoro" aria-label="eToro portfolio" aria-busy={busy || revoking}>
    <h2>eToro</h2>
    <p className="etoro-intro">Optional provider view, separate from your journal and its totals.
      Provider coverage is partial and not yet live-validated.</p>
    <details className="help-disclosure">
      <summary>About eToro data and permissions</summary>
      <p>Instrument types and individual position legs are not provided. Exposure is net by instrument;
        copied positions appear separately. Pending-order details are unavailable beyond reserved cash.</p>
      <p>The GBP balance is a separate, unsynchronized read with unverified coverage and source time.
        No holdings valuation, conversion or journal import is inferred.</p>
      <p>Keys stay in private server-side settings and account evidence is never sent to hosted AI.
        Only consent and account ID are saved; the portfolio remains in memory for this dashboard run.</p>
      <p>Revocation cannot guarantee cancellation of an already sent read or prevent cross-instance races.
        Refresh checks the account binding before the separate balance request; the initial connection read does not yet have a confirmed binding.</p>
    </details>
    {error && <p role="alert" className="investment-warnings">{error}</p>}
    {statusError && <p role="alert" className="investment-warnings">{statusError}</p>}
    {session?.message && <p role="alert" className="investment-warnings">{session.message}</p>}
    {!session && !error && !statusError && <p role="status">Checking eToro status.</p>}
    {(error || statusError || reconciliationRequired || missingReportedSnapshot) &&
      <button type="button" disabled={busy || revoking} onClick={() => void run(async () => { await load(); })}>Retry status</button>}
    {reconciliationRequired && <p role="status">{uncertainOutcome === "confirmation"
      ? "Confirmation is still unverified. Status is not a request-specific receipt; check the dashboard's logs before reconnecting."
      : uncertainOutcome === "refresh"
      ? "Refresh is still unverified. Status is not a request-specific receipt; check the dashboard's logs before requesting another read."
      : uncertainOutcome === "connection"
      ? "The connection read is still unverified. Check the dashboard's logs before requesting another read."
      : uncertainOutcome === "revocation"
      ? "Revocation or a read is still unverified. Status is not a request-specific receipt; check the dashboard's logs before requesting another read."
      : "Check both eToro status reads before starting or confirming another read."}</p>}
    {revokeOnly && <p role="note">While portfolio status needs attention, you can check eToro status,
      discard a preview or revoke startup refresh, but cannot start or confirm new reads here.</p>}
    {!session && revocationSession && <p role="note">Connection status is unavailable. Revocation can use the
      last available local session, but its result must be checked.</p>}
    {session && !session.enabled && <p role="status">eToro is not enabled for this session.
      Open GoldScout with a workspace or ask the person who starts it to select a separate eToro connection file.
      Opening this view does not request a read.</p>}
    {session?.enabled && !session.csrf && <p role="alert">eToro controls are unavailable because the local session
      could not be verified. Reload the page to retry.</p>}
    {!preview && !statusError && session?.state === "not_connected" && <p role="status">Not connected. Connecting requires an explicit one-time read.</p>}
    {!preview && !statusError && session?.state === "connected" && <p role="status">Connection saved.
      {!portfolio ? " The latest read status is unavailable."
        : portfolio.snapshot ? " The latest read is available below." : " No read is available in this dashboard session."}</p>}
    {!preview && portfolio && !portfolio.snapshot &&
      ["connected", "expired"].includes(session?.state ?? "") &&
      !["not_collected", "success"].includes(portfolio.last_status) &&
      <p role="status">The last eToro read did not complete ({portfolio.last_status}).
        No provider snapshot is available in this session.</p>}
    {!preview && portfolio && !portfolio.snapshot && session?.state === "startup_refresh_enabled" &&
      <p role="status">{waitingForStartup(session, portfolio)
        ? "Waiting for the one authorized startup read. This screen does not initiate a request."
        : missingReportedSnapshot
        ? "The startup read was reported, but its snapshot is unavailable. Retry status before requesting another read."
        : `The automatic startup read did not complete (${session.last_status}). It will not be retried automatically.`}</p>}
    {!preview && session?.state === "expired" && <p role="status">{statusError
      ? "Connection status reports expiry; check the unavailable or conflicting status."
      : "Startup permission expired. The portfolio will not be fetched automatically; reconnect to renew."}</p>}
    {!preview && session?.state === "revoked" && <p role="status">{statusError
      ? "Connection status reports revocation; check the unavailable or conflicting status."
      : "Startup permission is revoked. Further startup reads are not authorized by this permission."}</p>}
    {!preview && portfolio?.snapshot && <div className="recording-preview">
      <h3>Latest eToro read</h3>
      {portfolio.last_status !== "success" && <p role="status">Last refresh status: {portfolio.last_status}.
        The displayed source snapshot is not refreshed.</p>}
      <SnapshotView snapshot={portfolio.snapshot} now={now} />
      {bridgeReady && snapshot && journal && <EToroJournalBridge key={bridgeKey} snapshot={snapshot} journal={journal} />}
      {!bridgeReady && journal?.source_mode === "recorded" && <p className="position-note">
        Side-by-side review is unavailable until a valid, bound real-account read and journal are available.
        No records have been changed.</p>}
    </div>}
    {!preview && session?.connection &&
      <p className="position-note">Account ID {session.connection.account_id} · {session.connection.environment} environment.
        {session.connection.expires_at && <> Startup-read permission expiry: {providerTimestamp(session.connection.expires_at)}.</>}</p>}
    {(["connected", "expired", "startup_refresh_enabled"].includes(session?.state ?? "") || revocationSession ||
      (connectionReadInFlight && session?.enabled && session.csrf)) && <div className="etoro-actions">
      {!preview && !revokeOnly && ["connected", "expired", "startup_refresh_enabled"].includes(session?.state ?? "") && <button type="button"
        disabled={busy || revoking || !session?.csrf || !portfolio || Boolean(statusError) || reconciliationRequired ||
          waitingForStartup(session, portfolio)} onClick={refresh}>Refresh once now</button>}
      {(session?.state === "startup_refresh_enabled" || revocationSession || reading) && <button type="button" disabled={revoking || (busy && !reading) || !(session?.csrf || revocationSession?.csrf)}
        onClick={() => void revoke()}>{session?.state === "startup_refresh_enabled" || revocationSession
          ? "Revoke startup refresh" : "Stop current read"}</button>}
      <p className="position-note">An already sent read may still finish.</p>
    </div>}
    {preview ? <div className="recording-preview" aria-label="Review eToro portfolio before saving consent">
      <h3>Review eToro read</h3>
      <p>Review the returned account and its source coverage. No portfolio snapshot is stored on disk;
        only your explicit connection permission and account identifier are saved.</p>
      <SnapshotView snapshot={preview.snapshot} now={now} />
      <label className="setup-check"><input type="checkbox" checked={binding} disabled={revokeOnly}
        onChange={event => setBinding(event.target.checked)} />
        I verified account ID {preview.snapshot.account_id} is the eToro account I selected.</label>
      <label className="setup-check"><input type="checkbox" checked={startupRefresh}
        disabled={revokeOnly || !readScope} onChange={event => setStartupRefresh(event.target.checked)} />
        Allow one automatic read at each server startup while permission is active.</label>
      {!readScope && <p role="note">Automatic refresh is unavailable unless Read-only key scope is verified.</p>}
      {startupRefresh && <label>Permission expires in
        <select value={refreshDays} onChange={event => setRefreshDays(Number(event.target.value))}>
          <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
        </select>
      </label>}
      <p className="position-note">Only consent and account ID are saved; the portfolio stays in memory.</p>
      {previewExpired && <p role="alert">This eToro preview expired. Discard it and request a new read.</p>}
      <button type="button" disabled={busy || revoking || revokeOnly || reconciliationRequired || Boolean(statusError) ||
        !portfolio || !binding || previewExpired} onClick={confirm}>
        Confirm connection and account
      </button>
      <button type="button" disabled={busy || revoking} onClick={discard}>
        Discard preview
      </button>
    </div> : !revokeOnly && !reconciliationRequired && !statusError && portfolio &&
      session?.enabled && session.csrf && session.state !== "unavailable" && <details className="help-disclosure">
      <summary>{session.connection ? "Update eToro connection" : "Connect eToro"}</summary>
      <p className="etoro-intro">Keys stay in private server-side settings. Opening this panel does not request a read.</p>
      <form className="recording-form etoro-form" onSubmit={event => { event.preventDefault(); begin(); }}>
      <label>eToro environment
        <select value={environment} required onChange={event =>
          setEnvironment(event.target.value as "" | "real" | "demo")}>
          <option value="">Choose explicitly</option><option value="real">Real</option><option value="demo">Demo</option>
        </select>
      </label>
      <fieldset><legend>Connection checks</legend>
        <label className="setup-check"><input type="checkbox" checked={readScope}
          onChange={event => { setReadScope(event.target.checked); if (event.target.checked) setTradeRisk(false); else setStartupRefresh(false); }} />
          I verified this key has Read-only permission.</label>
        <label className="setup-check"><input type="checkbox" checked={tradeRisk}
          onChange={event => { setTradeRisk(event.target.checked); if (event.target.checked) { setReadScope(false); setStartupRefresh(false); } }} />
          I accept the risk if this credential can place, modify or cancel orders. This permits one supervised read only.</label>
        <label className="setup-check"><input type="checkbox" checked={eligibility}
          onChange={event => setEligibility(event.target.checked)} />
          I verified my account is eligible for this API use.</label>
        <label className="setup-check"><input type="checkbox" checked={retention}
          onChange={event => setRetention(event.target.checked)} />
          I verified rights for local display and transient in-memory processing; I understand only consent and account ID are saved.</label>
      </fieldset>
      <button type="submit" disabled={busy || revoking}>Connect and retrieve once</button>
      </form>
    </details>}
  </section>;
}
