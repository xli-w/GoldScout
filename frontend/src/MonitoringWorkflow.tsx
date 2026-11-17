import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { timestamp } from "./api";

type ReviewCase = {
  case_id: string; finding_id: string; finding: {
    rule: string; subject: string; status: string; explanation: string;
    recommendation: string; evidence_sha256: string;
  };
  lifecycle: "active" | "inactive" | "superseded";
  first_seen_at: string; last_seen_at: string;
  source_portfolio_revision: number; source_cache_revision: number | null;
  review_status: string; reviewed_at: string | null; note: string | null;
};
type Inbox = { enabled: false } | {
  enabled: true; portfolio_id: string; profile_hash: string; revision: number;
  cases: ReviewCase[]; sync_current: boolean; source_available: boolean;
  limitations: string[];
};
type Session = {
  enabled: boolean; authenticated: boolean; csrf: string | null; cache_enabled: boolean;
  review_enabled?: boolean; accounts: { account_id: string; provider: string }[];
};
type Reconciliation = {
  status: string; revision?: number; generated_at?: string; accounts?: unknown[];
  missing_recorded_accounts?: string[]; limitations?: string[]; reason?: string;
};
type StagePreview = {
  preview_id: string; request_id: string; journal_revision: number; cache_revision: number;
  observation: Record<string, unknown>; reconciliation: Reconciliation;
  expires_at: string; investment_journal_changed: false; already_staged?: boolean;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error("The local monitoring workflow is unavailable.");
  return body as T;
}

async function postJson<T>(path: string, csrf: string, payload: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
    body: JSON.stringify(payload), cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok) throw new Error(
    typeof body?.error === "string" ? body.error.replaceAll("_", " ") : "Request rejected.");
  return body as T;
}

function ActionRow({ item, inboxRevision, csrf, enabled, changed }: {
  item: ReviewCase; inboxRevision: number; csrf: string; enabled: boolean; changed: () => void;
}) {
  const [action, setAction] = useState<"acknowledged" | "deferred" | "reviewed">("reviewed");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      await postJson("/api/investment/v1/monitoring-workflow/action", csrf, {
        case_id: item.case_id, request_id: crypto.randomUUID(),
        expected_revision: inboxRevision, action, note,
      });
      setNote("");
      changed();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Action rejected."); }
    finally { setBusy(false); }
  }
  return <div className="review-case">
    <div><strong>{item.finding.subject}: {item.finding.status}</strong>
      <p>{item.finding.explanation}</p>
      <small>Finding {item.finding.rule}; first seen {timestamp(item.first_seen_at)};
        source journal revision {item.source_portfolio_revision};
        cache revision {item.source_cache_revision ?? "not selected"}.</small>
      <details><summary>Stored finding summary</summary>
        <p>Recommendation: {item.finding.recommendation}. Evidence hash: {item.finding.evidence_sha256}.</p>
        <p>Workflow status is not resolution, accounting correction or trade approval.</p>
      </details>
      {item.reviewed_at && <small>Latest action at {timestamp(item.reviewed_at)}{item.note ? `: ${item.note}` : "."}</small>}
    </div>
    <span>Workflow: {item.review_status}</span>
    {item.lifecycle === "active" && <form className="workflow-action-form" onSubmit={event => void submit(event)}>
      <label>Action<select value={action} onChange={event => setAction(event.target.value as typeof action)}>
        <option value="reviewed">Reviewed</option><option value="acknowledged">Acknowledged</option>
        <option value="deferred">Deferred</option>
      </select></label>
      <label>Local note (optional, 500 characters)<input maxLength={500} value={note}
        onChange={event => setNote(event.target.value)} /></label>
      <button type="submit" disabled={!enabled || busy}>Append action</button>
      {error && <p role="alert">{error}</p>}
    </form>}
  </div>;
}

