import { useEffect, useMemo, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import Decimal from "decimal.js";
import { evidenceAge, pounds, timestamp } from "./api";
import { readReviewCycle, reviewCanCheckAccount, reviewStorageKey, sameReview,
  savingsTermsNeedReview } from "./guided-review";
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

function parseSavedPreferenceJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("Saved account review settings are invalid. They were not changed.");
  }
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
    const value = parseSavedPreferenceJson(raw);
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
    result[id] = parsePreference(parseSavedPreferenceJson(saved));
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

function unrecordedSavingsDetails(terms: InvestmentSnapshot["accounts"][number]["terms"]) {
  if (!terms) return null;
  const missing = [
    terms.notice_days === null ? "notice period" : null,
    terms.maturity_date === null ? "maturity date" : null,
  ].filter((item): item is string => item !== null);
  if (missing.length === 0) return null;
  return `Recorded terms: ${missing.join(" and ")} ${missing.length === 1 ? "is" : "are"} not recorded; check whether ${missing.length === 1 ? "it applies" : "they apply"}.`;
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
    const termsStale = account.kind === "deposit" &&
      savingsTermsNeedReview(account.terms, preference?.termsIntervalDays, now);
    const maturityDays = account.terms?.maturity_date
      ? Math.ceil((Date.parse(`${account.terms.maturity_date}T00:00:00Z`) - now) / 86400000)
      : null;
    const name = accountDisplay(data, account.id);
    const missingSavingsDetails = account.kind === "deposit" ? unrecordedSavingsDetails(account.terms) : null;
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
      unresolved.push(`${name} savings terms are incomplete, not yet effective or need rechecking`);
    if (missingSavingsDetails) unresolved.push(`${name}: ${missingSavingsDetails}`);
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
  expectedCycle: ReviewCycle, refresh: () => Promise<InvestmentSnapshot>,
  onPreferencesReadFailure: () => void) {
  if (!navigator.locks) throw new Error("This browser cannot coordinate review progress across tabs.");
  return navigator.locks.request("goldscout-recording-confirmation", async () => {
    const cycle = readReviewCycle(data.portfolio_id);
    const activeIds = data.accounts.filter(account => account.status === "active").map(account => account.id);
    if (!cycle || !reviewCanCheckAccount(cycle, data.revision, accountId) ||
        !sameReview(cycle, expectedCycle) ||
        cycle.accountIds.length !== activeIds.length ||
        !cycle.accountIds.every((id, index) => id === activeIds[index])) return false;
    await requireCurrentSnapshot(data, refresh);
    let preferences: PreferenceState;
    try {
      preferences = readPreferences(data.portfolio_id);
    } catch (reason) {
      onPreferencesReadFailure();
      throw reason;
    }
    const preference = preferences[accountId] ?? {
      intervalDays: null, termsIntervalDays: null, reviewedAt: null,
    };
    savePreference(data.portfolio_id, accountId, {
      ...preference, reviewedAt: new Date().toISOString(),
    });
    const checkedAccountIds = [...cycle.checkedAccountIds, accountId];
    localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify({ ...cycle, checkedAccountIds }));
    return true;
  });
}

