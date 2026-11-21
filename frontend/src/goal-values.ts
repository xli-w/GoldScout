import Decimal from "decimal.js";
import type { InvestmentSnapshot } from "./InvestmentView";
import type { GoalInput } from "./planning-api";

const GoalDecimal = Decimal.clone({ precision: 500 });

export function recordedAccountValues(data: InvestmentSnapshot | null) {
  if (!data || data.source_mode !== "recorded" || !data.complete ||
      data.equity_gbp === null || data.as_of === null ||
      data.accounts.some(account => account.cash_gbp === null) ||
      data.holdings.some(holding => holding.value_gbp === null)) return null;
  const allValues = new Map<string, Decimal>();
  for (const account of data.accounts)
    allValues.set(account.id, new GoalDecimal(account.cash_gbp!));
  for (const holding of data.holdings)
    allValues.set(holding.account_id,
      (allValues.get(holding.account_id) ?? new GoalDecimal(0)).plus(holding.value_gbp!));
  const total = [...allValues.values()].reduce(
    (value, amount) => value.plus(amount), new GoalDecimal(0));
  if (!total.eq(data.equity_gbp)) return null;
  return new Map(data.accounts.filter(account => account.status === "active")
    .map(account => [account.id, allValues.get(account.id) ?? new GoalDecimal(0)]));
}

export function duplicateAccountLinks(goals: Pick<GoalInput, "linked_account_ids">[]) {
  const ids = goals.flatMap(goal => goal.linked_account_ids);
  return new Set(ids.filter((id, index) => ids.indexOf(id) !== index));
}

export function linkedGoalValue(goal: Pick<GoalInput, "linked_portfolio_id" | "linked_account_ids">,
  data: InvestmentSnapshot | null, values: Map<string, Decimal> | null, duplicates: Set<string>) {
  if (!goal.linked_account_ids.length || !data || !values ||
      goal.linked_portfolio_id !== data.portfolio_id ||
      goal.linked_account_ids.some(id => duplicates.has(id))) return null;
  let total = new GoalDecimal(0);
  for (const id of goal.linked_account_ids) {
    const value = values.get(id);
    if (!value) return null;
    total = total.plus(value);
  }
  return total;
}