export function MonitoringWorkflowPage() {
  const client = useQueryClient();
  const inbox = useQuery({ queryKey: ["monitoring-reviews"],
    queryFn: () => getJson<Inbox>("/api/investment/v1/monitoring/reviews"), retry: false });
  const session = useQuery({ queryKey: ["monitoring-workflow-session"],
    queryFn: () => getJson<Session>("/api/investment/v1/monitoring-workflow/session"), retry: false });
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<StagePreview | null>(null);
  const [requestId, setRequestId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [observationId, setObservationId] = useState("");
  const [observedAt, setObservedAt] = useState("");
  const [availableAt, setAvailableAt] = useState("");
  const [mappingAt, setMappingAt] = useState("");
  const [cash, setCash] = useState("");
  const [coverage, setCoverage] = useState<"complete" | "partial" | "unknown">("partial");
  const [evidence, setEvidence] = useState("");
  const [holdings, setHoldings] = useState("[]");
  const [productMappings, setProductMappings] = useState("[]");
  const data = inbox.data;
  const auth = session.data;
  const csrf = auth?.csrf || "";
  const current = data?.enabled === true && data.sync_current;

  async function invalidate() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ["monitoring-reviews"] }),
      client.invalidateQueries({ queryKey: ["investment"] }),
      client.invalidateQueries({ queryKey: ["monitoring"] }),
    ]);
  }
  async function login(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await postJson("/api/investment/v1/monitoring-workflow/login", csrf, { password });
      setPassword("");
      await client.invalidateQueries({ queryKey: ["monitoring-workflow-session"] });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sign-in rejected."); }
    finally { setBusy(false); }
  }
  async function sync() {
    setBusy(true); setError("");
    try {
      await postJson("/api/investment/v1/monitoring-workflow/sync", csrf, {});
      await invalidate();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Sync rejected."); }
    finally { setBusy(false); }
  }
  async function previewObservation(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setPreview(null);
    try {
      const observedHoldings: unknown = JSON.parse(holdings);
      const mappings: unknown = JSON.parse(productMappings);
      const selectedAccountId = accountId || auth?.accounts[0]?.account_id;
      const account = auth?.accounts.find(item => item.account_id === selectedAccountId);
      if (!account) throw new Error("Select an explicitly configured account.");
      const observation: Record<string, unknown> = {
        id: observationId, account_id: account.account_id, provider: account.provider,
        observed_at: observedAt || null, available_at: availableAt, currency: "GBP",
        cash: cash === "" ? null : cash, holdings: observedHoldings,
        coverage, evidence,
      };
      if (!Array.isArray(mappings)) throw new Error("Product mappings must be a JSON list.");
      if (mappings.length) {
        observation.product_mappings = mappings;
        observation.mapping_available_at = mappingAt;
      }
      const attemptRequestId = requestId || crypto.randomUUID();
      setRequestId(attemptRequestId);
      const result = await postJson<StagePreview>(
        "/api/investment/v1/monitoring-workflow/observation/preview", csrf,
        { request_id: attemptRequestId, mapping_observed_at: mappingAt, observation });
      setPreview(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Preview rejected."); }
    finally { setBusy(false); }
  }
  async function confirmStage() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await postJson("/api/investment/v1/monitoring-workflow/observation/confirm", csrf,
        { preview_id: preview.preview_id });
      setPreview(null);
      setRequestId("");
      await invalidate();
      setError("Observation staged only to the monitoring cache. The investment journal was not changed.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Confirmation rejected."); }
    finally { setBusy(false); }
  }
  return <article className="investment-view monitoring-workflow">
    <section className="history-group">
      <h2>Monitoring review inbox</h2>
      <p>Read-only summaries from the explicitly selected local review store. Reading never syncs or changes it.</p>
      {inbox.isError && <p role="alert">The selected review inbox or its exact portfolio/profile/cache binding is unavailable.</p>}
      {inbox.isPending && <p role="status">Loading the local inbox…</p>}
      {data?.enabled === false && <p>No explicitly selected monitoring review store.</p>}
      {data?.enabled === true && <>
        <p>Portfolio {data.portfolio_id}; profile {data.profile_hash}; inbox revision {data.revision}.
          {data.source_available ? data.sync_current
            ? " Latest sync matches the current monitoring evidence."
            : " Inbox sync is stale; actions are disabled until an authenticated explicit sync."
            : " Current monitoring sources are unavailable; actions are disabled."}</p>
        {data.cases.length === 0 ? <p>No synchronized review cases.</p>
          : data.cases.map(item => <ActionRow key={item.case_id} item={item}
            inboxRevision={data.revision} csrf={csrf}
            enabled={Boolean(auth?.authenticated && current)} changed={() => void invalidate()} />)}
        <h3>Historical cases</h3>
        {data.cases.filter(item => item.lifecycle !== "active").map(item =>
          <p key={item.case_id}>{item.finding.subject}: {item.lifecycle}; workflow {item.review_status}.</p>)}
        <details><summary>Inbox limitations</summary><ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul></details>
      </>}
    </section>

    <section className="history-group">
      <h2>Operator-normalized observation staging</h2>
      <p>Local manual entry only; no provider/network access. Preview compares available GBP evidence first.
        Confirmation appends only to the separate monitoring cache—never the investment journal.</p>
      {session.isError && <p role="alert">Monitoring workflow configuration is unavailable.</p>}
      {auth?.enabled && !auth.authenticated && <form className="recording-form" onSubmit={event => void login(event)}>
        <label>Separate monitoring-workflow password<input type="password" autoComplete="current-password"
          value={password} onChange={event => setPassword(event.target.value)} /></label>
        <button type="submit" disabled={busy || !csrf}>Sign in to workflow actions</button>
      </form>}
      {auth?.enabled && auth.authenticated && !auth.cache_enabled &&
        <p>An explicitly initialized monitoring cache is required for observation staging.</p>}
      {auth?.enabled && auth.authenticated && auth.cache_enabled && <>
        <form className="recording-form" onSubmit={event => void previewObservation(event)}>
          <label>Configured local account<select required value={accountId || auth.accounts[0]?.account_id || ""}
            onChange={event => setAccountId(event.target.value)}>
            {auth.accounts.map(item => <option key={item.account_id} value={item.account_id}>
              {item.account_id} / {item.provider}</option>)}
          </select></label>
          <label>Source observation ID<input required maxLength={200} value={observationId}
            onChange={event => setObservationId(event.target.value)} /></label>
          <label>Cache attempt ID (keep unchanged for retries)<input maxLength={128} value={requestId}
            onChange={event => setRequestId(event.target.value)} /></label>
          <label>Observed at (explicit UTC; leave blank if unknown)<input value={observedAt}
            placeholder="2026-11-17T12:00:00Z" onChange={event => setObservedAt(event.target.value)} /></label>
          <label>Available at (explicit UTC)<input required value={availableAt}
            placeholder="2026-11-17T12:05:00Z" onChange={event => setAvailableAt(event.target.value)} /></label>
          <label>Cash (GBP decimal; blank if unknown)<input inputMode="decimal" value={cash}
            onChange={event => setCash(event.target.value)} /></label>
          <label>Coverage<select value={coverage} onChange={event => setCoverage(event.target.value as typeof coverage)}>
            <option value="complete">Complete (operator-declared)</option>
            <option value="partial">Partial</option><option value="unknown">Unknown</option>
          </select></label>
          <label>Mapping evidence observed at (UTC)<input required value={mappingAt}
            placeholder="2026-11-17T11:00:00Z" onChange={event => setMappingAt(event.target.value)} /></label>
          <label>Operator evidence description<input required maxLength={2000} value={evidence}
            onChange={event => setEvidence(event.target.value)} /></label>
          <label>Holdings JSON list (source_product and quantity)<textarea value={holdings}
            onChange={event => setHoldings(event.target.value)} /></label>
          <label>Explicit product mappings JSON list (optional)<textarea value={productMappings}
            onChange={event => setProductMappings(event.target.value)} /></label>
          <button type="submit" disabled={busy || !csrf}>Preview and reconcile</button>
        </form>
        {preview && <div className="recording-preview observation-preview" role="region" aria-label="Observation reconciliation preview">
          <h3>Preview only—nothing has been staged</h3>
          <p>Journal revision {preview.journal_revision}; cache revision {preview.cache_revision};
            preview expires {timestamp(preview.expires_at)}. Investment journal change: no.</p>
          <p>Attempt ID: {preview.request_id}{preview.already_staged ? " (identical attempt already present)" : ""}</p>
          <details><summary>Normalized observation to be staged</summary>
            <pre>{JSON.stringify(preview.observation, null, 2)}</pre>
          </details>
          <pre>{JSON.stringify(preview.reconciliation, null, 2)}</pre>
          <button type="button" disabled={busy} onClick={() => void confirmStage()}>
            Confirm and stage only to the monitoring cache
          </button>
          <button type="button" disabled={busy} onClick={() => setPreview(null)}>Discard preview</button>
        </div>}
      </>}
      {auth?.enabled && auth.authenticated && data?.enabled === true && !data.sync_current && (
        <button type="button" disabled={busy || !csrf} onClick={() => void sync()}>
          Sync current findings before actions
        </button>
      )}
      {error && <p role="status">{error}</p>}
    </section>
  </article>;
}
