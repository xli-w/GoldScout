import { useEffect, useState, type FormEvent } from "react";
import { planningConfirm, planningPreview, planningSession, type GoalInput, type PlanningGoal, type PlanningPreview, type PlanningSession } from "./planning-api";
import { timestamp } from "./api";
import { goalScenario } from "./planning-scenarios";

type DraftGoal = GoalInput & { key: string };
const emptyGoal = (): DraftGoal => ({
  key: crypto.randomUUID().replaceAll("-", ""),
  name: "", target_amount_gbp: null, target_date: null, notes: "",
  purpose: "not_set", access_date: null, liquidity: "unknown",
  loss_tolerance: "not_set", current_amount_gbp: null, monthly_contribution_gbp: null,
});
const draft = (goal: PlanningGoal): DraftGoal => ({ ...goal, key: goal.id });

export function PlanningProfilePage() {
  const [session, setSession] = useState<PlanningSession | null>(null);
  const [goals, setGoals] = useState<DraftGoal[]>([]);
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

  useEffect(() => {
    let active = true;
    planningSession().then(result => {
      if (!active) return;
      setSession(result);
      setGoals(result.goals.map(draft));
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "Local planning is unavailable.");
    });
    return () => { active = false; };
  }, []);

  function updateGoal(key: string, field: keyof GoalInput, value: string) {
    setGoals(current => current.map(goal => goal.key === key
      ? { ...goal, [field]: field === "target_amount_gbp" || field === "target_date"
        ? value || null : value }
      : goal));
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
      });
      setPreview(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Goal preview was rejected.");
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
      setError(reason instanceof Error ? reason.message : "Goal save was rejected.");
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
      Optional personal goals are saved in a separate local file. They do not change journal balances,
      account records, monitoring, or market-context tools, and are not sent to hosted AI.
      Goals are not linked to account balances. Scenarios use only amounts you enter, assume no growth,
      interest, fees, tax or withdrawals, and are not forecasts or recommendations.
    </p>
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
            <label>Amount set aside for this goal (GBP, optional)
              <input inputMode="decimal" maxLength={18} pattern="(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,2})?"
                title="Use a non-negative GBP amount with up to two decimal places."
                value={goal.current_amount_gbp ?? ""}
                onChange={event => updateGoal(goal.key, "current_amount_gbp", event.target.value)} />
            </label>
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
            const scenario = goalScenario(goal);
            return scenario && <div className="planning-scenario">
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
            </div>;
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
        {goal.current_amount_gbp !== null && ` · set aside £${goal.current_amount_gbp}`}
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
