import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { NavLink } from "react-router-dom";
import { label, reviewTimestamp, timestamp } from "./api";
import { localClock } from "./recording";

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
  review_enabled?: boolean;
  accounts: { account_id: string; name: string; provider: string }[];
  products: { product_id: string; name: string }[];
};
type NamedAccount = Session["accounts"][number];
type NamedProduct = Session["products"][number];
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
  mapping_observed_at: string;
  observation: Record<string, unknown>; reconciliation: Reconciliation;
  expires_at: string; investment_journal_changed: false; already_staged?: boolean;
};

function accountReference(accounts: NamedAccount[], id: string | null) {
  if (id === null) return "No account mapping";
  const account = accounts.find(item => item.account_id === id);
  return account ? `${account.name} (${id})` : id;
}

function productReference(products: NamedProduct[], id: string | null) {
  if (id === null) return "Unmapped";
  const product = products.find(item => item.product_id === id);
  return product ? `${product.name} (${id})` : id;
}

function optionLabel<T extends { name: string }>(items: T[], item: T, id: string) {
  return items.filter(candidate => candidate.name === item.name).length > 1
    ? `${item.name} (${id})` : item.name;
}

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
      <small>First seen {timestamp(item.first_seen_at)}.</small>
      <details className="help-disclosure"><summary>Source and technical details for this check</summary>
        <p>Finding {item.finding.rule}; source journal revision {item.source_portfolio_revision};
          cache revision {item.source_cache_revision ?? "not selected"}.</p>
        <p>Recommendation: {item.finding.recommendation}. Evidence hash: {item.finding.evidence_sha256}.</p>
      </details>
      {item.reviewed_at && <small>Latest action at {timestamp(item.reviewed_at)}{item.note ? `: ${item.note}` : "."}</small>}
    </div>
    <span>Review status: {label(item.review_status)}</span>
    {item.lifecycle === "active" && <form className="workflow-action-form" onSubmit={event => void submit(event)}>
      <label>Action<select value={action} onChange={event => setAction(event.target.value as typeof action)}>
        <option value="reviewed">I reviewed this</option><option value="acknowledged">I noted this</option>
        <option value="deferred">Review later</option>
      </select></label>
      <label>Local note (optional, 500 characters)<input maxLength={500} value={note}
        onChange={event => setNote(event.target.value)} /></label>
      <button type="submit" disabled={!enabled || busy}>Save review status</button>
      <p className="form-note">This only records your review status, not a resolution, correction or permission to trade.</p>
      {error && <p role="alert">{error}</p>}
    </form>}
  </div>;
}

