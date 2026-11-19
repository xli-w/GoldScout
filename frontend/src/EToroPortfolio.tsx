import { useEffect, useRef, useState } from "react";

type Instrument = {
  instrument_id: number; asset_currency: string; netUnits: string;
  netContracts: string; netCurrentExposureAssetCurrency: string;
  netCurrentExposureAccountCurrency: string; avgLeverage: string;
};
type Mirror = {
  mirror_id: number; positions: Instrument[]; mirrorAvailableCash?: string;
  totals?: Record<string, string>;
};
type Snapshot = {
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
};
type Preview = { preview_id: string; expires_at: string; snapshot: Snapshot };
type PortfolioState = {
  state: string; snapshot: Snapshot | null; connection?: Connection | null;
  last_status: string; message?: string;
};

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

function isSnapshot(value: unknown): value is Snapshot {
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
  catch { throw new Error("eToro response was invalid. No account data was saved."); }
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
    throw new Error(`eToro request was refused or unavailable.${detail}`);
  }
  return value;
}

function isSession(value: unknown): value is Session {
  return object(value) && typeof value.enabled === "boolean" &&
    typeof value.state === "string" && typeof value.last_status === "string" &&
    typeof value.snapshot_available === "boolean" &&
    (value.csrf === undefined || value.csrf === null || typeof value.csrf === "string") &&
    (value.connection === undefined || value.connection === null || object(value.connection));
}

function isPortfolioState(value: unknown): value is PortfolioState {
  return object(value) && typeof value.state === "string" &&
    typeof value.last_status === "string" &&
    (value.snapshot === null || isSnapshot(value.snapshot)) &&
    (value.connection === undefined || value.connection === null || object(value.connection));
}

function timestamp(value: string) {
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime()) ? parsed.toLocaleString() : value;
}

function PositionRows({ label, positions }: { label: string; positions: Instrument[] }) {
  return <section className="etoro-section" aria-label={label}>
    <h3>{label}</h3>
    {positions.length === 0 ? <p>No instrument aggregates reported.</p> : <div className="table-scroll">
      <table><thead><tr>
        <th>Instrument ID</th><th>Net units</th><th>Net contracts</th>
        <th>Exposure ({positions[0].asset_currency})</th><th>Exposure (account currency)</th><th>Average leverage</th>
      </tr></thead><tbody>{positions.map(position => <tr key={position.instrument_id}>
        <td>{position.instrument_id}</td><td>{position.netUnits}</td><td>{position.netContracts}</td>
        <td>{position.netCurrentExposureAssetCurrency}</td>
        <td>{position.netCurrentExposureAccountCurrency}</td><td>{position.avgLeverage}</td>
      </tr>)}</tbody></table>
    </div>}
  </section>;
}

