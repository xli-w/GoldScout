import { useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { evidenceAge, pounds, timestamp } from "./api";
import type { InvestmentSnapshot } from "./InvestmentView";

type AccountPreference = {
  intervalDays: number | null;
  termsIntervalDays: number | null;
  reviewedAt: string | null;
};
type PreferenceState = Record<string, AccountPreference>;
type ReviewCycle = { version: 1; portfolioId: string; revision: number; startedAt: string;
  accountIds: string[]; checkedAccountIds: string[] };

const STORAGE_PREFIX = "goldscout.account-maintenance.v1.";
const REVIEW_STORAGE_PREFIX = "goldscout.guided-review.v1.";
const INTERVALS = [7, 14, 30, 90, 180, 365];

function reviewStorageKey(portfolioId: string) {
  return `${REVIEW_STORAGE_PREFIX}${encodeURIComponent(portfolioId)}`;
}

function accountStoragePrefix(portfolioId: string) {
  return `${STORAGE_PREFIX}${encodeURIComponent(portfolioId)}.account.`;
}

function accountStorageKey(portfolioId: string, accountId: string) {
  return `${accountStoragePrefix(portfolioId)}${encodeURIComponent(accountId)}`;
}

function parsePreference(value: unknown): AccountPreference {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Saved account review settings are invalid. They were not changed.");
  const item = value as Record<string, unknown>;
  const intervalDays = item.intervalDays;
  const reviewedAt = item.reviewedAt;
  const termsIntervalDays = item.termsIntervalDays ?? null;
  const keys = Object.keys(item).sort().join(",");
  if ((keys !== "intervalDays,reviewedAt" &&
      keys !== "intervalDays,reviewedAt,termsIntervalDays") ||
    !(intervalDays === null || typeof intervalDays === "number" && INTERVALS.includes(intervalDays)) ||
    !(termsIntervalDays === null || typeof termsIntervalDays === "number" &&
      INTERVALS.includes(termsIntervalDays)) ||
    !(reviewedAt === null || typeof reviewedAt === "string" &&
      Number.isFinite(Date.parse(reviewedAt))))
    throw new Error("Saved account review settings are invalid. They were not changed.");
  return { intervalDays, termsIntervalDays, reviewedAt };
}

function readPreferences(portfolioId: string): PreferenceState {
  const raw = localStorage.getItem(STORAGE_PREFIX + portfolioId);
  if (raw !== null && raw.length > 32768)
    throw new Error("Saved account review settings are too large. They were not changed.");
  const result: PreferenceState = {};
  if (raw !== null) {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value) ||
      Object.keys(value).length > 100)
      throw new Error("Saved account review settings are invalid. They were not changed.");
    for (const [id, preference] of Object.entries(value))
      result[id] = parsePreference(preference);
  }
  const prefix = accountStoragePrefix(portfolioId);
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    if (key === null || !key.startsWith(prefix)) continue;
    const id = decodeURIComponent(key.slice(prefix.length));
    const saved = localStorage.getItem(key);
    if (saved === null) continue;
    if (saved.length > 4096)
      throw new Error("Saved account review settings are too large. They were not changed.");
    result[id] = parsePreference(JSON.parse(saved));
    if (Object.keys(result).length > 100)
      throw new Error("Saved account review settings are invalid. They were not changed.");
  }
  return result;
}

function readReviewCycle(portfolioId: string): ReviewCycle | null {
  const raw = localStorage.getItem(reviewStorageKey(portfolioId));
  if (raw === null) return null;
  if (raw.length > 32768) throw new Error("Saved guided review is too large. It was not changed.");
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Saved guided review is invalid. It was not changed.");
  const item = value as Record<string, unknown>;
  const uniqueIds = (ids: unknown): ids is string[] => Array.isArray(ids) &&
    ids.length <= 100 && ids.every(id => typeof id === "string" && id.length > 0 && id.length <= 200) &&
    new Set(ids).size === ids.length;
  const accountIds = item.accountIds;
  const checkedAccountIds = item.checkedAccountIds;
  if (Object.keys(item).sort().join(",") !==
      "accountIds,checkedAccountIds,portfolioId,revision,startedAt,version" ||
      item.version !== 1 || item.portfolioId !== portfolioId ||
      typeof item.revision !== "number" || !Number.isSafeInteger(item.revision) || item.revision < 0 ||
      typeof item.startedAt !== "string" || !Number.isFinite(Date.parse(item.startedAt)) ||
      !uniqueIds(accountIds) || !uniqueIds(checkedAccountIds) ||
      checkedAccountIds.some(id => !accountIds.includes(id)))
    throw new Error("Saved guided review is invalid. It was not changed.");
  return item as ReviewCycle;
}

