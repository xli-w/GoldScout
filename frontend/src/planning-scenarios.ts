import Decimal from "decimal.js";

const GoalDecimal = Decimal.clone({ precision: 500 });

export type GoalScenarioInput = {
  target_amount_gbp: string | null;
  target_date: string | null;
  current_amount_gbp: string | null;
  monthly_contribution_gbp: string | null;
};

export type GoalScenario = {
  months: number;
  current_amount_gbp: string;
  target_amount_gbp: string;
  with_no_more_contributions_gbp: string;
  with_monthly_contributions_gbp: string | null;
  remaining_gap_with_no_more_contributions_gbp: string;
  remaining_gap_with_monthly_contributions_gbp: string | null;
};

export function goalScenario(goal: GoalScenarioInput, today = new Date()): GoalScenario | null {
  if (goal.target_amount_gbp === null || goal.target_date === null ||
    goal.current_amount_gbp === null) return null;
  const amount = /^(?:0|[1-9]\d*)(?:\.\d+)?$/;
  if (!amount.test(goal.target_amount_gbp) || !amount.test(goal.current_amount_gbp) ||
      goal.monthly_contribution_gbp !== null && !amount.test(goal.monthly_contribution_gbp))
    return null;
  const targetDate = new Date(`${goal.target_date}T00:00:00Z`);
  if (!Number.isFinite(targetDate.getTime())) return null;

  const firstContribution = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth() + 1, 1));
  let months = 0;
  while (firstContribution <= targetDate) {
    months++;
    firstContribution.setUTCMonth(firstContribution.getUTCMonth() + 1);
  }

  const target = new GoalDecimal(goal.target_amount_gbp);
  if (target.lte(0)) return null;
  const current = new GoalDecimal(goal.current_amount_gbp);
  const noMore = current;
  const gap = (amount: Decimal) => GoalDecimal.max(new GoalDecimal(0), target.minus(amount)).toFixed(2);
  const monthly = goal.monthly_contribution_gbp === null
    ? null : current.plus(new GoalDecimal(goal.monthly_contribution_gbp).mul(months));
  return {
    months,
    current_amount_gbp: current.toFixed(2),
    target_amount_gbp: target.toFixed(2),
    with_no_more_contributions_gbp: noMore.toFixed(2),
    with_monthly_contributions_gbp: monthly?.toFixed(2) ?? null,
    remaining_gap_with_no_more_contributions_gbp: gap(noMore),
    remaining_gap_with_monthly_contributions_gbp: monthly ? gap(monthly) : null,
  };
}
