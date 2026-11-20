import Decimal from "decimal.js";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { planningConfirm, planningPreview, planningSession, type GoalInput, type PlanningGoal, type PlanningPreview, type PlanningSession } from "./planning-api";
import { pounds, timestamp } from "./api";
import { goalScenario } from "./planning-scenarios";
import { loadInvestment, type InvestmentSnapshot } from "./InvestmentView";

type DraftGoal = GoalInput & { key: string };
const emptyGoal = (): DraftGoal => ({
  key: crypto.randomUUID().replaceAll("-", ""),
  name: "", target_amount_gbp: null, target_date: null, notes: "",
  purpose: "not_set", access_date: null, liquidity: "unknown",
  loss_tolerance: "not_set", current_amount_gbp: null, monthly_contribution_gbp: null,
  linked_portfolio_id: null, linked_account_ids: [],
});
const draft = (goal: PlanningGoal): DraftGoal => ({
  ...goal, linked_account_ids: [...goal.linked_account_ids], key: goal.id,
});

function recordedAccountValues(data: InvestmentSnapshot | null) {
  if (!data || data.source_mode !== "recorded" || !data.complete ||
      data.equity_gbp === null || data.as_of === null ||
      data.accounts.some(account => account.cash_gbp === null) ||
      data.holdings.some(holding => holding.value_gbp === null)) return null;
  const allValues = new Map<string, Decimal>();
  for (const account of data.accounts)
    allValues.set(account.id, new Decimal(account.cash_gbp!));
  for (const holding of data.holdings)
    allValues.set(holding.account_id,
      (allValues.get(holding.account_id) ?? new Decimal(0)).plus(holding.value_gbp!));
  const total = [...allValues.values()].reduce(
    (value, amount) => value.plus(amount), new Decimal(0));
  if (!total.eq(data.equity_gbp)) return null;
  return new Map(data.accounts.filter(account => account.status === "active")
    .map(account => [account.id, allValues.get(account.id) ?? new Decimal(0)]));
}

function linkedGoalValue(goal: Pick<GoalInput, "linked_portfolio_id" | "linked_account_ids">,
  data: InvestmentSnapshot | null, values: Map<string, Decimal> | null) {
  if (!goal.linked_account_ids.length || !data || !values ||
      goal.linked_portfolio_id !== data.portfolio_id) return null;
  let total = new Decimal(0);
  for (const id of goal.linked_account_ids) {
    const value = values.get(id);
    if (!value) return null;
    total = total.plus(value);
  }
  return total;
}

