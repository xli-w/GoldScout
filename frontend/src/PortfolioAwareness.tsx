import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import validate from "./awareness-validator.js";
import { label, timestamp } from "./api";

type Match = { account_id: string; product_id: string | null; roles: string[]; mapping_evidence: string };
type ThesisEntry = { observed_at: string; status: "watch" | "active" | "invalidated" | "closed";
  thesis: string; invalidation: string; evidence: string };
type Awareness = {
  enabled: true; schema_version: 2; portfolio_id: string; portfolio_revision: number; generated_at: string;
  settings_revision: number; profile_observed_at: string; profile_hash: string;
  calendar_status: string; calendar_available_at: string | null; calendar_revision: number | null;
  alerts: { event_id: string; revision: number; source: string; source_url: string; title: string;
    category: string; scheduled_at: string | null; available_at: string; status: string;
    evidence_status: string; matches: Match[] }[];
  briefs: { brief_id: string; available_at: string; expires_at: string; status: string;
    verification: "unverified"; provider: string; model: string; prompt_version: string;
    input_hash: string; interpretation: string; uncertainties: string[]; recommendation: string;
    symbols: string[]; matches: Match[]; claims: { text: string; evidence_ids: string[] }[];
    sources: { evidence_id: string; source_url: string; title: string;
      published_at: string | null; received_at: string }[] }[];
  reminders: { id: string; title: string; last_reviewed_at: string | null; interval_days: number;
    evidence: string; due_at: string | null; status: string }[];
  theses?: { id: string; title: string; account_id: string; product_id: string | null;
    history: ThesisEntry[] }[];
  calendar_watch?: { id: string; title: string; account_id: string; product_id: string | null;
    scheduled_on: string | null; time_note: string; status: string; observed_at: string; source: string }[];
  warnings: string[];
};

function utcMicroseconds(value: string): bigint | null {
  const parts = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?Z$/.exec(value);
  if (!parts) return null;
  const milliseconds = Date.parse(`${parts[1]}Z`);
  if (!Number.isFinite(milliseconds)) return null;
  return BigInt(milliseconds) * 1000n + BigInt((parts[2] ?? "").padEnd(6, "0"));
}

function validLocalClocks(value: Awareness): boolean {
  if (!value.theses && !value.calendar_watch) return true;
  const profile = utcMicroseconds(value.profile_observed_at), generated = utcMicroseconds(value.generated_at);
  if (profile === null || generated === null || profile > generated) return false;
  const thesisIds = new Set<string>();
  for (const item of value.theses ?? []) {
    if (thesisIds.has(item.id)) return false;
    thesisIds.add(item.id);
    let previous: bigint | null = null;
    for (const entry of item.history) {
      const observed = utcMicroseconds(entry.observed_at);
      if (observed === null || observed > profile || (previous !== null && observed <= previous)) return false;
      previous = observed;
    }
  }
  const watchIds = new Set<string>();
  return (value.calendar_watch ?? []).every(item => {
    const observed = utcMicroseconds(item.observed_at);
    if (watchIds.has(item.id)) return false;
    watchIds.add(item.id);
    return observed !== null && observed <= profile;
  });
}

