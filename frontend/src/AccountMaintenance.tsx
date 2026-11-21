import { useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { evidenceAge, pounds, timestamp } from "./api";
import { readReviewCycle, reviewStorageKey, sameReview } from "./guided-review";
import { accountDisplay } from "./portfolio-display";
import type { ReviewCycle } from "./guided-review";
import type { InvestmentSnapshot } from "./InvestmentView";

type AccountPreference = {
  intervalDays: number | null;
  termsIntervalDays: number | null;
  reviewedAt: string | null;
};
type PreferenceState = Record<string, AccountPreference>;
const STORAGE_PREFIX = "goldscout.account-maintenance.v1.";
const INTERVALS = [7, 14, 30, 90, 180, 365];

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

function isReviewDue(preference: AccountPreference | undefined, now: number) {
  if (preference?.intervalDays == null) return false;
  if (preference.reviewedAt === null) return true;
  const age = now - Date.parse(preference.reviewedAt);
  return !Number.isFinite(age) || age < 0 || age >= preference.intervalDays * 86400000;
}

function unresolvedReviewItems(data: InvestmentSnapshot, activeIds: string[],
  preferences: PreferenceState, now: number, reviewNow: number) {
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
    const name = accountDisplay(data, account.id);
    if (preference?.intervalDays === null || preference?.intervalDays === undefined)
      unresolved.push(`${name} account review interval has not been chosen`);
    if (hasMissingPrice) unresolved.push(`${name} has an unpriced holding`);
    if (hasCashDifference) unresolved.push(`${name} has a recorded cash difference`);
    if (correctionNeedsReview) unresolved.push(`${name} cash checks may have changed after correction replay`);
    if (stale) unresolved.push(`${name} valuation or holding prices are past the chosen interval`);
    if (cashCheckStale) unresolved.push(`${name} cash check is missing or past the chosen interval`);
    if (account.kind === "deposit" &&
        (preference?.termsIntervalDays === null || preference?.termsIntervalDays === undefined))
      unresolved.push(`${name} savings terms review interval has not been chosen`);
    else if (termsStale)
      unresolved.push(`${name} savings terms are missing or past their separate chosen interval`);
    if (maturityDays !== null && maturityDays <= 90)
      unresolved.push(`${name} has an approaching recorded maturity date`);
    if (isReviewDue(preference, reviewNow))
      unresolved.push(`${name} reminder is due`);
  }
  for (const account of data.accounts.filter(item => item.status === "retired")) {
    const hasCashDifference = data.cash_observations.some(item =>
      item.account_id === account.id && !new Decimal(item.difference_gbp).isZero());
    const correctionNeedsReview = data.recorded_corrections.length > 0 &&
      data.cash_observations.some(item => item.account_id === account.id);
    if (hasCashDifference)
      unresolved.push(`${accountDisplay(data, account.id)} (retired) has a recorded cash difference`);
    if (correctionNeedsReview)
      unresolved.push(`${accountDisplay(data, account.id)} (retired) cash checks may have changed after correction replay`);
  }
  return unresolved;
}

function savePreference(portfolioId: string, accountId: string, value: AccountPreference) {
  localStorage.setItem(accountStorageKey(portfolioId, accountId), JSON.stringify(value));
}

async function requireCurrentSnapshot(data: InvestmentSnapshot, refresh: () => Promise<InvestmentSnapshot>) {
  const latest = await refresh();
  if (latest.source_mode !== "recorded" || latest.portfolio_id !== data.portfolio_id ||
      latest.revision !== data.revision)
    throw new Error("The journal changed. Refresh the portfolio before continuing the review.");
}

export async function markAccountInReview(data: InvestmentSnapshot, accountId: string,
  expectedCycle: ReviewCycle, refresh: () => Promise<InvestmentSnapshot>) {
  if (!navigator.locks) throw new Error("This browser cannot coordinate review progress across tabs.");
  return navigator.locks.request("goldscout-recording-confirmation", async () => {
    const cycle = readReviewCycle(data.portfolio_id);
    const activeIds = data.accounts.filter(account => account.status === "active").map(account => account.id);
    if (!cycle || !sameReview(cycle, expectedCycle) || cycle.revision !== data.revision ||
        cycle.accountIds.length !== activeIds.length ||
        !cycle.accountIds.every((id, index) => id === activeIds[index]) ||
        !cycle.accountIds.includes(accountId)) return false;
    await requireCurrentSnapshot(data, refresh);
    const preferences = readPreferences(data.portfolio_id);
    const preference = preferences[accountId] ?? {
      intervalDays: null, termsIntervalDays: null, reviewedAt: null,
    };
    savePreference(data.portfolio_id, accountId, {
      ...preference, reviewedAt: new Date().toISOString(),
    });
    const checkedAccountIds = cycle.checkedAccountIds.includes(accountId)
      ? cycle.checkedAccountIds : [...cycle.checkedAccountIds, accountId];
    localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify({ ...cycle, checkedAccountIds }));
    return true;
  });
}