function AccountMaintenance({ data, now, mode, accountId, onFinishReview, refreshSnapshot,
  defaults, defaultsStatus }: {
  data: InvestmentSnapshot; now: number; mode: "overview" | "account"; accountId?: string;
  onFinishReview?: () => boolean;
  refreshSnapshot: () => Promise<InvestmentSnapshot>;
  defaults: { reviewIntervalDays: number; termsIntervalDays: number } | null;
  defaultsStatus: "loading" | "unavailable" | "ready";
}) {
  const navigate = useNavigate();
  const [preferences, setPreferences] = useState<PreferenceState>({});
  const [preferencesUnavailable, setPreferencesUnavailable] = useState(false);
  const [cycle, setCycle] = useState<ReviewCycle | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      setPreferences(readPreferences(data.portfolio_id));
      setPreferencesUnavailable(false);
      try {
        setCycle(readReviewCycle(data.portfolio_id));
        setError("");
      } catch (reason) {
        setCycle(null);
        setError(reason instanceof Error ? reason.message : "Saved guided review could not be read.");
      }
    } catch (reason) {
      setPreferences({});
      setPreferencesUnavailable(true);
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
        setPreferencesUnavailable(false);
        try {
          setCycle(readReviewCycle(data.portfolio_id));
          setError("");
        } catch (reason) {
          setCycle(null);
          setError(reason instanceof Error ? reason.message : "Saved guided review could not be read.");
        }
      } catch (reason) {
        setPreferencesUnavailable(true);
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
  const unresolved = [
    ...(preferencesUnavailable ? ["Saved review settings could not be read; reminder status is unknown."] : []),
    ...unresolvedReviewItems(data, activeIds, effectivePreferences, now, reviewNow),
  ];
  const unmatchedPreferences = useMemo(() => Object.keys(preferences)
    .filter(id => !data.accounts.some(account => account.id === id)), [data.accounts, preferences]);

  async function update(accountId: string, changes: Partial<AccountPreference>) {
    let readFailed = false;
    try {
      if (!navigator.locks) throw new Error("This browser cannot coordinate review settings across tabs.");
      const updated = await navigator.locks.request("goldscout-recording-confirmation", () => {
        let current: PreferenceState;
        try {
          current = readPreferences(data.portfolio_id);
        } catch (reason) {
          readFailed = true;
          throw reason;
        }
        const previous = current[accountId];
        const intervalDays = changes.intervalDays ?? previous?.intervalDays ?? null;
        const termsIntervalDays = changes.termsIntervalDays !== undefined
          ? changes.termsIntervalDays : previous?.termsIntervalDays ?? null;
        if (intervalDays === null && termsIntervalDays === null && changes.reviewedAt === undefined)
          throw new Error("Choose a review interval first.");
        const next: AccountPreference = {
          intervalDays, termsIntervalDays,
          reviewedAt: changes.reviewedAt ?? previous?.reviewedAt ?? null,
        };
        savePreference(data.portfolio_id, accountId, next);
        return { ...current, [accountId]: next };
      });
      setPreferences(updated);
      setPreferencesUnavailable(false);
      setError("");
    } catch (reason) {
      if (readFailed) setPreferencesUnavailable(true);
      setError(`${reason instanceof Error ? reason.message : "The browser could not save this local review setting."} No journal record was changed.`);
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
      if (next.accountIds[0])
        navigate(`/investment/update?account_id=${encodeURIComponent(next.accountIds[0])}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The browser could not save this review session.");
    }
  }

  async function finishReview() {
    try {
      if (!navigator.locks) throw new Error("This browser cannot coordinate review progress across tabs.");
      await navigator.locks.request("goldscout-recording-confirmation", async () => {
        const latest = readReviewCycle(data.portfolio_id);
        if (!latest || latest.finishedAt || !sameReview(latest, cycle) || latest.revision !== data.revision ||
            latest.accountIds.length !== activeIds.length ||
            !latest.accountIds.every((id, index) => id === activeIds[index]) ||
            latest.checkedAccountIds.length !== activeIds.length)
          throw new Error("The review changed. Refresh the portfolio before finishing.");
        await requireCurrentSnapshot(data, refreshSnapshot);
        try {
          setPreferences(readPreferences(data.portfolio_id));
          setPreferencesUnavailable(false);
        } catch (reason) {
          setPreferencesUnavailable(true);
          throw reason;
        }
        if (data.complete && data.as_of) {
          if (!onFinishReview) throw new Error("The comparison point is unavailable on this page.");
          if (!onFinishReview()) return;
        }
        const next = { ...latest, finishedAt: new Date().toISOString() };
        localStorage.setItem(reviewStorageKey(data.portfolio_id), JSON.stringify(next));
        setCycle(next);
        setError("");
      });
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : "The review could not be finished."} A comparison value may already have been saved.`);
    }
  }

  return <section id={mode === "overview" ? "account-review" : undefined}
    className="history-group account-maintenance" aria-label={mode === "overview"
      ? "Account freshness and next actions" : "Account review reminders"}>
    <h2>{mode === "overview" ? "Accounts" : "Review reminders"}</h2>
    <p className="position-note">These are reminders based on your chosen review interval and recorded journal dates.
      They do not verify a bank or broker. A complete value for an older date is not a current balance.</p>
    {defaultsStatus === "loading" && <p role="status">Loading workspace review defaults. Saved account-specific choices remain in effect.</p>}
    {defaultsStatus === "unavailable" && <p role="alert">Workspace review defaults are unavailable; they have not been replaced with assumed settings.
      {" "}Saved account-specific choices remain in effect. Choose an interval for each account or <NavLink to="/settings">retry Settings</NavLink>.</p>}
    {mode === "overview" && <section className="account-review-cycle" aria-label="Guided portfolio review">
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
            {!cycle.finishedAt ? <button type="button" disabled={preferencesUnavailable} onClick={() => void finishReview()}>
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
            <button type="button" disabled={preferencesUnavailable} onClick={() => void startReview()}>Restart review</button>
          </div>
          : <button type="button" disabled={preferencesUnavailable} onClick={() => void startReview()}>
            {cycle ? "Start a new review" : "Start account review"}
          </button>}
      </>}
    </section>}
    {error && <p role="alert">{error}</p>}
    {unmatchedPreferences.length > 0 && <p role="status">
      {unmatchedPreferences.length} saved review setting{unmatchedPreferences.length === 1 ? "" : "s"} refer to account IDs
      no longer in this journal; those settings were left untouched.
    </p>}
    {data.accounts.filter(account => account.status === "active" &&
      (mode === "overview" || account.id === accountId)).map(account => {
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
      const maturityDays = account.terms?.maturity_date
        ? Math.ceil((Date.parse(`${account.terms.maturity_date}T00:00:00Z`) - now) / 86400000)
        : null;
      const missingSavingsDetails = account.kind === "deposit" ? unrecordedSavingsDetails(account.terms) : null;
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
      const savingsTermsStale = account.kind === "deposit" &&
        savingsTermsNeedReview(account.terms, preference?.termsIntervalDays, now);
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
      else if (preferencesUnavailable) action = "Saved review settings could not be read; resolve the error before relying on reminders.";
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

      const status = needsAction ? "Action needed" : preferencesUnavailable ? "Review settings unavailable"
        : reviewNeeded ? "Check due" : preference?.intervalDays == null ? "Freshness unknown"
          : hasAccountReviewInterval ? "Within account interval" : "Within workspace default";
      if (mode === "overview") return <article className="account-maintenance-card account-overview-row" key={account.id}>
        <div className="account-maintenance-heading">
          <div><h3>{accountDisplay(data, account.id)}</h3>
            <span>{account.kind === "deposit" ? "Savings account" : "Investment account"}
              {checkedIds.includes(account.id) && " · Checked in this review"}</span></div>
          <strong>{status}</strong>
        </div>
        <p>{action}{missingSavingsDetails && ` ${missingSavingsDetails}`}</p>
        <NavLink className="text-link" aria-label={`Update ${accountDisplay(data, account.id)}`}
          to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>Open account</NavLink>
      </article>;

      return <article className="account-maintenance-card" key={account.id}>
        <div className="account-maintenance-heading">
          <div><h3>Review status for {accountDisplay(data, account.id)}</h3><span>{account.kind === "deposit" ? "Savings account" : "Investment account"}</span></div>
          <strong>{status}</strong>
        </div>
        <p className="account-next-action"><strong>Next action:</strong> {action}
          {missingSavingsDetails && ` ${missingSavingsDetails}`}</p>
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
              {` · observed ${timestamp(account.terms.observed_at)} (age: ${evidenceAge(account.terms.observed_at, now)})`}
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
              void update(account.id, { intervalDays });
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
              void update(account.id, { termsIntervalDays });
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
          {(cashDifference || earlierCashDifference || correctionCouldChangeChecks) &&
            <NavLink to="/investment?view=history#investment-accounts">Review cash evidence</NavLink>}
          {preference?.intervalDays !== null && preference?.intervalDays !== undefined &&
            (!currentCycle || cycle?.finishedAt) && <button type="button" disabled={preferencesUnavailable}
            onClick={() => void update(account.id, { reviewedAt: new Date().toISOString() })}>
            Mark account checked today
          </button>}
        </div>
      </article>;
    })}
    <p className="position-note">Review intervals, account check dates and guided-review progress are saved in this browser only.
      Marking an account checked sets only a reminder date; it does not save a portfolio
      comparison point, change the journal or resolve differences. eToro remains a separate read-only view and is not mapped into these totals.</p>
    {mode === "account" && <><p className="position-note">The dashboard has a common-date portfolio valuation, not a separately reconstructed balance history for each account.
      Cash and holding values above are the latest journal records and may not share the valuation date.</p>
    <p className="position-note">Recorded trade and account charges: {pounds(data.recorded_fees_gbp)}. This includes only explicitly recorded purchase/sale fees and account charges;
      fund, subscription, FX, tax and other costs may be missing. Savings terms are dated display facts, not an earned-return
      calculation or proof of the best obtainable rate.</p></>}
  </section>;
}

export default AccountMaintenance;
