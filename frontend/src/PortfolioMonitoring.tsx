import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import validate from "./monitoring-validator.js";
import { timestamp } from "./api";

type Finding = {
  id: string; rule: string; subject: string; status: "review" | "unavailable";
  explanation: string; calculation: string | null; evidence: string; recommendation: string;
};
type Monitoring = { enabled: false } | {
  enabled: true; source_mode: "demo" | "recorded"; portfolio_id: string; portfolio_revision: number;
  generated_at: string; cache_revision: number | null; dynamic_pct: string | null;
  eligible_holdings_gbp: string | null; findings: Finding[]; limitations: string[];
  providers: { account_id: string; provider: string; readiness: string; last_attempt_at: string | null;
    last_attempt_status: string | null; last_success_at: string | null; source_as_of: string | null;
    freshness: string; currency: string | null; coverage: string;
    native_summary: { credit_usd: string; position_count: number } | null }[];
};

async function load(signal: AbortSignal): Promise<Monitoring> {
  const response = await fetch("/api/investment/v1/monitoring", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok || !validate(body)) throw new Error("Portfolio monitoring is unavailable or invalid.");
  return body as Monitoring;
}

export function PortfolioMonitoring({ portfolioId, revision }: { portfolioId: string; revision: number }) {
  const [poll, setPoll] = useState(false);
  const query = useQuery({ queryKey: ["monitoring", portfolioId, revision],
    queryFn: ({ signal }) => load(signal), retry: false, refetchInterval: poll ? 60000 : false });
  const data = query.data;
  const mismatch = data?.enabled && (data.portfolio_id !== portfolioId || data.portfolio_revision !== revision);
  return <section className="history-group portfolio-monitoring" id="investment-monitoring" aria-label="Portfolio monitoring">
    <h2>Portfolio monitoring</h2>
    <p className="position-note">Local review findings, not orders or guaranteed improvements. Refresh never collects from a provider.</p>
    <div className="monitoring-actions">
      <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh monitoring</button>
      <label><input type="checkbox" checked={poll} onChange={event => setPoll(event.target.checked)} /> Poll local monitoring every minute</label>
    </div>
    {query.isError || mismatch ? <p role="alert">{mismatch && !query.isError
      ? "Investment revision changed. Refresh both views before interpreting findings."
      : "Portfolio monitoring is unavailable. Previous findings are not current."}</p>
      : query.isPending ? <p>Loading monitoring evidence...</p>
      : data?.enabled === false ? <p>Monitoring is disabled. Select a local monitoring profile for the investment store.</p>
      : data?.enabled && <>
        <p>{data.source_mode === "demo" ? "Synthetic monitoring demonstration" : "Recorded local evidence"}; generated {timestamp(data.generated_at)}; journal revision {data.portfolio_revision}; cache revision {data.cache_revision ?? "not selected"}.</p>
        <p>Dynamic share of eligible non-LISA investment holdings: {data.dynamic_pct === null ? "Unavailable"
          : `${new Decimal(data.dynamic_pct).toFixed(2)}%`}. Savings, LISA and cash excluded. The illustrative 10-20% range is not a limit.</p>
        <h3>Provider readiness and evidence</h3>
        <ul>{data.providers.map(item => <li key={item.account_id}>
          <strong>{item.account_id} / {item.provider}</strong>: {item.readiness}; {item.freshness}; coverage {item.coverage}; currency {item.currency ?? "unknown"}.
          Last attempt {timestamp(item.last_attempt_at)} ({item.last_attempt_status ?? "none"});
          last success {timestamp(item.last_success_at)}; source as-of {timestamp(item.source_as_of)}.
          {item.native_summary && <> Source credit field (USD): {item.native_summary.credit_usd};
            observed positions: {item.native_summary.position_count}. Not verified cash, GBP value or complete holdings.</>}
        </li>)}</ul>
        <h3>Findings for review</h3>
        <p><NavLink className="text-link" to="/investment/reviews">Open the separate review inbox</NavLink>
          {" "}to synchronize and record an append-only workflow action. Inbox status does not resolve a finding.</p>
        <ul>{data.findings.map(item => <li key={item.id}>
          <strong>{item.subject}: {item.status}</strong> - {item.explanation}
          {item.calculation !== null && <> Calculation: {item.calculation}.</>}
          <details><summary>Evidence and next check</summary><p>{item.evidence}</p>
            <p>Rule {item.rule}, action: {item.recommendation}. No accounting change or trade is approved.</p>
          </details>
        </li>)}</ul>
        <details><summary>Monitoring coverage and limitations</summary>
          <ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul>
        </details>
      </>}
  </section>;
}
