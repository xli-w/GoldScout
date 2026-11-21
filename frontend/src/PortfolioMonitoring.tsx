import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink } from "react-router-dom";
import Decimal from "decimal.js";
import validate from "./monitoring-validator.js";
import { label, timestamp } from "./api";

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
  return <section className="history-group portfolio-monitoring" aria-label="Portfolio monitoring">
    <h2>Checks from saved information</h2>
    <p className="position-note">Optional checks use saved evidence only; they do not verify accounts or change records.</p>
    <p><NavLink className="text-link" to="/investment/reviews">Review checks and compare account figures</NavLink>
      {" "}to track findings or compare figures using a separate monitoring store.</p>
    {query.isError || mismatch ? <p role="alert">{mismatch && !query.isError
      ? "Investment revision changed. Refresh both views before interpreting findings."
      : "Saved checks are unavailable. Previous findings are not current."}
        {" "}<button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Try again</button></p>
      : query.isPending ? <p>Loading saved checks...</p>
      : data?.enabled === false ? <p>These optional checks are not set up. Your portfolio works without them.
        {" "}<NavLink to="/investment-guide#monitoring-setup">Learn about optional checks</NavLink>.</p>
      : data?.enabled && <>
        <div className="monitoring-actions">
          <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh checks</button>
          <label><input type="checkbox" checked={poll} onChange={event => setPoll(event.target.checked)} /> Refresh checks every minute</label>
        </div>
        <p>{data.source_mode === "demo" ? "Synthetic monitoring demonstration" : "Recorded local evidence"}; checked {timestamp(data.generated_at)}.</p>
        <p>Provider connections are not verified. These checks use saved evidence only.</p>
        <h3>Findings</h3>
        <p>Recording a review status does not resolve a finding.</p>
        {data.findings.length === 0
          ? <p>No findings in this report. These checks do not verify every account or provider.</p>
          : <ul>{data.findings.map(item => <li key={item.id}>
            <strong>{item.subject}: {label(item.status)}</strong> - {item.explanation}
            {item.calculation !== null && <> Calculation: {item.calculation}.</>}
            <p>Next check: {item.recommendation}</p>
            <details><summary>Evidence</summary><p>{item.evidence}</p>
              <p>Rule {item.rule}. No accounting change or trade is approved.</p>
            </details>
          </li>)}</ul>}
        <details className="help-disclosure"><summary>Source status</summary>
          <p>Journal revision {data.portfolio_revision}; cache revision {data.cache_revision ?? "not selected"}.
            Readiness does not confirm provider connectivity.</p>
          <p>Dynamic-role share: {data.dynamic_pct === null ? "Unavailable"
            : `${new Decimal(data.dynamic_pct).toFixed(2)}%`} of eligible non-LISA investment holdings
            {data.eligible_holdings_gbp !== null && <> (GBP {data.eligible_holdings_gbp})</>}. It is not a target.
            Savings, LISA and cash are excluded.</p>
          {data.providers.length > 0 && <ul aria-label="Provider evidence status">{data.providers.map(item =>
            <li key={item.account_id}><strong>{item.account_id} / {item.provider}</strong>: {label(item.readiness)};
              freshness {label(item.freshness)}; coverage {label(item.coverage)}; currency {item.currency ?? "unknown"}.
              Last attempt {timestamp(item.last_attempt_at)} ({item.last_attempt_status ?? "none"});
              last success {timestamp(item.last_success_at)}; source as-of {timestamp(item.source_as_of)}.
              {item.native_summary && <> Source credit field (USD): {item.native_summary.credit_usd};
                observed positions: {item.native_summary.position_count}. Not verified cash, GBP value or complete holdings.</>}
            </li>)}</ul>}
        </details>
        <details className="help-disclosure"><summary>Coverage and limitations</summary>
          <ul>{data.limitations.map(item => <li key={item}>{item}</li>)}</ul>
        </details>
      </>}
  </section>;
}