async function loadAwareness(signal: AbortSignal): Promise<Awareness | { enabled: false }> {
  const response = await fetch("/api/investment/v2/awareness", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  if (!response.ok) throw new Error("Portfolio awareness unavailable. Verify its local profile and evidence stores.");
  const value: unknown = await response.json();
  if (!validate(value)) throw new Error("Portfolio awareness response failed validation.");
  const result = value as Awareness | { enabled: false };
  if (result.enabled && (result.schema_version !== 2 || !validLocalClocks(result))) throw new Error("Portfolio awareness response failed validation.");
  return result;
}

function Matches({ matches }: { matches: Match[] }) {
  return <ul>{matches.map(item => <li key={JSON.stringify([item.account_id, item.product_id])}>
    {item.account_id} / {item.product_id ?? "cash"} ({item.roles.map(label).join(", ")}):
    {" "}{item.mapping_evidence}
  </li>)}</ul>;
}

export function PortfolioAwareness({ portfolioId, revision }: { portfolioId: string; revision: number }) {
  const [polling, setPolling] = useState(false);
  const query = useQuery({ queryKey: ["portfolio-awareness", portfolioId, revision],
    queryFn: ({ signal }) => loadAwareness(signal), refetchInterval: polling ? 60000 : false,
    refetchOnWindowFocus: false, retry: false });
  const value = query.isError ? undefined : query.data;
  const data = value?.enabled ? value : undefined;
  const changed = data && (data.portfolio_id !== portfolioId || data.portfolio_revision !== revision);
  return <section className="history-group" aria-label="Portfolio awareness">
    <h2>Portfolio awareness</h2>
    <p>Local exposure matching only. Alerts, research and reminders cannot change accounting, approve policy or trade.</p>
    <button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh awareness</button>
    {data && <label><input type="checkbox" checked={polling}
      onChange={event => setPolling(event.target.checked)} /> Refresh awareness every minute (local reads only)</label>}
    {query.isPending ? <p role="status">Loading portfolio awareness</p>
      : query.isError ? <p role="alert">{query.error instanceof Error ? query.error.message : "Awareness unavailable."}</p>
        : value && !value.enabled ? <p>Not configured. Select an awareness profile and existing investment/intelligence stores at dashboard launch.</p>
          : changed ? <p role="alert">Portfolio revision changed. Refresh investment records and awareness; no matching context is displayed.</p>
            : data && <>
              <p>As of {timestamp(data.generated_at)}; portfolio revision {data.portfolio_revision};
                {" "}intelligence settings {data.settings_revision}. Calendar: {label(data.calendar_status)}.
                {" "}Calendar evidence available {timestamp(data.calendar_available_at)}.</p>
              <p>Mapping observed {timestamp(data.profile_observed_at)}. Public research symbols are declared context proxies, not verified holdings or look-through.</p>
              <ul>{data.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
              <h3>Calendar watch alerts</h3>
              {!data.alerts.length && <p>No matching dated events in this snapshot. This does not establish complete coverage or absence of events.</p>}
              {data.alerts.map(item => <details key={JSON.stringify([item.source, item.event_id, item.revision])} open>
                <summary>{item.title} - {label(item.status)} / {label(item.evidence_status)}</summary>
                <p>Scheduled: {timestamp(item.scheduled_at, "Time unknown")}; evidence available: {timestamp(item.available_at)}.
                  {" "}{label(item.category)}; source revision {item.revision}.</p>
                <a href={item.source_url} target="_blank" rel="noreferrer">Source: {item.source}</a>
                <Matches matches={item.matches} />
              </details>)}
              <h3>Operator calendar watch</h3>
              <p>Manual, unverified notes may be stale; no automatic collection, reminders or source-coverage guarantee.
                Dates are source-local, not inferred UTC times. Entries are not filtered by date or current holdings.</p>
              {!data.calendar_watch?.length && <p>No operator calendar watch entries.</p>}
              <ul>{data.calendar_watch?.map(item => <li key={item.id}>
                <strong>{item.title} - {label(item.status)}</strong>; date {item.scheduled_on ?? "Unknown"};
                {" "}{item.time_note}; observed {timestamp(item.observed_at)}.
                {" "}Declared association: {item.account_id} / {item.product_id ?? "cash"}.
                {" "}Source note: {item.source}
              </li>)}</ul>
              <h3>Dated research briefs</h3>
              <p>Current means unexpired at the recorded settings and source observation, not verified truth or a live-market assessment. Expired and disabled briefs are historical context only.</p>
              {!data.briefs.length && <p>No matching briefs among the latest 20 stored briefs. Collection/AI may be disabled or evidence absent; nothing is generated by this page.</p>}
              {data.briefs.map(brief => <details key={brief.brief_id}>
                <summary>{timestamp(brief.available_at)} - {label(brief.status)} / unverified</summary>
                <p>Expires {timestamp(brief.expires_at)}; advisory action: {brief.recommendation}.
                  {" "}Public context: {brief.symbols.join(", ")}.</p>
                <Matches matches={brief.matches} />
                <h4>Source quotations</h4>
                <ul>{brief.claims.map((claim, index) => <li key={index}>{claim.text}
                  {" "}({claim.evidence_ids.join(", ")})</li>)}</ul>
                <h4>Unverified interpretation</h4><p>{brief.interpretation}</p>
                <ul>{brief.uncertainties.map((item, index) => <li key={index}>{item}</li>)}</ul>
                <ul>{brief.sources.map(source => <li key={source.evidence_id}>
                  <a href={source.source_url} target="_blank" rel="noreferrer">{source.title}</a>
                  {" "}({source.evidence_id}); published {timestamp(source.published_at, "Unknown")},
                  {" "}received {timestamp(source.received_at)}
                </li>)}</ul>
                <p>{brief.provider} / {brief.model}; prompt {brief.prompt_version}; input SHA256 {brief.input_hash}</p>
              </details>)}
              <h3>Review reminders</h3>
              <p>Operator-configured cadence only. A reminder is not a recorded review acknowledgement or investment-policy approval.</p>
              {!data.reminders.length && <p>No review reminders configured.</p>}
              <ul>{data.reminders.map(item => <li key={item.id}>
                <strong>{item.title}: {label(item.status)}</strong>; due {timestamp(item.due_at, "No review date recorded")};
                {" "}last reviewed {timestamp(item.last_reviewed_at, "Never recorded")}; every {item.interval_days} days.
                {" "}{item.evidence}
              </li>)}</ul>
              <h3>Operator thesis watch</h3>
              <p>Locally maintained notes, not verified evidence, an immutable audit trail, a recommendation or trade approval.
                Associations do not imply a current holding; entries are never sent to AI.</p>
              {!data.theses?.length && <p>No theses configured.</p>}
              {data.theses?.map(item => <details key={item.id}>
                <summary>{item.title} - {label(item.history[item.history.length - 1].status)}</summary>
                <p>Declared association: {item.account_id} / {item.product_id ?? "cash"}.</p>
                {[...item.history].reverse().map(entry => <div key={entry.observed_at}>
                  <h4>{entry.observed_at.includes(".") ? entry.observed_at : timestamp(entry.observed_at)} - {label(entry.status)}</h4>
                  <p>Thesis: {entry.thesis}</p>
                  <p>Invalidation: {entry.invalidation}</p>
                  <p>Operator evidence note: {entry.evidence}</p>
                </div>)}
              </details>)}
              <details><summary>Local mapping provenance</summary><p>Profile SHA256: {data.profile_hash}</p></details>
            </>}
  </section>;
}
