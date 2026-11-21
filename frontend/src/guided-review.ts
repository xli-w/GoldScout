import type { InvestmentSnapshot } from "./InvestmentView";

export type ReviewCycle = { version: 1; portfolioId: string; revision: number; startedAt: string;
  accountIds: string[]; checkedAccountIds: string[]; finishedAt?: string; reviewId?: string };

export function reviewStorageKey(portfolioId: string) {
  return `goldscout.guided-review.v1.${encodeURIComponent(portfolioId)}`;
}

export function readReviewCycle(portfolioId: string): ReviewCycle | null {
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
  const keys = Object.keys(item);
  if (keys.some(key => !["accountIds", "checkedAccountIds", "finishedAt", "portfolioId",
        "revision", "reviewId", "startedAt", "version"].includes(key)) ||
      ("reviewId" in item && (typeof item.reviewId !== "string" ||
        item.reviewId.length < 1 || item.reviewId.length > 200)) ||
      item.version !== 1 || item.portfolioId !== portfolioId ||
      typeof item.revision !== "number" || !Number.isSafeInteger(item.revision) || item.revision < 0 ||
      typeof item.startedAt !== "string" || !Number.isFinite(Date.parse(item.startedAt)) ||
      ("finishedAt" in item && (typeof item.finishedAt !== "string" ||
        !Number.isFinite(Date.parse(item.finishedAt)))) ||
      !uniqueIds(accountIds) || !uniqueIds(checkedAccountIds) ||
      checkedAccountIds.some(id => !accountIds.includes(id)))
    throw new Error("Saved guided review is invalid. It was not changed.");
  return item as ReviewCycle;
}

export function sameReview(a: ReviewCycle | null, b: ReviewCycle | null) {
  return Boolean(a && b && (a.reviewId && b.reviewId
    ? a.reviewId === b.reviewId : !a.reviewId && !b.reviewId && a.startedAt === b.startedAt));
}

export function reviewCanCheckAccount(cycle: ReviewCycle | null, revision: number, accountId: string) {
  return Boolean(cycle && !cycle.finishedAt && cycle.revision === revision &&
    cycle.accountIds.includes(accountId) && !cycle.checkedAccountIds.includes(accountId));
}

export function savingsTermsNeedReview(terms: InvestmentSnapshot["accounts"][number]["terms"],
  intervalDays: number | null | undefined, now: number) {
  if (!terms || intervalDays == null || !Number.isFinite(intervalDays) || intervalDays <= 0 ||
      terms.annual_rate_pct === null || terms.rate_basis === null) return true;
  const observedAge = now - Date.parse(terms.observed_at);
  const availableAge = now - Date.parse(terms.available_at);
  const effective = Date.parse(`${terms.effective_from}T00:00:00Z`);
  return !Number.isFinite(observedAge) || observedAge < 0 || observedAge > intervalDays * 86400000 ||
    !Number.isFinite(availableAge) || availableAge < 0 ||
    !Number.isFinite(effective) || effective > now;
}

export function advanceReviewAfterSave(portfolioId: string, revision: number,
  event: Record<string, unknown>) {
  const cycle = readReviewCycle(portfolioId);
  if (!cycle || cycle.revision + 1 !== revision) return;
  const supported = ["contribution", "withdrawal", "transfer", "interest", "dividend",
    "account_fee", "buy", "sell", "split", "mark", "cash_observation", "savings_terms"];
  if (typeof event.type !== "string" || !supported.includes(event.type) ||
      typeof event.account_id !== "string" || !cycle.accountIds.includes(event.account_id)) return;
  const affected = [event.account_id];
  if (event.type === "transfer") {
    if (typeof event.destination_account_id !== "string" ||
        !cycle.accountIds.includes(event.destination_account_id)) return;
    affected.push(event.destination_account_id);
  }
  const { finishedAt: _finishedAt, ...unfinished } = cycle;
  localStorage.setItem(reviewStorageKey(portfolioId), JSON.stringify({
    ...unfinished, revision, checkedAccountIds: cycle.checkedAccountIds.filter(id => !affected.includes(id)),
  }));
}
