import { useEffect, useMemo, useState } from "react";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import { evidenceAge, pounds, timestamp } from "./api";
import type { InvestmentSnapshot } from "./InvestmentView";

type AccountPreference = { intervalDays: number; reviewedAt: string | null };
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
  if (Object.keys(item).sort().join(",") !== "intervalDays,reviewedAt" ||
    typeof intervalDays !== "number" || !INTERVALS.includes(intervalDays) ||
    !(reviewedAt === null || typeof reviewedAt === "string" &&
      Number.isFinite(Date.parse(reviewedAt))))
    throw new Error("Saved account review settings are invalid. They were not changed.");
  return { intervalDays, reviewedAt };
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

function savePreference(portfolioId: string, accountId: string, value: AccountPreference) {
  localStorage.setItem(accountStorageKey(portfolioId, accountId), JSON.stringify(value));
}

function AccountMaintenance({ data, now }: { data: InvestmentSnapshot; now: number }) {
  const [preferences, setPreferences] = useState<PreferenceState>({});
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      setPreferences(readPreferences(data.portfolio_id));
      setError("");
    } catch (reason) {
      setPreferences({});
      setError(reason instanceof Error ? reason.message : "Saved review settings could not be read.");
    }
  }, [data.portfolio_id]);

  useEffect(() => {
    const prefix = accountStoragePrefix(data.portfolio_id);
    const legacyKey = STORAGE_PREFIX + data.portfolio_id;
    function onStorage(event: StorageEvent) {
      if (event.key !== null && event.key !== legacyKey && !event.key.startsWith(prefix)) return;
      try {
        setPreferences(readPreferences(data.portfolio_id));
        setError("");
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Saved review settings could not be read.");
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [data.portfolio_id]);

  const unmatchedPreferences = useMemo(() => Object.keys(preferences)
    .filter(id => !data.accounts.some(account => account.id === id)), [data.accounts, preferences]);

  function update(accountId: string, changes: Partial<AccountPreference>) {
    try {
      const current = readPreferences(data.portfolio_id);
      const intervalDays = changes.intervalDays ?? current[accountId]?.intervalDays ??
        preferences[accountId]?.intervalDays;
      if (intervalDays === undefined) throw new Error("Choose an account review interval first.");
      const next: AccountPreference = {
        intervalDays,
        reviewedAt: changes.reviewedAt ?? current[accountId]?.reviewedAt ??
          preferences[accountId]?.reviewedAt ?? null,
      };
      savePreference(data.portfolio_id, accountId, next);
      setPreferences({ ...current, [accountId]: next });
      setError("");
    } catch {
      setError("The browser could not save this local review setting. No journal record was changed.");
    }
  }

  return <section className="history-group account-maintenance" aria-label="Account freshness and next actions">
    <h2>Account status and next action</h2>
    <p className="position-note">These are reminders based on your chosen review interval and recorded journal dates.
      They do not verify a bank or broker. A complete value for an older date is not a current balance.</p>
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
      const cashCheckAge = latestCashCheck === null ? null : now - Date.parse(latestCashCheck.timestamp);
      const termsAge = account.terms === null ? null : now - Date.parse(account.terms.available_at);
      const maturityDays = account.terms?.maturity_date
        ? Math.ceil((Date.parse(`${account.terms.maturity_date}T00:00:00Z`) - now) / 86400000)
        : null;
      const asOfAge = data.as_of === null ? null : now - Date.parse(data.as_of);
      const markAge = marks.map(holding => holding.mark_at === null ? null : now - Date.parse(holding.mark_at));
      const oldestMarkAge = markAge.length === 0 || markAge.some(age => age === null)
        ? null : Math.max(...markAge.map(age => age ?? 0));
      const stale = preference !== undefined && (asOfAge === null ||
        asOfAge < 0 || asOfAge > preference.intervalDays * 86400000 ||
        oldestMarkAge !== null && (oldestMarkAge < 0 ||
          oldestMarkAge > preference.intervalDays * 86400000));
      const cashCheckStale = preference !== undefined && (cashCheckAge === null ||
        cashCheckAge < 0 || cashCheckAge > preference.intervalDays * 86400000);
      const savingsTermsStale = account.kind === "deposit" && preference !== undefined &&
        (termsAge === null || !Number.isFinite(termsAge) || termsAge < 0 ||
          termsAge > preference.intervalDays * 86400000);
      const maturityNeedsCheck = account.kind === "deposit" &&
        maturityDays !== null && maturityDays <= 90;
      const reviewDue = preference !== undefined && (preference.reviewedAt === null ||
        now - Date.parse(preference.reviewedAt) >= preference.intervalDays * 86400000);
      const needsAttention = stale || cashDifference || savingsTermsStale ||
        maturityNeedsCheck || cashCheckStale || reviewDue;
      let action: string;
      if (preference === undefined) action = "Choose how often you expect to review this account.";
      else if (!data.complete || missingPrice) action = "Update the recorded evidence; its full value is unavailable.";
      else if (stale) action = "Update the account records; the latest complete value is past your chosen interval.";
      else if (cashDifference) action = "Review the recorded cash difference.";
      else if (savingsTermsStale) action = account.terms === null
        ? "Record current savings rate, access, notice and maturity terms from a dated source."
        : "Recheck the savings rate, access, notice and maturity terms from a dated source.";
      else if (maturityNeedsCheck)
        action = "Confirm the recorded maturity date and access or notice options using current provider information.";
      else if (cashCheckStale) action = "Check the latest cash balance against a dated source.";
      else if (reviewDue) action = "Review this account's records and terms.";
      else action = "No review is due under your chosen interval.";

      return <article className="account-maintenance-card" key={account.id}>
        <div className="account-maintenance-heading">
          <div><h3>{account.name}</h3><span>{account.kind === "deposit" ? "Savings account" : "Investment account"}</span></div>
          <strong>{preference === undefined ? "Freshness unknown" : needsAttention ? "Check due" : "Within chosen interval"}</strong>
        </div>
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
        <p className="account-next-action"><strong>Next action:</strong> {action}</p>
        <div className="recording-actions">
          <NavLink className="record-action"
            to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>Update this account</NavLink>
          {preference && <button type="button" disabled={error !== ""}
            onClick={() => update(account.id, { reviewedAt: new Date(now).toISOString() })}>
            Record local review date
          </button>}
        </div>
      </article>;
    })}
    <p className="position-note">Review intervals and review dates are saved in this browser only. They are not journal evidence,
      provider checks or proof that an account is current. eToro remains a separate read-only view and is not mapped into these totals.</p>
    <p className="position-note">The dashboard has a common-date portfolio valuation, not a separately reconstructed balance history for each account.
      Cash and holding values above are the latest journal records and may not share the valuation date.</p>
    <p className="position-note">Recorded transaction fees: {pounds(data.recorded_fees_gbp)}. This includes only explicitly recorded purchase/sale fees;
      platform, fund, subscription, FX, tax and other costs may be missing. Savings terms are dated display facts, not an earned-return
      calculation or proof of the best obtainable rate.</p>
  </section>;
}

export default AccountMaintenance;