function AccountMaintenance({ data, now, onReviewCashEvidence, onFinishReview, refreshSnapshot,
  defaults, defaultsStatus }: {
  data: InvestmentSnapshot; now: number; onReviewCashEvidence: () => void; onFinishReview: () => boolean;
  refreshSnapshot: () => Promise<InvestmentSnapshot>;
  defaults: { reviewIntervalDays: number; termsIntervalDays: number } | null;
  defaultsStatus: "loading" | "unavailable" | "ready";
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
  const effectivePreferences = useMemo(() => {
    const result: PreferenceState = {};
    for (const account of data.accounts) {
      const saved = preferences[account.id];
      result[account.id] = {
        intervalDays: saved?.intervalDays ?? defaults?.reviewIntervalDays ?? null,
        termsIntervalDays: account.kind === "deposit"
          ? saved?.termsIntervalDays ?? defaults?.termsIntervalDays ?? null : null,
        reviewedAt: saved?.reviewedAt ?? null,
      };
    }
    return result;
  }, [data.accounts, defaults, preferences]);
  const currentCycle = cycle !== null && cycle.revision === data.revision &&
    cycle.accountIds.length === activeIds.length &&
    cycle.accountIds.every((id, index) => id === activeIds[index]);
  const checkedIds = currentCycle ? cycle.checkedAccountIds : [];
  const nextAccount = activeIds.find(id => !checkedIds.includes(id));
  const reviewNow = Date.now();
  const unresolved = unresolvedReviewItems(data, activeIds, effectivePreferences, now, reviewNow);
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

  async function startReview() {
    try {
      const next: ReviewCycle = {
        version: 1, portfolioId: data.portfolio_id, revision: data.revision,
        startedAt: new Date().toISOString(), reviewId: crypto.randomUUID(), accountIds: activeIds,
        checkedAccountIds: [],
      };
      if (!navigator.locks) throw new Error("This browser cannot coordinate review progress across tabs.");
      await navigator.locks.request("goldscout-recording-confirmation", async () => {
        if (JSON.stringify(readReviewCycle(data.portfolio_id)) !== JSON.stringify(cycle))
          throw new Error("This review changed in another tab. Refresh before starting a new review.");
        await requireCurrentSnapshot(data, refreshSnapshot);
        localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify(next));
      });
      setCycle(next);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The browser could not save this review session.");
    }
  }

  async function markReviewed(accountId: string) {
    if (!cycle || !currentCycle || !cycle.accountIds.includes(accountId)) return;
    try {
      if (!await markAccountInReview(data, accountId, cycle, refreshSnapshot)) {
        setCycle(readReviewCycle(data.portfolio_id));
        setError("The review changed. Refresh the portfolio before continuing.");
        return;
      }
      setCycle(readReviewCycle(data.portfolio_id));
      setPreferences(readPreferences(data.portfolio_id));
      setError("");
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : "The browser could not save review progress."} A reminder date may have been saved; check it before continuing. No journal record was changed.`);
    }
  }

  async function finishReview() {
    try {
      if (!navigator.locks) throw new Error("This browser cannot coordinate review progress across tabs.");
      await navigator.locks.request("goldscout-recording-confirmation", async () => {
        const latest = readReviewCycle(data.portfolio_id);
        if (!latest || !sameReview(latest, cycle) || latest.revision !== data.revision ||
            latest.accountIds.length !== activeIds.length ||
            !latest.accountIds.every((id, index) => id === activeIds[index]) ||
            latest.checkedAccountIds.length !== activeIds.length)
          throw new Error("The review changed. Refresh the portfolio before finishing.");
        await requireCurrentSnapshot(data, refreshSnapshot);
        if (data.complete && data.as_of && !onFinishReview()) return;
        const next = { ...latest, finishedAt: new Date().toISOString() };
        localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify(next));
        setCycle(next);
      });
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : "The review could not be finished."} A comparison value may already have been saved.`);
    }
  }

  return <section id="account-review" className="history-group account-maintenance" aria-label="Account freshness and next actions">
    <h2>Account status and next action</h2>
    <p className="position-note">These are reminders based on your chosen review interval and recorded journal dates.
      They do not verify a bank or broker. A complete value for an older date is not a current balance.</p>
    {defaultsStatus === "loading" && <p role="status">Loading workspace review defaults. Saved account-specific choices remain in effect.</p>}
    {defaultsStatus === "unavailable" && <p role="alert">Workspace review defaults are unavailable; they have not been replaced with assumed settings.
      {" "}Saved account-specific choices remain in effect. Choose an interval for each account or <NavLink to="/settings">retry Settings</NavLink>.</p>}
    <section className="account-review-cycle" aria-label="Guided portfolio review">
      <h3>Guided account review</h3>
      {activeIds.length === 0 ? <p>No active accounts are available to review.</p> : <>
        {currentCycle ? <>
          <p role="status">{checkedIds.length} of {activeIds.length} accounts checked in this review.
            {" "}Started {timestamp(cycle.startedAt)}.</p>
          {nextAccount && <p>Next: {accountDisplay(data, nextAccount)}.</p>}
          {checkedIds.length === activeIds.length && <>
            <p><strong>Accounts checked.</strong> {unresolved.length === 0
              ? "This checklist found no unresolved items. Review the portfolio warnings and limitations separately; this is not provider verification."
              : `${unresolved.length} unresolved item${unresolved.length === 1 ? "" : "s"} remain. Review the account actions below; completion is not an all-clear.`}</p>
            {unresolved.length > 0 && <ul aria-label="Unresolved review items">
              {unresolved.map(item => <li key={item}>{item}</li>)}
            </ul>}
            {!cycle.finishedAt ? <button type="button" onClick={() => void finishReview()}>
              {data.complete && data.as_of ? "Finish review and save this value for next time" : "Finish review"}</button>
              : <p role="status">Review complete. Finished {timestamp(cycle.finishedAt)}. A comparison value is saved separately when available.
                {" "}Outstanding items remain visible below. This does not verify your accounts.</p>}
          </>}
        </> : cycle ? <p role="status">The journal changed after this review began. Restart the review to use current records; previous progress remains saved but is not counted.</p>
          : <p>Review each active account in turn. Progress is saved in this browser and is not an accounting record.</p>}
        {currentCycle && nextAccount
          ? <div className="recording-actions">
            <NavLink className="record-action"
              to={`/investment/update?account_id=${encodeURIComponent(nextAccount)}`}>Continue review</NavLink>
            <button type="button" onClick={() => void startReview()}>Restart review</button>
          </div>
          : <button type="button" onClick={() => void startReview()}>
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
      const preference = effectivePreferences[account.id];
      const savedPreference = preferences[account.id];
      const hasAccountReviewInterval = savedPreference?.intervalDays !== null &&
        savedPreference?.intervalDays !== undefined;
      const hasAccountTermsInterval = savedPreference?.termsIntervalDays !== null &&
        savedPreference?.termsIntervalDays !== undefined;
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
      const reviewDue = isReviewDue(preference, reviewNow);
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
          <div><h3>{accountDisplay(data, account.id)}</h3><span>{account.kind === "deposit" ? "Savings account" : "Investment account"}</span></div>
          <strong>{needsAction ? "Action needed" : reviewNeeded ? "Check due"
            : preference?.intervalDays == null ? "Freshness unknown"
              : hasAccountReviewInterval ? "Within account interval" : "Within workspace default"}</strong>
        </div>
        <p className="account-next-action"><strong>Next action:</strong> {action}</p>
        {preference?.reviewedAt && preference.intervalDays !== null && <p className="position-note">
          Next reminder: {timestamp(new Date(Date.parse(preference.reviewedAt) + preference.intervalDays * 86400000).toISOString())}.
        </p>}
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
          <span className="account-setting-source">{hasAccountReviewInterval
            ? "Saved for this account in this browser."
            : preference?.intervalDays !== null && preference?.intervalDays !== undefined
              ? "Using the workspace default. Changing this saves a separate choice for this account."
              : "No interval is set. Choose one to start a local reminder."}</span>
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
          <span className="account-setting-source">{hasAccountTermsInterval
            ? "Saved for this account in this browser."
            : preference?.termsIntervalDays !== null && preference?.termsIntervalDays !== undefined
              ? "Using the workspace default. Changing this saves a separate choice for this account."
              : "No interval is set. Choose one to start a local reminder."}</span>
        </label>}
        <div className="recording-actions">
          <NavLink className="record-action"
            to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>Update this account</NavLink>
          {missingPrice && marks.filter(holding => holding.value_gbp === null).map(holding =>
            <NavLink key={holding.product_id}
              to={`/investment/record?record=account-update&account_id=${encodeURIComponent(account.id)}&kind=mark&product_id=${encodeURIComponent(holding.product_id)}`}>
              Update price for {holding.name}
            </NavLink>)}
          {cashCheckStale && <NavLink
            to={`/investment/record?record=account-update&account_id=${encodeURIComponent(account.id)}&kind=cash_observation`}>
            Check this account's cash
          </NavLink>}
          {account.kind === "deposit" && (savingsTermsStale || maturityNeedsCheck) && <NavLink
            to={`/investment/record?record=account-update&account_id=${encodeURIComponent(account.id)}&kind=savings_terms`}>
            Record this account's savings terms
          </NavLink>}
          {(cashDifference || earlierCashDifference || correctionCouldChangeChecks) &&
            <a href="#investment-accounts" onClick={onReviewCashEvidence}>Review cash evidence</a>}
          {currentCycle && cycle && cycle.accountIds.includes(account.id) &&
            !checkedIds.includes(account.id) && <button type="button" disabled={error !== ""}
              onClick={() => void markReviewed(account.id)}>
              Mark reviewed in this session
            </button>}
          {preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
            !currentCycle && <button type="button" disabled={error !== ""}
            onClick={() => update(account.id, { reviewedAt: new Date().toISOString() })}>
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