function unresolvedReviewItems(data: InvestmentSnapshot, activeIds: string[],
  preferences: PreferenceState, now: number) {
  const unresolved: string[] = [];
  if (!data.complete) unresolved.push("portfolio value is incomplete");
  for (const accountId of activeIds) {
    const account = data.accounts.find(item => item.id === accountId);
    if (!account) continue;
    const preference = preferences[accountId];
    const hasMissingPrice = data.holdings.some(item => item.account_id === accountId &&
      (item.mark_at === null || item.value_gbp === null));
    const hasCashDifference = data.cash_observations.some(item => item.account_id === accountId &&
      !new Decimal(item.difference_gbp).isZero());
    const correctionNeedsReview = data.recorded_corrections.length > 0 &&
      data.cash_observations.some(item => item.account_id === accountId);
    const asOfAge = data.as_of === null ? null : now - Date.parse(data.as_of);
    const marks = data.holdings.filter(item => item.account_id === accountId);
    const markAges = marks.map(item => item.mark_at === null ? null : now - Date.parse(item.mark_at));
    const stale = preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
      (asOfAge === null || asOfAge < 0 ||
        asOfAge > preference.intervalDays * 86400000 ||
        markAges.some(age => age === null || age < 0 ||
          age > preference.intervalDays! * 86400000));
    const cashObservation = account.cash_observation;
    const cashCheckStale = preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
      (cashObservation === null ||
      now - Date.parse(cashObservation.timestamp) < 0 ||
      now - Date.parse(cashObservation.timestamp) > preference.intervalDays * 86400000);
    const termsAge = account.terms === null ? null : now - Date.parse(account.terms.available_at);
    const termsStale = account.kind === "deposit" && (
      preference?.termsIntervalDays === null || preference?.termsIntervalDays === undefined ||
      (termsAge === null || !Number.isFinite(termsAge) || termsAge < 0 ||
        termsAge > preference.termsIntervalDays * 86400000));
    const maturityDays = account.terms?.maturity_date
      ? Math.ceil((Date.parse(`${account.terms.maturity_date}T00:00:00Z`) - now) / 86400000)
      : null;
    if (preference?.intervalDays === null || preference?.intervalDays === undefined)
      unresolved.push(`${account.name} account review interval has not been chosen`);
    if (hasMissingPrice) unresolved.push(`${account.name} has an unpriced holding`);
    if (hasCashDifference) unresolved.push(`${account.name} has a recorded cash difference`);
    if (correctionNeedsReview) unresolved.push(`${account.name} cash checks may have changed after correction replay`);
    if (stale) unresolved.push(`${account.name} valuation or holding prices are past the chosen interval`);
    if (cashCheckStale) unresolved.push(`${account.name} cash check is missing or past the chosen interval`);
    if (account.kind === "deposit" &&
        (preference?.termsIntervalDays === null || preference?.termsIntervalDays === undefined))
      unresolved.push(`${account.name} savings terms review interval has not been chosen`);
    else if (termsStale)
      unresolved.push(`${account.name} savings terms are missing or past their separate chosen interval`);
    if (maturityDays !== null && maturityDays <= 90)
      unresolved.push(`${account.name} has an approaching recorded maturity date`);
    if (preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
      (preference.reviewedAt === null ||
      now - Date.parse(preference.reviewedAt) >= preference.intervalDays * 86400000))
      unresolved.push(`${account.name} reminder is due`);
  }
  for (const account of data.accounts.filter(item => item.status === "retired")) {
    const hasCashDifference = data.cash_observations.some(item =>
      item.account_id === account.id && !new Decimal(item.difference_gbp).isZero());
    const correctionNeedsReview = data.recorded_corrections.length > 0 &&
      data.cash_observations.some(item => item.account_id === account.id);
    if (hasCashDifference)
      unresolved.push(`${account.name} (retired) has a recorded cash difference`);
    if (correctionNeedsReview)
      unresolved.push(`${account.name} (retired) cash checks may have changed after correction replay`);
  }
  return unresolved;
}