export function PlanningProfilePage() {
  const [session, setSession] = useState<PlanningSession | null>(null);
  const [goals, setGoals] = useState<DraftGoal[]>([]);
  const [portfolio, setPortfolio] = useState<InvestmentSnapshot | null>(null);
  const [portfolioLoading, setPortfolioLoading] = useState(true);
  const [portfolioError, setPortfolioError] = useState("");
  const [preview, setPreview] = useState<PlanningPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  async function reload() {
    const result = await planningSession();
    setSession(result);
    setGoals(result.goals.map(draft));
    setPreview(null);
  }

  async function reloadPortfolio(signal?: AbortSignal) {
    setPortfolioLoading(true);
    setPortfolioError("");
    try {
      const result = await loadInvestment(signal ?? new AbortController().signal);
      if (!signal?.aborted) setPortfolio(result);
    } catch (reason) {
      if (!signal?.aborted) {
        setPortfolio(null);
        setPortfolioError(reason instanceof Error
          ? reason.message : "Recorded account values are unavailable.");
      }
    } finally {
      if (!signal?.aborted) setPortfolioLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    planningSession().then(result => {
      if (!active) return;
      setSession(result);
      setGoals(result.goals.map(draft));
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "Local planning is unavailable.");
    });
    void reloadPortfolio(controller.signal);
    return () => { active = false; controller.abort(); };
  }, []);

  const accountValues = useMemo(() => recordedAccountValues(portfolio), [portfolio]);
  const portfolioAccounts = portfolio?.source_mode === "recorded"
    ? portfolio.accounts.filter(account => account.status === "active" && account.cash_gbp !== null)
    : [];
  const duplicateLinks = useMemo(() => {
    const ids = goals.flatMap(goal => goal.linked_account_ids);
    return new Set(ids.filter((id, index) => ids.indexOf(id) !== index));
  }, [goals]);

  function updateGoal(key: string,
    field: Exclude<keyof GoalInput, "linked_portfolio_id" | "linked_account_ids">, value: string) {
    setGoals(current => current.map(goal => goal.key === key
      ? { ...goal, [field]: field === "target_amount_gbp" || field === "target_date"
        ? value || null : value }
      : goal));
    setPreview(null);
    setSaved("");
  }

  function updateLinkedAccount(key: string, accountId: string, checked: boolean) {
    setGoals(current => current.map(goal => {
      if (goal.key !== key) return goal;
      const linked_account_ids = checked
        ? [...goal.linked_account_ids, accountId]
        : goal.linked_account_ids.filter(id => id !== accountId);
      return {
        ...goal,
        current_amount_gbp: checked || goal.linked_account_ids.length > 0
          ? null : goal.current_amount_gbp,
        linked_account_ids,
        linked_portfolio_id: linked_account_ids.length
          ? portfolio?.portfolio_id ?? goal.linked_portfolio_id : null,
      };
    }));
    setPreview(null);
    setSaved("");
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!session?.enabled || !session.csrf || !session.profile_hash || busy) return;
    setBusy(true); setError(""); setSaved(""); setPreview(null);
    try {
      const result = await planningPreview(session.csrf, {
        request_id: crypto.randomUUID(),
        expected_revision: session.revision,
        expected_hash: session.profile_hash,
        goals: goals.map(({ key: _key, ...goal }) => goal),
        expected_portfolio_id: goals.some(goal => goal.linked_account_ids.length > 0)
          ? portfolio?.portfolio_id ?? null : null,
        expected_journal_revision: goals.some(goal => goal.linked_account_ids.length > 0)
          ? portfolio?.revision ?? null : null,
      });
      setPreview(result);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Goal preview was rejected.";
      setError(message);
      if (message.includes("planning portfolio changed")) void reloadPortfolio();
    } finally { setBusy(false); }
  }

  async function confirm() {
    if (!session?.csrf || !preview || busy) return;
    setBusy(true); setError(""); setSaved("");
    try {
      const result = await planningConfirm(session.csrf, preview.preview_id);
      setSaved(`Saved as local planning revision ${result.revision}.`);
      try { await reload(); }
      catch {
        setError("The save was confirmed, but the latest profile could not be reloaded. Keep this page open and retry.");
      }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Goal save was rejected.";
      setError(message);
      if (message.includes("planning portfolio changed")) {
        setPreview(null);
        void reloadPortfolio();
      }
    } finally { setBusy(false); }
  }

  if (!session && !error) return <p role="status">Loading local planning profile…</p>;
  if (session && !session.enabled) return <section className="record-review">
    <h2>Local planning is not enabled for this launch</h2>
    <p>Use the default workspace or restart with <code>--planning-profile &lt;path&gt;</code> to select a separate goals file.
      No profile is created by viewing this page.</p>
  </section>;

  return <section className="planning-profile">
    <p className="planning-boundary">
      Optional goals are saved in a separate local file and are not sent to hosted AI.
      You may associate each whole account with at most one goal; this does not move or reserve money.
      Linked amounts appear only when the recorded portfolio has a complete value that reconciles to its account records.
      Scenarios assume no growth, interest, fees, tax or withdrawals, and are not forecasts or recommendations.
    </p>
    {portfolioError && <p role="status">Portfolio values could not be loaded: {portfolioError}
      {" "}<button type="button" onClick={() => void reloadPortfolio()}>Retry portfolio values</button>
    </p>}
    {duplicateLinks.size > 0 && <p role="alert">
      A saved account is linked to more than one goal. No linked goal value is shown until the duplicate assignments are removed.
    </p>}
    {session && <p className="planning-revision">Saved profile revision {session.revision}. No profile file is created until you confirm a save.</p>}
    {error && <div role="alert" className="error-state"><p>{error}</p>
      {session ? <button type="button" disabled={busy} onClick={() => {
        setError(""); void reload().catch(reason =>
          setError(reason instanceof Error ? reason.message : "Local planning is unavailable."));
      }}>Reload profile</button> : <button type="button" onClick={() => {
        setError(""); void reload().catch(reason =>
          setError(reason instanceof Error ? reason.message : "Local planning is unavailable."));
      }}>Retry</button>}
    </div>}
    {saved && <p role="status" className="success-message">{saved}</p>}
    {session && session.enabled && <form className="recording-form" onSubmit={submit}>
      <fieldset disabled={busy || Boolean(preview)}>
        <legend>Your goals</legend>
        {goals.length === 0 && <p>No goals are saved. Add one if it is useful; this profile is optional.</p>}
        {goals.map((goal, index) => <div className="planning-goal" key={goal.key}>
          <div className="recording-row">
            <label>Goal name
              <input required maxLength={80} value={goal.name}
                onChange={event => updateGoal(goal.key, "name", event.target.value)} />
            </label>
            <label>Target amount (GBP, optional)
              <input inputMode="decimal" maxLength={18} pattern="(?=.*[1-9])(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?"
                title="Use a positive GBP amount with up to two decimal places, without commas or currency symbols."
                value={goal.target_amount_gbp ?? ""}
                onChange={event => updateGoal(goal.key, "target_amount_gbp", event.target.value)} />
            </label>
            <label>Target date (optional)
              <input type="date" value={goal.target_date ?? ""}
                onChange={event => updateGoal(goal.key, "target_date", event.target.value)} />
            </label>
            <label>Purpose
              <select value={goal.purpose} onChange={event =>
                updateGoal(goal.key, "purpose", event.target.value as GoalInput["purpose"])}>
                <option value="not_set">Choose later</option>
                <option value="first_home">First home</option>
                <option value="retirement">Retirement</option>
                <option value="education">Education</option>
                <option value="major_purchase">Major purchase</option>
                <option value="financial_resilience">Financial resilience</option>
                <option value="other">Other</option>
              </select>
            </label>
            <label>Earliest access date (optional)
              <input type="date" value={goal.access_date ?? ""}
                onChange={event => updateGoal(goal.key, "access_date", event.target.value)} />
            </label>
            <label>How soon might you need access?
              <select value={goal.liquidity} onChange={event =>
                updateGoal(goal.key, "liquidity", event.target.value as GoalInput["liquidity"])}>
                <option value="unknown">Not sure</option>
                <option value="immediate">At any time</option>
                <option value="within_1_year">Within one year</option>
                <option value="within_5_years">Within five years</option>
                <option value="flexible">Flexible</option>
              </select>
            </label>
              <fieldset className="planning-linked-accounts">
                <legend>Accounts associated with this goal (optional)</legend>
                <p>Each account's full recorded value can be assigned to only one goal here. Assigning an account does not earmark or transfer its money.</p>
                {portfolioLoading ? <p role="status">Loading recorded accounts…</p>
                  : portfolioError ? <p>Account links are unavailable until the portfolio can be loaded.</p>
                  : portfolio?.source_mode !== "recorded" ? <p>Only recorded accounts can be linked; synthetic examples are not used.</p>
                  : goal.linked_portfolio_id !== null &&
                    goal.linked_portfolio_id !== portfolio.portfolio_id ? <>
                    <p role="alert">These saved account links belong to a different portfolio and are not used.</p>
                    <p>Saved account references: {goal.linked_account_ids.join(", ")}</p>
                    <button type="button" onClick={() => {
                      setGoals(current => current.map(item => item.key === goal.key
                        ? { ...item, current_amount_gbp: null,
                          linked_portfolio_id: null, linked_account_ids: [] } : item));
                      setPreview(null); setSaved("");
                    }}>Remove saved account links</button>
                  </> : portfolioAccounts.length === 0
                    ? <p>No opened active accounts are available to link.</p>
                    : portfolioAccounts.map(account => {
                      const assignedElsewhere = goals.some(item =>
                        item.key !== goal.key && item.linked_account_ids.includes(account.id));
                      const checked = goal.linked_account_ids.includes(account.id);
                      return <label key={account.id}>
                        <input type="checkbox" aria-label={`${account.name} (${account.id})`}
                          checked={checked}
                          disabled={assignedElsewhere && !checked}
                          onChange={event => updateLinkedAccount(goal.key, account.id, event.target.checked)} />
                        {account.name} ({account.id}){assignedElsewhere ? " — already assigned to another goal" : ""}
                      </label>;
                    })}
                {goal.linked_account_ids.some(id =>
                  !portfolioAccounts.some(account => account.id === id)) &&
                  <p role="alert">One or more saved links are not active accounts in this portfolio.
                    {" "}<button type="button" onClick={() => {
                      setGoals(current => current.map(item => item.key === goal.key
                        ? { ...item, current_amount_gbp: null,
                          linked_portfolio_id: null, linked_account_ids: [] } : item));
                      setPreview(null); setSaved("");
                    }}>Remove unavailable links</button>
                  </p>}
                {goal.linked_account_ids.length > 0 && <>
                  <p>{linkedGoalValue(goal, portfolio, accountValues) === null
                    ? "Linked account amount unavailable until this portfolio has a complete, matching recorded value."
                    : `Linked recorded amount: ${pounds(linkedGoalValue(goal, portfolio, accountValues)!.toString())} at ${timestamp(portfolio?.as_of ?? null)}.`}</p>
                  {goal.linked_account_ids.some(id => duplicateLinks.has(id)) &&
                    <p role="alert">Remove duplicate assignments before relying on this goal amount.</p>}
                </>}
              </fieldset>
            <label>Comfort with possible losses
              <select value={goal.loss_tolerance} onChange={event =>
                updateGoal(goal.key, "loss_tolerance", event.target.value as GoalInput["loss_tolerance"])}>
                <option value="not_set">Not decided</option>
                <option value="avoid_loss">Avoid losses</option>
                <option value="small_fluctuations">Small fluctuations only</option>
                <option value="substantial_fluctuations">Can tolerate substantial fluctuations</option>
                <option value="not_sure">Not sure</option>
              </select>
            </label>
            <label>Manual amount set aside (GBP, used only without linked accounts)
              <input inputMode="decimal" maxLength={18} pattern="(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?"
                title="Use a non-negative GBP amount with up to two decimal places."
                disabled={goal.linked_account_ids.length > 0}
                value={goal.current_amount_gbp ?? ""}
                onChange={event => updateGoal(goal.key, "current_amount_gbp", event.target.value)} />
            </label>
            {goal.linked_account_ids.length > 0 && <small className="field-help">
              While accounts are linked, their recorded amount replaces this manual figure; the two are never added together.
            </small>}
            <label>Expected monthly contribution (GBP, optional)
              <input inputMode="decimal" maxLength={18} pattern="(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?"
                title="Use a non-negative GBP amount with up to two decimal places."
                value={goal.monthly_contribution_gbp ?? ""}
                onChange={event => updateGoal(goal.key, "monthly_contribution_gbp", event.target.value)} />
            </label>
            <label>Notes (optional)
              <input maxLength={500} value={goal.notes}
                onChange={event => updateGoal(goal.key, "notes", event.target.value)} />
            </label>
            <button type="button" aria-label={`Remove ${goal.name || `goal ${index + 1}`}`}
              onClick={() => {
                setGoals(current => current.filter(item => item.key !== goal.key));
                setPreview(null); setSaved("");
              }}>Remove</button>
          </div>
          {(() => {
            const linkedAmount = linkedGoalValue(goal, portfolio, accountValues);
            const scenarioGoal = goal.linked_account_ids.length > 0
              ? { ...goal, current_amount_gbp: linkedAmount?.toString() ?? null }
              : goal;
            const scenario = goalScenario(scenarioGoal);
            return <>
              {goal.linked_account_ids.length > 0 && !linkedAmount &&
                <p role="status">Goal progress is unavailable until linked account values are complete and match this portfolio.</p>}
              {scenario && <div className="planning-scenario">
              <h4>Simple contribution illustration</h4>
              <p>Starting with your entered £{scenario.current_amount_gbp}, there are {scenario.months} assumed
                monthly contribution date{scenario.months === 1 ? "" : "s"} before the target date.
                Contributions are assumed at the start of each month from next month through the target date.</p>
              <dl className="health-facts">
                <div><dt>If no more contributions are made</dt><dd>£{scenario.with_no_more_contributions_gbp};
                  remaining target gap £{scenario.remaining_gap_with_no_more_contributions_gbp}</dd></div>
                {scenario.with_monthly_contributions_gbp !== null && <div>
                  <dt>If £{goal.monthly_contribution_gbp} is contributed each month</dt>
                  <dd>£{scenario.with_monthly_contributions_gbp}; remaining target gap
                    {" "}£{scenario.remaining_gap_with_monthly_contributions_gbp}</dd>
                </div>}
              </dl>
              <p>This arithmetic assumes no investment growth, interest, fees, tax, withdrawals or missed contributions.
                It does not account for access restrictions, inflation or whether money is actually available.</p>
              </div>}
            </>;
          })()}
        </div>)}
        <button type="button" onClick={() => {
          setGoals(current => [...current, emptyGoal()]);
          setPreview(null); setSaved("");
        }} disabled={goals.length >= 20}>Add a goal</button>
      </fieldset>
      <div className="recording-actions">
        <button type="submit" disabled={busy || !session.csrf || !session.profile_hash}>
          {busy ? "Working…" : "Preview changes"}
        </button>
      </div>
    </form>}
    {preview && <section className="recording-preview" aria-label="Review goals before saving">
      <h2>Review before saving</h2>
      <p>This will replace the separate planning profile with revision {preview.revision}; it will not update the investment journal.</p>
      <ul>{preview.goals.map(goal => <li key={goal.id}>
        <strong>{goal.name}</strong>
        {` · ${goal.purpose === "not_set" ? "Purpose not set" : goal.purpose.replaceAll("_", " ")}`}
        {goal.target_amount_gbp && ` · £${goal.target_amount_gbp}`}
        {goal.target_date && ` · target ${goal.target_date}`}
        {goal.access_date && ` · access from ${goal.access_date}`}
        {` · liquidity: ${goal.liquidity.replaceAll("_", " ")}`}
        {` · loss comfort: ${goal.loss_tolerance.replaceAll("_", " ")}`}
        {goal.linked_account_ids.length > 0 && ` · linked accounts: ${
          goal.linked_account_ids.map(id =>
            portfolio?.accounts.find(account => account.id === id)?.name ?? id).join(", ")}`}
        {goal.linked_account_ids.length > 0 && ` · recorded amount: ${
          linkedGoalValue(goal, portfolio, accountValues) === null
            ? "unavailable" : pounds(linkedGoalValue(goal, portfolio, accountValues)!.toString())} at ${
              timestamp(portfolio?.as_of ?? null)}`}
        {goal.linked_account_ids.length === 0 && goal.current_amount_gbp !== null &&
          ` · set aside £${goal.current_amount_gbp}`}
        {goal.monthly_contribution_gbp !== null && ` · £${goal.monthly_contribution_gbp}/month expected`}
        {goal.notes && <p>{goal.notes}</p>}
      </li>)}</ul>
      <p>Preview expires {timestamp(preview.expires_at)}.</p>
      <button type="button" disabled={busy} onClick={() => void confirm()}>
        {busy ? "Saving…" : "Confirm and save locally"}
      </button>
      <button type="button" disabled={busy} onClick={() => setPreview(null)}>Edit goals</button>
    </section>}
    <p className="planning-storage-note">
      This file is separate from the accounting workspace. It is local plaintext, not an encrypted vault;
      use a device and backup location you control. Removing it does not affect journal records.
    </p>
  </section>;
}
