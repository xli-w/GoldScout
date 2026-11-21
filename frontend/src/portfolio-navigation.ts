export type PortfolioView = "overview" | "history" | "tools";

const HISTORY_FRAGMENTS = new Set([
  "#investment-account-summary", "#investment-holdings", "#investment-performance",
  "#investment-roles", "#investment-records", "#investment-corrections", "#investment-accounts",
]);
const TOOL_FRAGMENTS = new Set([
  "#investment-monitoring", "#investment-awareness", "#investment-etoro", "#investment-backup",
]);

export function portfolioView(search: string): PortfolioView {
  const requested = new URLSearchParams(search).get("view");
  return requested === "history" || requested === "tools" ? requested : "overview";
}

export function portfolioFragmentView(hash: string): PortfolioView | null {
  if (HISTORY_FRAGMENTS.has(hash)) return "history";
  if (TOOL_FRAGMENTS.has(hash)) return "tools";
  if (hash === "#account-review" || hash === "#investment-overview") return "overview";
  return null;
}

export function portfolioViewLocation(view: PortfolioView, hash = "") {
  return { pathname: "/investment", search: view === "overview" ? "" : `?view=${view}`, hash };
}
