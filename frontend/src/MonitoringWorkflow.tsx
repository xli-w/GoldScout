import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { label, timestamp } from "./api";

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
  enabled: boolean; csrf: string | null; cache_enabled: boolean;
  review_enabled?: boolean; accounts: { account_id: string; provider: string }[];
};
type StagedHolding = { source_product: string; quantity: string };
type ProductMapping = { source_product: string; product_id: string; evidence: string };
type Comparison = { observed: string | null; recorded: string | null; difference: string | null; status: string };
type ReconciledHolding = Comparison & {
  source_product: string; product_id: string | null; mapping_evidence: string | null;
};
type ReconciledAccount = {
  source_account: string; account_id: string | null; observed_at: string; available_at: string;
  freshness: string; comparison_unavailable_reason: string | null; cash_coverage: string;
  holdings_coverage: string; coverage_evidence: string; cash: Comparison;
  holdings: ReconciledHolding[]; missing_recorded_products: string[];
};
type Reconciliation = {
  status: string; revision?: number; generated_at?: string; accounts?: ReconciledAccount[];
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

function ReconciliationSummary({ result }: { result: Reconciliation }) {
  return <section className="reconciliation-summary" aria-label="Reconciliation results">
    <h4>Comparison with recorded journal evidence</h4>
    <p>Status: <strong>{result.status === "matched" ? "Matched at the stated clocks" : label(result.status)}</strong>.
      A match does not establish present custody or settled buying power.</p>
    {result.reason && <p>{label(result.reason)}</p>}
    {result.accounts?.map(account => <article key={account.source_account}>
      <h5>{account.source_account} → {account.account_id ?? "No account mapping"}</h5>
      <p>Observation {timestamp(account.observed_at)}; available {timestamp(account.available_at)}; {label(account.freshness)}.
        Cash coverage: {label(account.cash_coverage)}; holdings coverage: {label(account.holdings_coverage)}.</p>
      {account.comparison_unavailable_reason && <p>Comparison unavailable: {label(account.comparison_unavailable_reason)}.</p>}
      <dl><div><dt>Cash</dt><dd>{label(account.cash.status)}; observed GBP {account.cash.observed ?? "unknown"},
        {" "}recorded GBP {account.cash.recorded ?? "unavailable"}, difference GBP {account.cash.difference ?? "unavailable"}.</dd></div></dl>
      {account.holdings.length > 0 && <div className="table-scroll"><table>
        <thead><tr><th>Observed product</th><th>Journal product</th><th>Observed quantity</th>
          <th>Recorded quantity</th><th>Difference</th><th>Status</th></tr></thead>
        <tbody>{account.holdings.map((holding, index) => <tr key={`${holding.source_product}:${index}`}>
          <td>{holding.source_product}</td><td>{holding.product_id ?? "Unmapped"}</td>
          <td>{holding.observed ?? "unknown"}</td><td>{holding.recorded ?? "unavailable"}</td>
          <td>{holding.difference ?? "unavailable"}</td><td>{label(holding.status)}</td>
        </tr>)}</tbody>
      </table></div>}
      {account.missing_recorded_products.length > 0 && <p>Recorded products not represented in this observation: {account.missing_recorded_products.join(", ")}.</p>}
      <p>Coverage evidence: {account.coverage_evidence}</p>
    </article>)}
    {result.missing_recorded_accounts && result.missing_recorded_accounts.length > 0 &&
      <p>Recorded accounts absent from this observation: {result.missing_recorded_accounts.join(", ")}.</p>}
    {result.limitations && <details><summary>Comparison limitations</summary>
      <ul>{result.limitations.map(item => <li key={item}>{item}</li>)}</ul>
    </details>}
  </section>;
}

export function MonitoringWorkflowPage() {
  const client = useQueryClient();
  const inbox = useQuery({ queryKey: ["monitoring-reviews"],
    queryFn: () => getJson<Inbox>("/api/investment/v1/monitoring/reviews"), retry: false });
  const session = useQuery({ queryKey: ["monitoring-workflow-session"],
    queryFn: () => getJson<Session>("/api/investment/v1/monitoring-workflow/session"), retry: false });
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
  const [holdings, setHoldings] = useState<StagedHolding[]>([]);
  const [productMappings, setProductMappings] = useState<ProductMapping[]>([]);
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
      const observedHoldings = holdings.filter(item => item.source_product.trim() || item.quantity.trim());
      if (observedHoldings.some(item => !item.source_product.trim() || !item.quantity.trim()))
        throw new Error("Complete both the source product and quantity for each holding, or remove that row.");
      const mappings = productMappings.filter(item =>
        item.source_product.trim() || item.product_id.trim() || item.evidence.trim());
      if (mappings.some(item => !item.source_product.trim() || !item.product_id.trim() || !item.evidence.trim()))
        throw new Error("Complete all product mapping fields, or remove that mapping row.");
      const selectedAccountId = accountId || auth?.accounts[0]?.account_id;
      const account = auth?.accounts.find(item => item.account_id === selectedAccountId);
      if (!account) throw new Error("Select an explicitly configured account.");
      const observation: Record<string, unknown> = {
        id: observationId, account_id: account.account_id, provider: account.provider,
        observed_at: observedAt || null, available_at: availableAt, currency: "GBP",
        cash: cash === "" ? null : cash, holdings: observedHoldings,
        coverage, evidence,
      };
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
    <nav className="monitoring-workflow-nav" aria-label="Monitoring workflow sections">
      <a href="#review-inbox">Review inbox</a>
      <a href="#observation-staging">Stage an observation</a>
    </nav>
    <section className="history-group" id="review-inbox">
      <h2>Monitoring review inbox</h2>
      <p>Read-only summaries from the explicitly selected local review store. Reading never syncs or changes it.</p>
      {inbox.isError && <p role="alert">The selected review inbox or its exact portfolio/profile/cache binding is unavailable.</p>}
      {inbox.isPending && <p role="status">Loading the local inbox…</p>}
      {data?.enabled === false && <p>No explicitly selected monitoring review store.</p>}
      {data?.enabled === true && <>
        <p>Portfolio {data.portfolio_id}; profile {data.profile_hash}; inbox revision {data.revision}.
          {data.source_available ? data.sync_current
            ? " Latest sync matches the current monitoring evidence."
            : " Inbox sync is stale; actions are disabled until an explicit sync."
            : " Current monitoring sources are unavailable; actions are disabled."}</p>
        {data.cases.length === 0 ? <p>No synchronized review cases.</p>
          : data.cases.map(item => <ActionRow key={item.case_id} item={item}
            inboxRevision={data.revision} csrf={csrf}
            enabled={Boolean(auth?.enabled && current)} changed={() => void invalidate()} />)}
        <h3>Historical cases</h3>
        {data.cases.filter(item => item.lifecycle !== "active").map(item =>
          <p key={item.case_id}>{item.finding.subject}: {item.lifecycle}; workflow {item.review_status}.</p>)}
        <details><summary>Inbox limitations</summary><ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul></details>
      </>}
    </section>

    <section className="history-group" id="observation-staging">
      <h2>Operator-normalized observation staging</h2>
      <p>Local manual entry only; no provider/network access. Preview compares available GBP evidence first.
        Confirmation appends only to the separate monitoring cache—never the investment journal.</p>
      {session.isError && <p role="alert">Monitoring workflow configuration is unavailable.</p>}
      {auth?.enabled && !auth.cache_enabled &&
        <p>An explicitly initialized monitoring cache is required for observation staging.</p>}
      {auth?.enabled && auth.cache_enabled && <>
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
          <label>Account and product mapping evidence available at (UTC)<input required value={mappingAt}
            placeholder="2026-11-17T11:00:00Z" onChange={event => setMappingAt(event.target.value)} /></label>
          <label>Operator evidence description<input required maxLength={2000} value={evidence}
            onChange={event => setEvidence(event.target.value)} /></label>
          <fieldset className="monitoring-entry-group">
            <legend>Observed holdings</legend>
            <p>Enter only source evidence. Do not enter a row for an unknown quantity; coverage remains explicit.</p>
            {holdings.map((holding, index) => <div className="monitoring-entry-row" key={index}>
              <label>Source product name<input required value={holding.source_product}
                onChange={event => setHoldings(rows => rows.map((row, i) => i === index
                  ? { ...row, source_product: event.target.value } : row))} /></label>
              <label>Observed quantity<input required inputMode="decimal" value={holding.quantity}
                onChange={event => setHoldings(rows => rows.map((row, i) => i === index
                  ? { ...row, quantity: event.target.value } : row))} /></label>
              <button type="button" aria-label={`Remove observed holding ${index + 1}`}
                onClick={() => setHoldings(rows => rows.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
            <button type="button" onClick={() => setHoldings(rows => [...rows, { source_product: "", quantity: "" }])}>
              Add observed holding</button>
          </fieldset>
          <fieldset className="monitoring-entry-group">
            <legend>Explicit product mappings (optional)</legend>
            <p>Mappings are operator-supplied evidence. They are never inferred from names.</p>
            {productMappings.map((mapping, index) => <div className="monitoring-entry-row" key={index}>
              <label>Source product<input required value={mapping.source_product}
                onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, source_product: event.target.value } : row))} /></label>
              <label>Journal product ID<input required value={mapping.product_id}
                onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, product_id: event.target.value } : row))} /></label>
              <label>Evidence for this mapping<input required value={mapping.evidence}
                onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, evidence: event.target.value } : row))} /></label>
              <button type="button" aria-label={`Remove product mapping ${index + 1}`}
                onClick={() => setProductMappings(rows => rows.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
            <button type="button" onClick={() => setProductMappings(rows =>
              [...rows, { source_product: "", product_id: "", evidence: "" }])}>Add product mapping</button>
          </fieldset>
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
          <ReconciliationSummary result={preview.reconciliation} />
          <button type="button" disabled={busy} onClick={() => void confirmStage()}>
            Confirm and stage only to the monitoring cache
          </button>
          <button type="button" disabled={busy} onClick={() => setPreview(null)}>Discard preview</button>
        </div>}
      </>}
      {auth?.enabled && data?.enabled === true && !data.sync_current && (
        <button type="button" disabled={busy || !csrf} onClick={() => void sync()}>
          Sync current findings before actions
        </button>
      )}
      {error && <p role="status">{error}</p>}
    </section>
  </article>;
}