function ReconciliationSummary({ result, accounts, products }: {
  result: Reconciliation; accounts: NamedAccount[]; products: NamedProduct[];
}) {
  return <section className="reconciliation-summary" aria-label="Reconciliation results">
    <h4>Comparison with your portfolio records</h4>
    <p>Status: <strong>{result.status === "matched" ? "Matched at the stated dates and times" : label(result.status)}</strong>.
      A match does not verify today's account balance or money available to spend.</p>
    {result.reason && <p>{label(result.reason)}</p>}
    {result.accounts?.map(account => <article key={account.source_account}>
      <h5>{accountReference(accounts, account.source_account)} → {accountReference(accounts, account.account_id)}</h5>
      <dl className="review-dates">
        <div><dt>Snapshot applies at</dt><dd>{reviewTimestamp(account.observed_at)}</dd></div>
        <div><dt>Source available from</dt><dd>{reviewTimestamp(account.available_at)}</dd></div>
      </dl>
      <p>Age status: {label(account.freshness)}. Cash coverage: {label(account.cash_coverage)};
        investment coverage: {label(account.holdings_coverage)}.</p>
      {account.comparison_unavailable_reason && <p>Comparison unavailable: {label(account.comparison_unavailable_reason)}.</p>}
      <dl><div><dt>Cash</dt><dd>{label(account.cash.status)}; observed GBP {account.cash.observed ?? "unknown"},
        {" "}recorded GBP {account.cash.recorded ?? "unavailable"}, difference GBP {account.cash.difference ?? "unavailable"}.</dd></div></dl>
      {account.holdings.length > 0 && <div className="table-scroll"><table>
        <thead><tr><th>Investment in source</th><th>Recorded investment reference</th><th>Quantity in source</th>
          <th>Recorded quantity</th><th>Difference</th><th>Status</th></tr></thead>
        <tbody>{account.holdings.map((holding, index) => <tr key={`${holding.source_product}:${index}`}>
          <td>{holding.source_product}</td><td>{productReference(products, holding.product_id)}</td>
          <td>{holding.observed ?? "unknown"}</td><td>{holding.recorded ?? "unavailable"}</td>
          <td>{holding.difference ?? "unavailable"}</td><td>{label(holding.status)}</td>
        </tr>)}</tbody>
      </table></div>}
      {account.missing_recorded_products.length > 0 && <p>Recorded investments not represented in this observation: {account.missing_recorded_products
        .map(id => productReference(products, id)).join(", ")}.</p>}
      <p>Coverage evidence: {account.coverage_evidence}</p>
    </article>)}
    {result.missing_recorded_accounts && result.missing_recorded_accounts.length > 0 &&
      <p>Recorded accounts absent from this observation: {result.missing_recorded_accounts
        .map(id => accountReference(accounts, id)).join(", ")}.</p>}
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
      const observedClock = observedAt ? localClock(observedAt) : null;
      const availableClock = localClock(availableAt);
      const mappingClock = localClock(mappingAt);
      const observation: Record<string, unknown> = {
        id: observationId, account_id: account.account_id, provider: account.provider,
        observed_at: observedClock, available_at: availableClock, currency: "GBP",
        cash: cash === "" ? null : cash, holdings: observedHoldings,
        coverage, evidence,
      };
      if (mappings.length) {
        observation.product_mappings = mappings;
        observation.mapping_available_at = mappingClock;
      }
      const attemptRequestId = requestId || crypto.randomUUID();
      setRequestId(attemptRequestId);
      const result = await postJson<StagePreview>(
        "/api/investment/v1/monitoring-workflow/observation/preview", csrf,
        { request_id: attemptRequestId, mapping_observed_at: mappingClock, observation });
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
      setError("Snapshot saved only to the separate monitoring store. Your portfolio journal was not changed.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Confirmation rejected."); }
    finally { setBusy(false); }
  }
  return <article className="investment-view monitoring-workflow">
    <p>Optional: use this page to track checks you have reviewed or compare a source with your recorded portfolio.
      It does not update your accounts or fix differences. You can use the journal without it.</p>
    <nav className="monitoring-workflow-nav" aria-label="Monitoring workflow sections">
      <a href="#review-inbox">Review inbox</a>
      <a href="#observation-staging">Compare a snapshot</a>
    </nav>
    <section className="history-group" id="review-inbox">
      <h2>Monitoring review inbox</h2>
      <p>Track which saved checks you have reviewed. Opening this page does not update the inbox or change your portfolio.</p>
      {inbox.isError && <p role="alert">The review inbox could not be opened or verified against these portfolio records.
        No review status can be saved. Check the terminal window used to start the dashboard for error details.</p>}
      {inbox.isPending && <p role="status">Loading the local inbox…</p>}
      {data?.enabled === false && <p>A review inbox has not been selected for this dashboard.
        This does not mean there are no checks to review. Opening an inbox requires separate startup configuration.
        {" "}<NavLink to="/investment-guide#monitoring-setup">See optional monitoring setup</NavLink>.</p>}
      {data?.enabled === true && <>
        <p>{data.source_available ? data.sync_current
            ? "The inbox matches the latest saved checks."
            : "The inbox is out of date. Update it from the saved checks before recording review statuses."
            : "The information needed for these checks is unavailable. Review actions are disabled; no result should be treated as current."}</p>
        {auth?.enabled && !data.sync_current && <button type="button" disabled={busy || !csrf} onClick={() => void sync()}>
          Update inbox from saved checks
        </button>}
        {auth && !auth.enabled && <p>Review actions are read-only. Separate startup permission is needed to save statuses;
          this page cannot enable it.</p>}
        <details className="help-disclosure"><summary>Inbox identity and revision</summary>
          <p>Portfolio {data.portfolio_id}; profile {data.profile_hash}; inbox revision {data.revision}.</p>
        </details>
        {data.cases.length === 0 ? <p>No checks have been copied to this inbox.</p>
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
      <h2>Compare an account snapshot</h2>
      <p>Enter balances from a source you checked, then preview the differences. Saving adds a separate monitoring snapshot,
        not a portfolio transaction. There is no provider or network access.</p>
      <details className="help-disclosure"><summary>How this differs from recording a transaction</summary>
        <p>A snapshot compares what a source reported with your journal. It does not repair a difference or change the journal.
          The dates keep separate when figures apply, when the source became available, and when account or investment matches
          were established. Enter source times in your computer's local timezone; they are converted to UTC for review.
          If the timezone is unclear, pause rather than guessing.</p>
        <p>The save request reference (attempt ID) identifies this save request. Keep it unchanged when retrying the same snapshot.
          "Complete" means you declare the source coverage complete; it is not independent verification.</p>
      </details>
      {session.isError && <p role="alert">Snapshot permissions could not be checked. Nothing can be saved.
        Check the terminal window used to start the dashboard for error details.</p>}
      {auth && !auth.enabled && <p>Snapshot entry is not enabled. It needs a separately configured monitoring store and startup permission;
        opening this page does not grant access or contact a provider.
        {" "}<NavLink to="/investment-guide#monitoring-setup">See optional monitoring setup</NavLink>.</p>}
      {auth?.enabled && !auth.cache_enabled &&
        <p>No monitoring snapshot store has been selected. Configure the separate store before saving comparisons;
          your portfolio journal cannot be used in its place.</p>}
      {auth?.enabled && auth.cache_enabled && <>
        {!preview && <form className="recording-form" onSubmit={event => void previewObservation(event)}>
          <label>Account to compare<select required value={accountId || auth.accounts[0]?.account_id || ""}
            disabled={busy} onChange={event => setAccountId(event.target.value)}>
            {auth.accounts.map(item => <option key={item.account_id} value={item.account_id}>
              {optionLabel(auth.accounts, item, item.account_id)}</option>)}
          </select></label>
          <label>Snapshot reference (source observation ID)<input required maxLength={200} value={observationId}
            aria-label="Snapshot reference (source observation ID)" aria-describedby="snapshot-reference-help"
            disabled={busy} onChange={event => setObservationId(event.target.value)} />
            <small className="field-help" id="snapshot-reference-help">A stable reference for this source snapshot, not an account number.
              Reuse it for the same snapshot; do not reuse it for different figures.</small></label>
          <details className="help-disclosure"><summary>Advanced: save request reference and retries</summary>
            <label>Save request reference (attempt ID)<input maxLength={128} value={requestId}
              disabled={busy} onChange={event => setRequestId(event.target.value)} /></label>
            <p>Leave blank to generate one on preview. Keep it unchanged when retrying the same snapshot.
              This identifies the request, not the source statement.</p>
          </details>
          <label>When do these figures apply? (leave blank if unknown)<input type="datetime-local" value={observedAt}
            disabled={busy} onChange={event => setObservedAt(event.target.value)} /></label>
          <label>When was this source available?<input required type="datetime-local" value={availableAt}
            disabled={busy} onChange={event => setAvailableAt(event.target.value)} /></label>
          <label>Cash (GBP decimal; blank if unknown)<input inputMode="decimal" value={cash}
            disabled={busy} onChange={event => setCash(event.target.value)} /></label>
          <label>How much does the source cover?<select aria-label="How much does the source cover?" aria-describedby="snapshot-coverage-help"
            value={coverage} disabled={busy} onChange={event => setCoverage(event.target.value as typeof coverage)}>
            <option value="complete">All cash and holdings (declared by you)</option>
            <option value="partial">Some information only</option><option value="unknown">I cannot establish coverage</option>
          </select><small className="field-help" id="snapshot-coverage-help">An empty holdings list does not prove that the account has no investments.
            Unknown cash or quantity is not zero.</small></label>
          <label>When was the account or investment match established?<input required type="datetime-local" value={mappingAt}
            aria-describedby="snapshot-mapping-help" disabled={busy} onChange={event => setMappingAt(event.target.value)} />
            <small className="field-help" id="snapshot-mapping-help">When the source identifying this account and any matched investments was available.
              Names alone are not proof of a match. If unknown, pause rather than guessing.</small></label>
          <label>Where did these figures come from?<input required maxLength={2000} value={evidence}
            disabled={busy} onChange={event => setEvidence(event.target.value)} /></label>
          <p className="form-note">Enter each source time in your computer's local timezone. The preview shows the converted time
            as well. Keep the time figures apply separate from when the source became available.</p>
          <fieldset className="monitoring-entry-group">
            <legend>Investments listed in the source</legend>
            <p>Enter only source evidence. Do not enter a row for an unknown quantity; coverage remains explicit.</p>
            {holdings.map((holding, index) => <div className="monitoring-entry-row" key={index}>
              <label>Investment name in source<input required value={holding.source_product}
                disabled={busy} onChange={event => setHoldings(rows => rows.map((row, i) => i === index
                  ? { ...row, source_product: event.target.value } : row))} /></label>
              <label>Number of units in source<input required inputMode="decimal" value={holding.quantity}
                disabled={busy} onChange={event => setHoldings(rows => rows.map((row, i) => i === index
                  ? { ...row, quantity: event.target.value } : row))} /></label>
              <button type="button" aria-label={`Remove observed holding ${index + 1}`}
                disabled={busy} onClick={() => setHoldings(rows => rows.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
            <button type="button" disabled={busy} onClick={() => setHoldings(rows => [...rows, { source_product: "", quantity: "" }])}>
              Add observed holding</button>
          </fieldset>
          <fieldset className="monitoring-entry-group">
            <legend>Match source investments to your records (optional)</legend>
            <p>Only add matches you can support with a source. They are never guessed from similar names.
              An unmatched investment cannot be fully compared.</p>
            {productMappings.map((mapping, index) => <div className="monitoring-entry-row" key={index}>
              <label>Investment name to match<input required value={mapping.source_product}
                disabled={busy} onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, source_product: event.target.value } : row))} /></label>
              <label>Recorded investment<select required value={mapping.product_id}
                disabled={busy} onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, product_id: event.target.value } : row))}>
                <option value="">Choose an investment</option>
                {auth.products.map(product => <option key={product.product_id} value={product.product_id}>
                  {optionLabel(auth.products, product, product.product_id)}</option>)}
              </select></label>
              <label>Source supporting this match<input required value={mapping.evidence}
                disabled={busy} onChange={event => setProductMappings(rows => rows.map((row, i) => i === index
                  ? { ...row, evidence: event.target.value } : row))} /></label>
              <button type="button" aria-label={`Remove product mapping ${index + 1}`}
                disabled={busy} onClick={() => setProductMappings(rows => rows.filter((_, i) => i !== index))}>Remove</button>
            </div>)}
            {auth.products.length === 0
              ? <p>No recorded investments are available to match. Source holdings can still be compared without a match.</p>
              : <button type="button" disabled={busy} onClick={() => setProductMappings(rows =>
                [...rows, { source_product: "", product_id: "", evidence: "" }])}>Add investment match</button>}
          </fieldset>
          <button type="submit" disabled={busy || !csrf}>Preview comparison</button>
        </form>}
        {preview && <div className="recording-preview observation-preview" role="region" aria-label="Observation reconciliation preview">
          <h3>Check this comparison before saving</h3>
          <p>{preview.already_staged ? "This identical save request is already in the monitoring store."
            : "This is a preview; it has not saved this request."} Your portfolio journal is not changed.</p>
          <p>Preview valid until {reviewTimestamp(preview.expires_at)}.</p>
          <dl className="review-dates">
            <div><dt>Snapshot applies at</dt><dd>{reviewTimestamp(typeof preview.observation.observed_at === "string" ? preview.observation.observed_at : null)}</dd></div>
            <div><dt>Source available from</dt><dd>{reviewTimestamp(typeof preview.observation.available_at === "string" ? preview.observation.available_at : null)}</dd></div>
            <div><dt>Account and investment match known from</dt><dd>{reviewTimestamp(preview.mapping_observed_at)}</dd></div>
          </dl>
          <details className="help-disclosure"><summary>Exact snapshot and technical references</summary>
            <p>Journal revision {preview.journal_revision}; cache revision {preview.cache_revision}.
              Attempt ID: {preview.request_id}{preview.already_staged ? " (identical attempt already present)" : ""}.</p>
            <pre>{JSON.stringify(preview.observation, null, 2)}</pre>
          </details>
          <ReconciliationSummary result={preview.reconciliation} accounts={auth.accounts} products={auth.products} />
          <button type="button" disabled={busy} onClick={() => void confirmStage()}>
            Confirm and save monitoring snapshot only
          </button>
          <button type="button" disabled={busy} onClick={() => setPreview(null)}>Edit comparison</button>
        </div>}
      </>}
      {error && <p role="status">{error}</p>}
    </section>
  </article>;
}