function savePreference(portfolioId: string, accountId: string, value: AccountPreference) {
  localStorage.setItem(accountStorageKey(portfolioId, accountId), JSON.stringify(value));
}

function AccountMaintenance({ data, now, onReviewCashEvidence }: {
  data: InvestmentSnapshot; now: number; onReviewCashEvidence: () => void;
}) {
  const [preferences, setPreferences] = useState<PreferenceState>({});
  const [cycle, setCycle] = useState<ReviewCycle | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      setPreferences(readPreferences(data.portfolio_id));
      try {
        setCycle(readReviewCycle(data.portfolio_id));
        setError("");
      } catch (reason) {
        setCycle(null);
        setError(reason instanceof Error ? reason.message : "Saved guided review could not be read.");
      }
    } catch (reason) {
      setPreferences({});
      setError(reason instanceof Error ? reason.message : "Saved review settings could not be read.");
    }
  }, [data.portfolio_id]);

  useEffect(() => {
    const prefix = accountStoragePrefix(data.portfolio_id);
    const legacyKey = STORAGE_PREFIX + data.portfolio_id;
    const cycleKey = reviewStorageKey(data.portfolio_id);
    function onStorage(event: StorageEvent) {
      if (event.key !== null && event.key !== legacyKey &&
          event.key !== cycleKey && !event.key.startsWith(prefix)) return;
      try {
        setPreferences(readPreferences(data.portfolio_id));
        try {
          setCycle(readReviewCycle(data.portfolio_id));
          setError("");
        } catch (reason) {
          setCycle(null);
          setError(reason instanceof Error ? reason.message : "Saved guided review could not be read.");
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Saved review settings could not be read.");
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [data.portfolio_id]);

  const activeIds = data.accounts.filter(account => account.status === "active")
    .map(account => account.id);
  const currentCycle = cycle !== null && cycle.revision === data.revision &&
    cycle.accountIds.length === activeIds.length &&
    cycle.accountIds.every((id, index) => id === activeIds[index]);
  const checkedIds = currentCycle ? cycle.checkedAccountIds : [];
  const nextAccount = activeIds.find(id => !checkedIds.includes(id));
  const unresolved = unresolvedReviewItems(data, activeIds, preferences, now);
  const unmatchedPreferences = useMemo(() => Object.keys(preferences)
    .filter(id => !data.accounts.some(account => account.id === id)), [data.accounts, preferences]);

  function update(accountId: string, changes: Partial<AccountPreference>): boolean {
    try {
      const current = readPreferences(data.portfolio_id);
      const previous = current[accountId] ?? preferences[accountId];
      const intervalDays = changes.intervalDays ?? previous?.intervalDays ?? null;
      const termsIntervalDays = changes.termsIntervalDays !== undefined
        ? changes.termsIntervalDays : previous?.termsIntervalDays ?? null;
      if (intervalDays === null && termsIntervalDays === null)
        throw new Error("Choose a review interval first.");
      const next: AccountPreference = {
        intervalDays,
        termsIntervalDays,
        reviewedAt: changes.reviewedAt ?? current[accountId]?.reviewedAt ??
          preferences[accountId]?.reviewedAt ?? null,
      };
      savePreference(data.portfolio_id, accountId, next);
      setPreferences({ ...current, [accountId]: next });
      setError("");
      return true;
    } catch {
      setError("The browser could not save this local review setting. No journal record was changed.");
      return false;
    }
  }

  function startReview() {
    try {
      const next: ReviewCycle = {
        version: 1, portfolioId: data.portfolio_id, revision: data.revision,
        startedAt: new Date(now).toISOString(), accountIds: activeIds,
        checkedAccountIds: [],
      };
      localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify(next));
      setCycle(next);
      setError("");
    } catch {
      setError("The browser could not save this review session. No journal record was changed.");
    }
  }

  function markReviewed(accountId: string) {
    if (!cycle || !currentCycle || !cycle.accountIds.includes(accountId)) return;
    try {
      const next: ReviewCycle = {
        ...cycle,
        checkedAccountIds: [...cycle.checkedAccountIds, accountId],
      };
      localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify(next));
      setCycle(next);
      const preference = preferences[accountId];
      if (preference?.intervalDays == null ||
          update(accountId, { reviewedAt: new Date(now).toISOString() }))
        setError("");
    } catch {
      setError("The browser could not save review progress. No journal record was changed.");
    }
  }

  return <section id="account-review" className="history-group account-maintenance" aria-label="Account freshness and next actions">
    <h2>Account status and next action</h2>
    <p className="position-note">These are reminders based on your chosen review interval and recorded journal dates.
      They do not verify a bank or broker. A complete value for an older date is not a current balance.</p>
    <section className="account-review-cycle" aria-label="Guided portfolio review">
      <h3>Guided account review</h3>
      {activeIds.length === 0 ? <p>No active accounts are available to review.</p> : <>
        {currentCycle ? <>
          <p role="status">{checkedIds.length} of {activeIds.length} accounts checked in this review.
            {" "}Started {timestamp(cycle.startedAt)}.</p>
          {nextAccount && <p>Next: {data.accounts.find(account => account.id === nextAccount)?.name}.</p>}
          {checkedIds.length === activeIds.length && <>
            <p><strong>Review complete.</strong> {unresolved.length === 0
              ? "This checklist found no unresolved items. Review the portfolio warnings and limitations separately; this is not provider verification."
              : `${unresolved.length} unresolved item${unresolved.length === 1 ? "" : "s"} remain. Review the account actions below; completion is not an all-clear.`}</p>
            {unresolved.length > 0 && <ul aria-label="Unresolved review items">
              {unresolved.map(item => <li key={item}>{item}</li>)}
            </ul>}
          </>}
        </> : cycle ? <p role="status">The journal changed after this review began. Restart the review to use current records; previous progress remains saved but is not counted.</p>
          : <p>Review each active account in turn. Progress is saved in this browser and is not an accounting record.</p>}
        {currentCycle && nextAccount
          ? <NavLink className="record-action"
            to={`/investment/update?account_id=${encodeURIComponent(nextAccount)}`}>Continue review</NavLink>
          : <button type="button" onClick={startReview}>
            {cycle ? "Start a new review" : "Start account review"}
          </button>}
      </>}
    </section>
    {error && <p role="alert">{error}</p>}
    {unmatchedPreferences.length > 0 && <p role="status">
      {unmatchedPreferences.length} saved review setting{unmatchedPreferences.length === 1 ? "" : "s"} refer to account IDs
      no longer in this journal; those settings were left untouched.
    </p>}
    {data.accounts.filter(account => account.status === "active").map(account => {
      const preference = preferences[account.id];
      const marks = data.holdings.filter(holding => holding.account_id === account.id);
      const missingPrice = marks.some(holding => holding.mark_at === null || holding.value_gbp === null);
      const latestCashCheck = account.cash_observation;
      const cashDifference = latestCashCheck !== null &&
        !new Decimal(latestCashCheck.difference_gbp).isZero();
      const earlierCashDifference = data.cash_observations.some(observation =>
        observation.account_id === account.id && !new Decimal(observation.difference_gbp).isZero());
      const correctionCouldChangeChecks = data.recorded_corrections.length > 0 &&
        data.cash_observations.some(observation => observation.account_id === account.id);
      const cashCheckAge = latestCashCheck === null ? null : now - Date.parse(latestCashCheck.timestamp);
      const termsAge = account.terms === null ? null : now - Date.parse(account.terms.available_at);
      const maturityDays = account.terms?.maturity_date
        ? Math.ceil((Date.parse(`${account.terms.maturity_date}T00:00:00Z`) - now) / 86400000)
        : null;
      const asOfAge = data.as_of === null ? null : now - Date.parse(data.as_of);
      const markAge = marks.map(holding => holding.mark_at === null ? null : now - Date.parse(holding.mark_at));
      const oldestMarkAge = markAge.length === 0 || markAge.some(age => age === null)
        ? null : Math.max(...markAge.map(age => age ?? 0));
      const stale = preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
        (asOfAge === null ||
        asOfAge < 0 || asOfAge > preference.intervalDays * 86400000 ||
        oldestMarkAge !== null && (oldestMarkAge < 0 ||
          oldestMarkAge > preference.intervalDays * 86400000));
      const cashCheckStale = preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
        (cashCheckAge === null ||
        cashCheckAge < 0 || cashCheckAge > preference.intervalDays * 86400000);
      const savingsTermsStale = account.kind === "deposit" && (
        preference?.termsIntervalDays === null || preference?.termsIntervalDays === undefined ||
        (termsAge === null || !Number.isFinite(termsAge) || termsAge < 0 ||
          termsAge > preference.termsIntervalDays * 86400000));
      const maturityNeedsCheck = account.kind === "deposit" &&
        maturityDays !== null && maturityDays <= 90;
      const reviewDue = preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
        (preference.reviewedAt === null ||
        now - Date.parse(preference.reviewedAt) >= preference.intervalDays * 86400000);
      const needsAction = !data.complete || missingPrice || cashDifference || earlierCashDifference ||
        correctionCouldChangeChecks;
      const reviewNeeded = stale || savingsTermsStale || maturityNeedsCheck || cashCheckStale || reviewDue;
      let action: string;
      if (cashDifference) action = "Review the recorded cash difference.";
      else if (earlierCashDifference) action = "Review earlier cash differences; a later match does not resolve them.";
      else if (correctionCouldChangeChecks)
        action = "Compare prior reports with the original cash checks and correction evidence; replay may have changed past differences.";
      else if (missingPrice) action = "Update the missing investment price; the full portfolio value is unavailable.";
      else if (!data.complete) action = "Review the portfolio evidence; the full portfolio value is unavailable.";
      else if (preference?.intervalDays == null && maturityNeedsCheck)
        action = "Confirm the recorded maturity date and access or notice options using current provider information.";
      else if (preference?.intervalDays == null) action = "Choose how often you expect to review this account.";
      else if (stale) action = "Update the account records; the latest complete value is past your chosen interval.";
      else if (account.kind === "deposit" && account.terms === null)
        action = "Record current savings rate, access, notice and maturity terms from a dated source.";
      else if (account.kind === "deposit" && preference?.termsIntervalDays == null)
        action = "Choose a separate interval for rechecking savings terms.";
      else if (savingsTermsStale)
        action = "Recheck the savings rate, access, notice and maturity terms from a dated source.";
      else if (maturityNeedsCheck)
        action = "Confirm the recorded maturity date and access or notice options using current provider information.";
      else if (cashCheckStale) action = "Check the latest cash balance against a dated source.";
      else if (reviewDue) action = account.kind === "deposit"
        ? "Review this account's records; savings terms have a separate recheck interval."
        : "Review this account's records.";
      else action = "No review is due under your chosen interval.";

      return <article className="account-maintenance-card" key={account.id}>
        <div className="account-maintenance-heading">
          <div><h3>{account.name}</h3><span>{account.kind === "deposit" ? "Savings account" : "Investment account"}</span></div>
          <strong>{needsAction ? "Action needed" : reviewNeeded ? "Check due"
            : preference?.intervalDays == null ? "Freshness unknown" : "Within chosen interval"}</strong>
        </div>
        <p className="account-next-action"><strong>Next action:</strong> {action}</p>
        <details className="help-disclosure">
        <summary>Recorded account details</summary>
        <dl className="health-facts">
          <div><dt>Recorded cash</dt><dd>{account.cash_gbp === null ? "Unavailable" : `£${account.cash_gbp}`}</dd></div>
          <div><dt>Latest complete portfolio value date (shared)</dt><dd>{data.complete && data.as_of
            ? `${timestamp(data.as_of)} (age: ${evidenceAge(data.as_of, now)})`
            : "Unavailable; portfolio evidence is incomplete"}</dd></div>
          <div><dt>Coverage</dt><dd>{data.complete && !missingPrice
            ? "Complete at the shared recorded date only; not a current provider check"
            : "Partial or unknown; a complete account total is unavailable"}</dd></div>
          <div><dt>Provider check</dt><dd>Not linked to this journal account; see the separate provider view if configured</dd></div>
          <div><dt>Recorded holdings</dt><dd>{marks.length === 0 ? "None recorded"
            : `${marks.length} holding${marks.length === 1 ? "" : "s"}; full account total is not calculated separately`}</dd></div>
          <div><dt>Last cash comparison</dt><dd>{latestCashCheck
            ? `${timestamp(latestCashCheck.timestamp)} (age: ${evidenceAge(latestCashCheck.timestamp, now)}) · difference ${pounds(latestCashCheck.difference_gbp, true)}`
            : "Not recorded"}</dd></div>
          {account.kind === "deposit" && <div><dt>Savings terms (display facts)</dt><dd>{account.terms === null
            ? "Not recorded"
            : <>
              {account.terms.annual_rate_pct === null ? "Rate unavailable"
                : `${account.terms.annual_rate_pct}% ${account.terms.rate_basis ?? "basis unavailable"}`}
              {` · effective ${account.terms.effective_from}`}
              {account.terms.maturity_date !== null && ` · maturity ${account.terms.maturity_date}`}
              {account.terms.notice_days !== null && ` · notice ${account.terms.notice_days} days`}
              {` · available ${timestamp(account.terms.available_at)} (age: ${evidenceAge(account.terms.available_at, now)})`}
            </>}</dd></div>}
        </dl>
        {marks.length > 0 && <ul className="account-holding-values">
          {marks.map(holding => <li key={holding.product_id}>
            {holding.name}: {pounds(holding.value_gbp)} · price date {timestamp(holding.mark_at)}
          </li>)}
        </ul>}
        </details>
        <label>Expected review interval
          <select aria-label={`Expected review interval for ${account.name}`}
            value={preference?.intervalDays ?? ""}
            onChange={event => {
              const intervalDays = Number(event.target.value);
              if (!INTERVALS.includes(intervalDays)) return;
              update(account.id, { intervalDays });
            }}>
            <option value="">Choose an interval</option>
            {INTERVALS.map(days => <option key={days} value={days}>
              {days === 7 ? "Every week" : days === 14 ? "Every two weeks"
                : days === 30 ? "Every month" : days === 90 ? "Every three months"
                  : days === 180 ? "Every six months" : "Every year"}
            </option>)}
          </select>
        </label>
        {account.kind === "deposit" && <label>Expected savings-terms recheck interval (separate)
          <select aria-label={`Savings terms recheck interval for ${account.name}`}
            value={preference?.termsIntervalDays ?? ""}
            onChange={event => {
              const termsIntervalDays = Number(event.target.value);
              if (!INTERVALS.includes(termsIntervalDays)) return;
              update(account.id, { termsIntervalDays });
            }}>
            <option value="">Choose an interval</option>
            {INTERVALS.map(days => <option key={days} value={days}>
              {days === 7 ? "Every week" : days === 14 ? "Every two weeks"
                : days === 30 ? "Every month" : days === 90 ? "Every three months"
                  : days === 180 ? "Every six months" : "Every year"}
            </option>)}
          </select>
        </label>}
        <div className="recording-actions">
          <NavLink className="record-action"
            to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>Update this account</NavLink>
          {(cashDifference || earlierCashDifference || correctionCouldChangeChecks) &&
            <a href="#investment-accounts" onClick={onReviewCashEvidence}>Review cash evidence</a>}
          {currentCycle && cycle && cycle.accountIds.includes(account.id) &&
            !checkedIds.includes(account.id) && <button type="button" disabled={error !== ""}
              onClick={() => markReviewed(account.id)}>
              Mark reviewed in this session
            </button>}
          {preference && !currentCycle && <button type="button" disabled={error !== ""}
            onClick={() => update(account.id, { reviewedAt: new Date(now).toISOString() })}>
            Mark account checked today
          </button>}
        </div>
      </article>;
    })}
    <p className="position-note">Review intervals, account check dates and guided-review progress are saved in this browser only.
      Marking an account checked sets only a reminder date; it does not save a portfolio
      comparison point, change the journal or resolve differences. eToro remains a separate read-only view and is not mapped into these totals.</p>
    <p className="position-note">The dashboard has a common-date portfolio valuation, not a separately reconstructed balance history for each account.
      Cash and holding values above are the latest journal records and may not share the valuation date.</p>
    <p className="position-note">Recorded trade and account charges: {pounds(data.recorded_fees_gbp)}. This includes only explicitly recorded purchase/sale fees and account charges;
      fund, subscription, FX, tax and other costs may be missing. Savings terms are dated display facts, not an earned-return
      calculation or proof of the best obtainable rate.</p>
  </section>;
}

export default AccountMaintenance;