function SnapshotView({ snapshot }: { snapshot: Snapshot }) {
  const totals = snapshot.account_totals;
  const gbp = snapshot.gbp_balance;
  return <div className="etoro-snapshot">
    <p><strong>{snapshot.environment === "real" ? "Real account" : "Demo account"}</strong>
      {" · "}Account ID {snapshot.account_id} · Source snapshot {timestamp(snapshot.source_as_of)} UTC
      {" · "}Account currency {snapshot.account_currency}</p>
    <div className="etoro-warning" role="note">
      This is read-only provider evidence, not a journal entry, custody verification, or buying-power approval.
      Instrument type and individual position legs are not provided by this endpoint. Long and short exposure
      is shown as a net instrument aggregate; copied positions are kept in separate mirror groups.
    </div>
    <section className="etoro-section" aria-label="Account totals">
      <h3>Provider account totals ({snapshot.account_currency})</h3>
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
    {gbp?.status === "provider_reported" ? <section className="etoro-section" aria-label="Provider reported GBP value">
      <h3>Provider-reported GBP total</h3>
      <p><strong>GBP {gbp.amount_gbp}</strong> · Converted from {gbp.source_balance} {gbp.source_currency}
        {" · "}Provider rate {gbp.exchange_rate} {gbp.display_currency} per {gbp.source_currency}</p>
      <p className="position-note">This is a separate account-balance read received {timestamp(gbp.received_at)}.
        The provider did not return a source timestamp. It is not synchronized to the portfolio snapshot
        and is not used to value individual holdings or populate the GBP journal.</p>
    </section> : <section className="etoro-section" aria-label="GBP total unavailable">
      <h3>GBP total unavailable</h3>
      <p>The provider did not return a validated GBP account balance. No conversion was estimated.</p>
    </section>}
    <PositionRows label="Direct instrument aggregates" positions={snapshot.direct_positions} />
    {snapshot.mirrors.map(mirror => <section key={mirror.mirror_id} className="etoro-section"
      aria-label={`Copy relationship ${mirror.mirror_id}`}>
      <h3>Copy relationship {mirror.mirror_id}</h3>
      {mirror.mirrorAvailableCash !== undefined &&
        <p>Mirror available cash ({snapshot.account_currency}): {mirror.mirrorAvailableCash}</p>}
      {mirror.totals && <dl className="etoro-totals">{Object.entries(mirror.totals).map(([name, value]) =>
        <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>}
      <PositionRows label={`Instrument aggregates for copy relationship ${mirror.mirror_id}`}
        positions={mirror.positions} />
    </section>)}
  </div>;
}

export function EToroPortfolioPanel({ embedded = false }: { embedded?: boolean }) {
  const [session, setSession] = useState<Session | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null);
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
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const pendingRequest = useRef<string | null>(null);

  const load = async () => {
    const [currentSession, currentPortfolio] = await Promise.all([
      get("/api/investment/v1/etoro/session", isSession),
      get("/api/investment/v1/etoro/portfolio", isPortfolioState),
    ]);
    setSession(currentSession);
    setPortfolio(currentPortfolio);
  };
  useEffect(() => {
    let active = true;
    Promise.all([
      get("/api/investment/v1/etoro/session", isSession),
      get("/api/investment/v1/etoro/portfolio", isPortfolioState),
    ]).then(([currentSession, currentPortfolio]) => {
      if (active) { setSession(currentSession); setPortfolio(currentPortfolio); }
    }).catch(failure => {
      if (active) setError(failure instanceof Error ? failure.message : "eToro status is unavailable.");
    });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!session?.enabled || session.state !== "startup_refresh_enabled" ||
        session.snapshot_available || session.last_status !== "not_collected") return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const [currentSession, currentPortfolio] = await Promise.all([
          get("/api/investment/v1/etoro/session", isSession),
          get("/api/investment/v1/etoro/portfolio", isPortfolioState),
        ]);
        if (!active) return;
        setSession(currentSession);
        setPortfolio(currentPortfolio);
        if (!currentSession.snapshot_available && currentSession.last_status === "not_collected")
          timer = window.setTimeout(() => { void poll(); }, 1000);
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "eToro startup status is unavailable.");
      }
    };
    timer = window.setTimeout(() => { void poll(); }, 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session?.enabled, session?.state, session?.snapshot_available, session?.last_status]);
  useEffect(() => {
    if (!preview) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview]);

  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "eToro request was unavailable."); }
    finally { setBusy(false); }
  };
  const begin = () => void run(async () => {
    if (!session?.csrf || !environment) throw new Error("Choose an eToro environment and load the local session first.");
    if (readScope === tradeRisk) throw new Error("Verify Read scope or accept broader-key risk for this one-time read.");
    if (!eligibility || !retention) throw new Error("Confirm account eligibility and permitted local display/retention.");
    const requestId = pendingRequest.current ?? crypto.randomUUID();
    pendingRequest.current = requestId;
    const result = await post("/api/investment/v1/etoro/connect", session.csrf, {
      request_id: requestId, environment, read_scope_verified: readScope,
      trade_capable_key_risk_accepted: tradeRisk,
      eligibility_verified: eligibility, retention_rights_verified: retention,
    });
    if (!object(result) || typeof result.preview_id !== "string" ||
        typeof result.expires_at !== "string" || !isSnapshot(result.snapshot))
      throw new Error("eToro preview failed validation. Nothing was saved.");
    setPreview(result as unknown as Preview);
    setBinding(false);
  });
  const confirm = () => void run(async () => {
    if (!session?.csrf || !preview) throw new Error("The eToro preview is unavailable.");
    if (!binding) throw new Error("Confirm that the returned account identifier is the account you selected.");
    if (startupRefresh && !readScope)
      throw new Error("Automatic startup refresh requires verified Read-only key scope.");
    await post("/api/investment/v1/etoro/confirm", session.csrf, {
      preview_id: preview.preview_id, account_binding_confirmed: binding,
      startup_refresh: startupRefresh, refresh_days: startupRefresh ? refreshDays : null,
    });
    setPreview(null); pendingRequest.current = null;
    await load();
  });
  const discard = () => void run(async () => {
    if (!session?.csrf || !preview) throw new Error("The eToro preview is unavailable.");
    const result = await post("/api/investment/v1/etoro/discard", session.csrf, {
      preview_id: preview.preview_id,
    });
    if (!object(result) || typeof result.discarded !== "boolean")
      throw new Error("The eToro preview could not be discarded.");
    setPreview(null); pendingRequest.current = null;
  });
  const revoke = () => void run(async () => {
    if (!session?.csrf || !window.confirm("Revoke automatic eToro startup refresh? The current journal will not change.")) return;
    await post("/api/investment/v1/etoro/revoke", session.csrf, {});
    await load();
  });
  const refresh = () => void run(async () => {
    if (!session?.csrf) throw new Error("The local eToro session is unavailable.");
    const result = await post("/api/investment/v1/etoro/refresh", session.csrf, {});
    if (isPortfolioState(result)) setPortfolio(result);
    else await load();
  });

  const title = embedded ? "Connect an eToro portfolio" : "eToro portfolio";
  return <section className={`etoro-panel${embedded ? " etoro-panel-embedded" : ""}`}
    aria-label={title} aria-busy={busy}>
    {!embedded && <h1>{title}</h1>}
    {embedded && <h2>{title}</h2>}
    <p className="etoro-intro">Read-only portfolio evidence stays on this computer and never enters hosted AI.
      Keys are read from private server-side connection settings only after you request a read; they are never
      entered in this page. Connecting does not change the GBP journal or place orders.</p>
    {error && <p role="alert" className="investment-warnings">{error}</p>}
    {session?.message && <p role="alert" className="investment-warnings">{session.message}</p>}
    {preview ? <div className="recording-preview" aria-label="Review eToro portfolio before saving consent">
      <p>Review the returned account and its source coverage. No portfolio snapshot is stored on disk;
        only your explicit connection permission and account identifier are saved.</p>
      <SnapshotView snapshot={preview.snapshot} />
      <label className="setup-check"><input type="checkbox" checked={binding}
        onChange={event => setBinding(event.target.checked)} />
        I verified account ID {preview.snapshot.account_id} is the eToro account I selected.</label>
      <label className="setup-check"><input type="checkbox" checked={startupRefresh}
        disabled={!readScope} onChange={event => setStartupRefresh(event.target.checked)} />
        Allow one automatic read at each server startup while permission is active.</label>
      {!readScope && startupRefresh && <p role="note">Automatic refresh is unavailable unless Read-only key scope is verified.</p>}
      {startupRefresh && <label>Permission expires in
        <select value={refreshDays} onChange={event => setRefreshDays(Number(event.target.value))}>
          <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
        </select>
      </label>}
      <p className="position-note">The saved permission contains the account ID and consent only.
        The portfolio is held in memory for this dashboard run and is not retained as history.
        Startup refresh never updates journal balances or marks.</p>
      <button type="button" disabled={busy || Date.parse(preview.expires_at) <= now} onClick={confirm}>
        Confirm connection and account
      </button>
      <button type="button" disabled={busy} onClick={discard}>
        Discard preview
      </button>
    </div> : <form className="recording-form etoro-form" onSubmit={event => { event.preventDefault(); begin(); }}>
      <label>eToro environment
        <select value={environment} required onChange={event =>
          setEnvironment(event.target.value as "" | "real" | "demo")}>
          <option value="">Choose explicitly</option><option value="real">Real</option><option value="demo">Demo</option>
        </select>
      </label>
      <fieldset><legend>Connection checks</legend>
        <label className="setup-check"><input type="checkbox" checked={readScope}
          onChange={event => { setReadScope(event.target.checked); if (event.target.checked) setTradeRisk(false); }} />
          I verified this key has Read-only permission.</label>
        <label className="setup-check"><input type="checkbox" checked={tradeRisk}
          onChange={event => { setTradeRisk(event.target.checked); if (event.target.checked) setReadScope(false); }} />
          I accept the risk if this credential can place, modify or cancel orders. This permits one supervised read only.</label>
        <label className="setup-check"><input type="checkbox" checked={eligibility}
          onChange={event => setEligibility(event.target.checked)} />
          I verified my account is eligible for this API use.</label>
        <label className="setup-check"><input type="checkbox" checked={retention}
          onChange={event => setRetention(event.target.checked)} />
          I verified rights for local display and transient in-memory processing; I understand only consent and account ID are saved.</label>
      </fieldset>
      <p className="position-note">The provider API returns account-level values, instrument aggregates and copy groups.
        This view does not identify every instrument class or individual position leg. Pending order details are
        unavailable; only the provider's reserved-cash total is shown. GBP totals use a separate provider read
        and may have no source timestamp. Incompatible evidence is never imported into the GBP journal.</p>
      <button type="submit" disabled={busy || !session?.enabled}>Connect and retrieve once</button>
    </form>}
    {!preview && portfolio?.snapshot && <div className="recording-preview">
      <h2>Latest in-memory portfolio read</h2>
      {portfolio.last_status !== "success" && <p role="status">Last refresh status: {portfolio.last_status}.
        The displayed source snapshot is not refreshed.</p>}
      <SnapshotView snapshot={portfolio.snapshot} />
    </div>}
    {!preview && portfolio && !portfolio.snapshot && session?.state === "startup_refresh_enabled" &&
      <p role="status">{session.last_status === "not_collected"
        ? "Waiting for the one authorized startup read. This screen does not initiate a request."
        : `The automatic startup read did not complete (${session.last_status}). It will not be retried automatically.`}</p>}
    {!preview && session?.connection?.expires_at &&
      <p className="position-note">Startup-read permission expires {timestamp(session.connection.expires_at)}.
        Account ID {session.connection.account_id} · {session.connection.environment} environment.</p>}
    {!preview && session?.state === "startup_refresh_enabled" && <div className="etoro-actions">
      <button type="button" disabled={busy} onClick={refresh}>Refresh once now</button>
      <button type="button" disabled={busy} onClick={revoke}>Revoke startup refresh</button>
    </div>}
    {!preview && session?.state === "expired" &&
      <p role="status">Startup permission expired. The portfolio will not be fetched automatically; reconnect to renew.</p>}
    {!preview && session?.state === "revoked" &&
      <p role="status">Startup permission is revoked. No automatic reads will occur.</p>}
  </section>;
}
