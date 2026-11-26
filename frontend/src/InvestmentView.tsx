import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import Decimal from "decimal.js";
import { RefreshCw, ShieldAlert } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type FormEvent } from "react";
import validate from "./investment-validator-v2.js";
import { evidenceAge, label, pounds, reviewTimestamp, timestamp } from "./api";
import { PortfolioAwareness } from "./PortfolioAwareness";
import { PortfolioMonitoring } from "./PortfolioMonitoring";
import { EToroPortfolioPanel } from "./EToroPortfolio";
import AccountMaintenance from "./AccountMaintenance";
import { accountDisplay } from "./portfolio-display";
import { portfolioFragmentView, portfolioView, portfolioViewLocation } from "./portfolio-navigation";
export { accountDisplay } from "./portfolio-display";
import { advanceReviewAfterSave } from "./guided-review";
import { duplicateAccountLinks, linkedGoalValue, recordedAccountValues } from "./goal-values";
import { planningSession } from "./planning-api";
import RecoveryPanel from "./RecoveryPanel";
import { confirmRecording, pendingRecording, PENDING_RECORDING_KEY, recordingClocks, recordingVersion,
  RecordingPreviewChangedError, RecordingPreviewExpiredError, RecordingRequestError, RecordingSavedStorageError, standardRecord,
  type PendingRecording } from "./recording";

export type InvestmentSnapshot = {
  schema_version: 2; source_mode: "demo" | "recorded"; portfolio_id: string;
  revision: number; generated_at: string; as_of: string | null; currency: "GBP";
  complete: boolean; equity_gbp: string | null; net_contributions_gbp: string;
  twr_pct: string | null; max_drawdown_pct: string | null;
  proposal: { status: "blocked"; reason: string; expires_at: null;
    orders: []; manual_transfers: []; execution_enabled: false };
  warnings: string[]; limitations: string[];
  baseline_as_of: string | null; since_baseline_gain_gbp: string | null;
  role_accounting_at: string | null;
  role_assignments: { id: string; account_id: string; product_id: string | null;
    role: "core" | "dynamic"; amount: string; timestamp: string; available_at: string; evidence: string }[];
  role_accounting: { role: "core" | "dynamic" | "unassigned"; cash_gbp: string | null;
    value_gbp: string | null; accounts: { account_id: string; cash_gbp: string }[];
    holdings: { account_id: string; product_id: string; quantity: string }[] }[];
  lifetime_gain_gbp: null;
  recorded_fees_gbp: string; recorded_interest_gbp: string; recorded_dividends_gbp: string;
  recorded_income_gbp: string;
  recorded_fee_events: { id: string; type: "buy" | "sell"; account_id: string;
    product_id: string; role: string; quantity: string; price_gbp: string; fee_gbp: string;
    timestamp: string; available_at: string; evidence: string }[];
  recorded_account_fees: { id: string; type: "account_fee"; account_id: string;
    role: string; amount_gbp: string; timestamp: string; available_at: string; evidence: string }[];
  recorded_income_events: { id: string; type: "interest" | "dividend"; account_id: string;
    product_id: string | null; amount_gbp: string; timestamp: string;
    role: string; role_evidence: string | null; available_at: string; evidence: string }[];
  recorded_flow_events: { id: string; type: "contribution" | "withdrawal" | "transfer";
    account_id: string; destination_account_id: string | null; amount_gbp: string;
    role: string; timestamp: string; available_at: string; evidence: string }[];
  recorded_corrections: { id: string; original_event_id: string; replacement_event_id: string;
    replaced_type: "contribution" | "withdrawal" | "transfer" | "interest" | "dividend" |
      "buy" | "sell" | "account_fee" | "split" | "mark";
    account_id: string | null; role: string; role_evidence: string | null;
    original_amount_gbp: string | null; replacement_amount_gbp: string | null; timestamp: string;
    available_at: string; evidence: string }[];
  recorded_profile_changes: { id: string; action: "add_product" | "rename" | "retire" | "scope_addition";
    target: "account" | "product"; entity_id: string; name: string | null;
    timestamp: string; available_at: string; evidence: string }[];
  recorded_scope_additions_gbp: string;
  recorded_scope_additions: { id: string; account_id: string; amount_gbp: string;
    timestamp: string; available_at: string; evidence: string }[];
  cash_observations: { account_id: string; balance_gbp: string; difference_gbp: string;
    timestamp: string; available_at: string; source: string; evidence: string }[];
  valuation_history: {
    timestamp: string; source_available_at: string; as_of: string | null; complete: boolean;
    equity_gbp: string | null; capital_basis_gbp: string | null;
    net_contributions_gbp: string | null; gain_since_baseline_gbp: string | null;
    twr_pct: string | null; max_drawdown_pct: string | null; gap_reason: string | null;
  }[];
  accounts: { id: string; name: string; kind: "broker" | "deposit"; status: "active" | "retired"; cash_gbp: string | null;
    cash_observation: { balance_gbp: string; difference_gbp: string; timestamp: string;
      available_at: string; source: string; evidence: string } | null;
    terms: { annual_rate_pct: string | null; rate_basis: "AER" | "gross" | null;
      effective_from: string; observed_at: string; available_at: string; source: string;
      maturity_date: string | null; notice_days: number | null } | null }[];
  holdings: { account_id: string; product_id: string; name: string; sleeve: "equity" | "bond";
    quantity: string; value_gbp: string | null; mark_at: string | null; mark_available_at: string | null }[];
};

type OpeningSnapshot = InvestmentSnapshot;

export async function loadInvestment(signal: AbortSignal): Promise<InvestmentSnapshot> {
  const response = await fetch("/api/investment/v1/snapshot", {
    signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store",
  });
  const body: unknown = await response.json();
  if (!response.ok) throw new Error("Investment records are unavailable. Select a valid investment store or synthetic demo.");
  if (!validate(body)) throw new Error("Investment response failed validation.");
  return body as InvestmentSnapshot;
}

function percent(value: string | null) { return value === null ? "Unavailable" : `${new Decimal(value).toFixed(2)}%`; }

function accountReference(data: OpeningSnapshot, id: string | null) {
  if (id === null) return "Not applicable";
  const account = data.accounts.find(item => item.id === id);
  return account ? `${account.name} (${id})` : id;
}

function productReference(data: OpeningSnapshot, id: string | null) {
  if (id === null) return "GBP cash";
  const held = data.holdings.find(item => item.product_id === id);
  const profileName = [...data.recorded_profile_changes].reverse().find(item =>
    item.target === "product" && item.entity_id === id && item.name !== null)?.name;
  const name = held?.name ?? profileName;
  return name ? `${name} (${id})` : id;
}

function CashEvidenceOverview({ data, now }: { data: OpeningSnapshot; now: number }) {
  const differences = useMemo(() => {
    const result = new Map<string, { count: number; first: OpeningSnapshot["cash_observations"][number];
      latest: OpeningSnapshot["cash_observations"][number] }>();
    for (const observation of data.cash_observations) {
      if (new Decimal(observation.difference_gbp).isZero()) continue;
      const prior = result.get(observation.account_id);
      if (prior) {
        prior.count++;
        prior.latest = observation;
      } else {
        result.set(observation.account_id, { count: 1, first: observation, latest: observation });
      }
    }
    return result;
  }, [data.cash_observations]);
  const correctionWarning = data.warnings.find(warning =>
    warning.startsWith("Historical correction replay may have changed past cash-observation differences."));
  return <section className="history-group" aria-label="Cash evidence overview">
    <h2>Cash evidence overview</h2>
    <p className="position-note">Counts and differences below reflect the current replay of recorded observations. Later matching observations do not resolve differences in this replay. Absence of a difference does not establish a current balance.</p>
    {correctionWarning && <p className="position-note">{correctionWarning}</p>}
    <dl className="health-facts">{data.accounts.map(account => {
      const history = differences.get(account.id);
      const firstDifference = history?.first;
      const latestDifference = history?.latest;
      const latest = account.cash_observation;
      return <div key={account.id}><dt>{account.name} ({account.id})</dt><dd>
        {latest === null ? "No cash observation" : <>
          Latest observed {timestamp(latest.timestamp)} (age: {evidenceAge(latest.timestamp, now)});
          available {timestamp(latest.available_at)}; difference {pounds(latest.difference_gbp, true)}.
        </>}
        {" "}{history?.count ?? 0} differing {history?.count === 1 ? "observation" : "observations"} in current replayed history.
        {firstDifference && firstDifference !== latestDifference && <> Earliest difference: {pounds(firstDifference.difference_gbp, true)} at {timestamp(firstDifference.timestamp)};
          source {firstDifference.source}; evidence {firstDifference.evidence}.</>}
        {latestDifference && <> Latest difference: {pounds(latestDifference.difference_gbp, true)} at {timestamp(latestDifference.timestamp)};
          source {latestDifference.source}; evidence {latestDifference.evidence}.</>}
      </dd></div>;
    })}</dl>
  </section>;
}

function InvestmentNextSteps({ data }: { data: OpeningSnapshot }) {
  const unmarked = data.holdings.filter(holding => holding.mark_at === null);
  const hasDifferences = data.cash_observations.some(observation =>
    !new Decimal(observation.difference_gbp).isZero());
  const correctionWarnings = data.warnings.filter(warning =>
    warning.startsWith("Historical corrections preserve original evidence"));
  const correctionReplayWarnings = data.warnings.filter(warning =>
    warning.startsWith("Historical correction replay may have changed past cash-observation differences."));
  const cashWarnings = data.warnings.filter(warning =>
    warning.startsWith("Recorded cash observation") || /^\d+ recorded cash observations/.test(warning));
  const otherWarnings = data.warnings.filter(warning =>
    !(warning === "A complete common-clock valuation is unavailable." && !data.complete)
    && !(data.recorded_corrections.length > 0 && correctionWarnings.includes(warning))
    && !correctionReplayWarnings.includes(warning)
    && !(hasDifferences && cashWarnings.includes(warning)));
  const needsAttention = !data.complete || hasDifferences ||
    correctionReplayWarnings.length > 0 || otherWarnings.length > 0;
  const priceAction = (holding: OpeningSnapshot["holdings"][number]) => data.source_mode === "recorded"
    ? <NavLink aria-label={`Add a checked price for ${holding.name} in ${accountDisplay(data, holding.account_id)}`}
      to={`/investment/record?record=missing-price&account_id=${encodeURIComponent(holding.account_id)}&product_id=${encodeURIComponent(holding.product_id)}`}>
      add a checked price
    </NavLink>
    : <NavLink to="/investment-guide#recording-prices">how to add a checked price</NavLink>;
  return <>
  <section className="history-group investment-next-steps" aria-label="Portfolio evidence to review">
    <h2>Portfolio evidence to review</h2>
    {!needsAttention && <p>No missing total or recorded cash differences are flagged. This does not verify your accounts.</p>}
    {needsAttention && <ul>
      {!data.complete && <li>
        {unmarked.length > 0
          ? <>{unmarked.length} {unmarked.length === 1 ? "holding has" : "holdings have"} no recorded price: {unmarked.slice(0, 3).map((holding, index) =>
              <span key={`${holding.account_id}:${holding.product_id}`}>
                {index > 0 ? "; " : ""}{holding.name}
                {" "}({accountDisplay(data, holding.account_id)}):{" "}
                {priceAction(holding)}
              </span>)}{unmarked.length > 3 ? ` and ${unmarked.length - 3} more` : ""}.
              {unmarked.length > 3 && <details className="help-disclosure">
                <summary>Show {unmarked.length - 3} more missing prices</summary>
                <ul>{unmarked.slice(3).map(holding => <li key={`${holding.account_id}:${holding.product_id}`}>
                  {holding.name} ({accountDisplay(data, holding.account_id)}): {priceAction(holding)}
                </li>)}</ul>
              </details>}
              {" "}<NavLink to="/investment?view=history#investment-holdings">Review holdings</NavLink>.
              <p>Use a source that supports the price and both dates. Later information cannot fill an earlier valuation.</p></>
          : "No complete total is available for this date. Missing is not zero; check the dates and source. Later information cannot fill an earlier gap."}
      </li>}
      {(hasDifferences || correctionReplayWarnings.length > 0) && <li>
        {hasDifferences && <>Recorded cash observations differ from the journal; they do not change balances or show the current one. </>}
        {correctionReplayWarnings.length > 0 && <>A correction may have changed earlier reported cash differences.
          Compare prior reports with the original observations and correction evidence. </>}
        {" "}<NavLink to="/investment?view=history#investment-accounts">Review cash evidence</NavLink>.
        {(cashWarnings.length > 0 || correctionReplayWarnings.length > 0) && <details className="help-disclosure">
          <summary>Cash evidence details</summary>
          <ul>{[...cashWarnings, ...correctionReplayWarnings].map(warning => <li key={warning}>{warning}</li>)}</ul>
        </details>}
      </li>}
      {otherWarnings.map(warning => <li key={warning}>{warning}</li>)}
    </ul>}
  </section>
  </>;
}

function PerformanceLimitations({ data, compact = false }: {
  data: OpeningSnapshot; compact?: boolean;
}) {
  const limited = data.recorded_corrections.length > 0 ||
    data.baseline_as_of === null || data.twr_pct === null || data.max_drawdown_pct === null;
  if (!limited) return null;
  if (compact) return <section className="history-group performance-limit-note"
    aria-label="Historical performance note">
    <p className="position-note">{data.recorded_corrections.length > 0
      ? <>A correction made historical performance unavailable; no new starting point was created.
        {" "}<NavLink to="/investment?view=history#investment-corrections">Review correction history</NavLink>.
        {" "}<NavLink to="/investment?view=history#investment-performance">
          Why performance history may be incomplete
        </NavLink>.</>
      : <>{data.baseline_as_of === null
        ? "Performance needs a complete starting value. Later information cannot fill past gaps."
        : "Some historical performance figures are unavailable."}
        {" "}<NavLink to="/investment?view=history#investment-performance">
          Why performance history may be incomplete
        </NavLink>.</>}
    </p>
  </section>;
  const correctionWarnings = data.warnings.filter(warning =>
    warning.startsWith("Historical corrections preserve original evidence"));
  return <section className="history-group" aria-label="Historical performance limitations">
    <h2>About past performance</h2>
    {data.recorded_corrections.length > 0
      ? <><p>A correction made past performance unavailable; no new starting point was created.
        {" "}<NavLink to="/investment?view=history#investment-corrections">Review correction history</NavLink>.</p>
        {correctionWarnings.length > 0 && <details className="help-disclosure">
          <summary>Correction details</summary>
          <ul>{correctionWarnings.map(warning => <li key={warning}>{warning}</li>)}</ul>
        </details>}</>
      : data.baseline_as_of === null
      ? <p>Performance has no complete starting value. Later information cannot recreate missing past returns.
        {" "}<NavLink to="/investment-guide#performance-gaps">Learn more</NavLink>.</p>
      : <p>Some performance figures are unavailable; complete totals do not restore past history.
        {" "}<NavLink to="/investment?view=history#investment-performance">View performance history</NavLink>.</p>}
    <p>You can continue to record and review your portfolio without those historical figures.</p>
  </section>;
}

function AccountAndHoldingsSummary({ data, now }: { data: OpeningSnapshot; now: number }) {
  const concentration = useMemo(() => {
    if (!data.complete || data.equity_gbp === null ||
        data.holdings.some(item => item.value_gbp === null)) return null;
    const portfolioValue = new Decimal(data.equity_gbp);
    if (portfolioValue.lte(0)) return null;
    const byProduct = new Map<string, { name: string; value: Decimal }>();
    for (const holding of data.holdings) {
      const current = byProduct.get(holding.product_id);
      const value = new Decimal(holding.value_gbp!);
      byProduct.set(holding.product_id, {
        name: holding.name,
        value: (current?.value ?? new Decimal(0)).plus(value),
      });
    }
    return [...byProduct.entries()].map(([id, item]) => ({ id, ...item }))
      .sort((left, right) => right.value.comparedTo(left.value));
  }, [data]);

  return <section className="history-group portfolio-summary" id="investment-account-summary"
    aria-label={data.source_mode === "recorded" ? "Recorded investments" : "Accounts and investments"}>
    <h2>{data.source_mode === "recorded" ? "Investments you hold" : "Your accounts and investments"}</h2>
    <p className="position-note">Latest journal entries, not live balances; dates may differ from the valuation.</p>
    {data.source_mode === "demo" && <>
    <p className="position-note">Cash checks are separate observations, not balance updates. Neither a check nor a recorded price verifies current holdings or custody.</p>
    <h3>Accounts and cash</h3>
    <dl className="health-facts">
      {data.accounts.map(account => <div key={account.id}>
        <dt>{accountDisplay(data, account.id)}{account.status === "retired" ? " · no longer included" : ""}</dt>
        <dd>{label(account.kind)} · {pounds(account.cash_gbp)} recorded cash
          <br />{account.cash_observation === null ? "No cash check recorded"
            : <>Last cash check: {timestamp(account.cash_observation.timestamp)} (age: {evidenceAge(account.cash_observation.timestamp, now)});
              {" "}difference at that time: {pounds(account.cash_observation.difference_gbp, true)}</>}
          {account.terms !== null && <>
            <br />Reported savings terms: {account.terms.annual_rate_pct === null
              ? "Rate unavailable"
              : `${percent(account.terms.annual_rate_pct)} ${account.terms.rate_basis ?? "basis unavailable"}`}
            {" · "}Effective {account.terms.effective_from}
            {account.terms.maturity_date !== null && <> · Maturity {account.terms.maturity_date}</>}
            {account.terms.notice_days !== null && <> · Notice {account.terms.notice_days} days</>}
            <details className="help-disclosure">
              <summary>Term source and dates</summary>
              <p>Observed {account.terms.observed_at}; available {account.terms.available_at} (UTC).
                {" "}Source: {account.terms.source}.</p>
              <p>These are dated reported terms, not current provider verification, an accrued return or a guarantee.</p>
            </details>
          </>}
          {data.source_mode === "recorded" && account.status === "active" && <>
            <br /><NavLink className="account-update-link" to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>
              Update {accountDisplay(data, account.id)}
            </NavLink>
            {" · "}<NavLink to={`/investment/record?record=cash-check&account_id=${encodeURIComponent(account.id)}`}>
              Check cash for {accountDisplay(data, account.id)}
            </NavLink>
          </>}
        </dd>
      </div>)}
    </dl>
    </>}
    <h3 id="investment-holdings">{data.source_mode === "recorded" ? "Recorded holdings" : "Investments you hold"}</h3>
    {data.holdings.length === 0 ? <p>No investment holdings are recorded.</p>
      : <div className="table-scroll" tabIndex={0} aria-label="Recorded investment holdings">
        <table>
          <thead><tr><th>Investment</th><th>Account</th><th>Units</th><th>Recorded value</th><th>Price date</th></tr></thead>
          <tbody>{data.holdings.map(holding => <tr key={`${holding.account_id}:${holding.product_id}`}>
            <td>{holding.name}</td>
            <td>{accountDisplay(data, holding.account_id)}</td>
            <td>{holding.quantity}</td>
            <td>{pounds(holding.value_gbp)}</td>
            <td>{holding.mark_at === null ? "No recorded price" : <>{timestamp(holding.mark_at)}
              {" "}(age: {evidenceAge(holding.mark_at, now)})</>}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    {data.holdings.length > 0 && <details className="help-disclosure">
      <summary>Price dates and source availability</summary>
      <p>These are the dates for each holding's latest recorded price. Missing is not zero.</p>
      <div className="table-scroll"><table>
        <thead><tr><th>Investment</th><th>Account</th><th>Price applies at</th><th>Source available from</th></tr></thead>
        <tbody>{data.holdings.map(holding => <tr key={`${holding.account_id}:${holding.product_id}`}>
          <td>{holding.name}</td>
          <td>{accountDisplay(data, holding.account_id)}</td>
          <td>{holding.mark_at === null ? "No recorded price" : holding.mark_at}</td>
          <td>{holding.mark_available_at === null ? "Not available" : holding.mark_available_at}</td>
        </tr>)}</tbody>
      </table></div>
    </details>}
    {data.holdings.length > 0 && <section className="history-group" aria-label="Direct investment concentration">
      <h3>Largest direct investments</h3>
      {!concentration || data.equity_gbp === null
        ? <p>A concentration percentage is unavailable until a complete recorded portfolio value is available.</p>
        : <>
          <p className="position-note">Share of the complete recorded portfolio value dated {timestamp(data.as_of)}.
            Investments held across accounts are combined by their journal identity.</p>
          <ul>{concentration.slice(0, 5).map(item =>
            <li key={item.id}>{item.name} ({item.id}): {pounds(item.value.toString())}
              {" "}· {item.value.div(data.equity_gbp!).mul(100).toFixed(1)}% of portfolio value</li>)}
            {concentration.length > 5 && (() => {
              const otherValue = concentration.slice(5).reduce(
                (total, item) => total.plus(item.value), new Decimal(0));
              return <li>Other direct investments: {pounds(otherValue.toString())}
                {" "}· {otherValue.div(data.equity_gbp!).mul(100).toFixed(1)}%</li>;
            })()}
          </ul>
          <p className="position-note">This measures only the named holdings recorded directly. It does not look through funds to their underlying assets and is not a diversification, suitability or risk assessment.</p>
        </>}
    </section>}
  </section>;
}

function microseconds(value: string) {
  const fraction = value.match(/\.(\d{1,6})Z$/)?.[1] ?? "";
  return BigInt(Date.parse(value)) * 1000n + BigInt(fraction.padEnd(6, "0").slice(3));
}

const ChangeDecimal = Decimal.clone({ precision: 500 });

export type ValueChangeSelection = { portfolioId: string; sourceMode: OpeningSnapshot["source_mode"];
  timestamp: string; fromLatest: number };

function RecordedValueChanges({ data, selection, onSelect, checkpointTimestamp }: {
  data: OpeningSnapshot; selection: ValueChangeSelection | null;
  onSelect: (selection: ValueChangeSelection) => void;
  checkpointTimestamp: string | null;
}) {
  const selected = selection?.portfolioId === data.portfolio_id && selection.sourceMode === data.source_mode
    ? selection : null;
  const history = data.valuation_history;
  const ordered = history.every((point, index) =>
    index === 0 || microseconds(history[index - 1].timestamp) < microseconds(point.timestamp));
  const end = history[history.length - 1];
  const usable = (point: OpeningSnapshot["valuation_history"][number]) =>
    point.complete && point.equity_gbp !== null && point.net_contributions_gbp !== null &&
    point.capital_basis_gbp !== null && point.as_of === point.timestamp &&
    microseconds(point.source_available_at) <= microseconds(point.timestamp);
  const candidates = ordered ? history.slice(0, -1).filter(usable) : [];
  const start = selected
    ? data.source_mode === "demo" ? candidates[candidates.length - selected.fromLatest]
      : candidates.find(point => point.timestamp === selected.timestamp)
    : checkpointTimestamp
      ? candidates.find(point => point.timestamp === checkpointTimestamp)
      : candidates[candidates.length - 1];
  const recentCandidates = candidates.slice(-100);
  const shownCandidates = start && !recentCandidates.includes(start)
    ? [start, ...recentCandidates] : recentCandidates;
  const usingCheckpoint = !selected && checkpointTimestamp !== null;

  let unavailable: string | null = null;
  if (data.recorded_corrections.length) {
    unavailable = "A historical correction changed replayed history. This comparison is unavailable; review the original evidence and correction history instead.";
  } else if (!ordered) {
    unavailable = "The valuation history is not in a comparable order.";
  } else if (!end || !usable(end) || !data.complete || data.as_of !== end.as_of ||
    data.equity_gbp === null || !new ChangeDecimal(data.equity_gbp).eq(end.equity_gbp!)) {
    unavailable = "The latest recorded portfolio value is incomplete or does not match the latest history point. A comparison cannot be calculated.";
  } else if (!start) {
    unavailable = selected ? "The selected earlier valuation is no longer available. Choose another date."
      : checkpointTimestamp
        ? checkpointTimestamp === end.timestamp
          ? `No newer complete recorded value is available yet. This does not mean your accounts were unchanged. Return after updating your records${shownCandidates.length > 0 ? ", or choose an earlier value below" : ""}.`
          : "The saved review valuation is no longer available. Review the journal history before choosing another date."
        : "At least two complete recorded valuations are needed. A later source cannot fill an earlier gap.";
  }

  const within = (value: { timestamp: string }) => start && end &&
    microseconds(value.timestamp) > microseconds(start.timestamp) &&
    microseconds(value.timestamp) <= microseconds(end.timestamp);
  const flows = data.recorded_flow_events.filter(within);
  const income = data.recorded_income_events.filter(within);
  const fees = data.recorded_fee_events.filter(within);
  const accountFees = data.recorded_account_fees.filter(within);
  const scope = data.recorded_scope_additions.filter(within);
  const records = [
    ...flows.map(item => ({ ...item, description: item.type === "transfer"
      ? `Transfer ${pounds(item.amount_gbp, true)} from ${accountDisplay(data, item.account_id)} to ${item.destination_account_id === null ? "unknown account" : accountDisplay(data, item.destination_account_id)} (not an external flow)`
      : `${item.type === "contribution" ? "Money added" : "Money withdrawn"}: ${pounds(item.amount_gbp, true)} · ${accountDisplay(data, item.account_id)}` })),
    ...income.map(item => ({ ...item, description: `${item.type === "interest" ? "Interest" : "Dividend"} received: ${pounds(item.amount_gbp, true)} · ${accountDisplay(data, item.account_id)}` })),
    ...fees.map(item => ({ ...item, description: `Recorded ${item.type === "buy" ? "purchase" : "sale"} fee: ${pounds(item.fee_gbp, true)} · ${productReference(data, item.product_id)} · ${accountDisplay(data, item.account_id)}` })),
    ...accountFees.map(item => ({ ...item, description: `Recorded account/platform charge: ${pounds(item.amount_gbp, true)} · ${accountDisplay(data, item.account_id)}` })),
    ...scope.map(item => ({ ...item, description: `Account added to tracked scope: ${pounds(item.amount_gbp, true)} · ${accountDisplay(data, item.account_id)}` })),
  ].sort((a, b) => microseconds(a.timestamp) < microseconds(b.timestamp) ? -1
    : microseconds(a.timestamp) > microseconds(b.timestamp) ? 1 : 0);
  const sum = (values: string[]) => values.reduce((total, value) => total.plus(value), new ChangeDecimal(0));
  const added = sum(flows.filter(item => item.type === "contribution").map(item => item.amount_gbp));
  const withdrawn = sum(flows.filter(item => item.type === "withdrawal").map(item => item.amount_gbp));
  const received = sum(income.map(item => item.amount_gbp));
  const paidFees = sum([
    ...fees.map(item => item.fee_gbp),
    ...accountFees.map(item => item.amount_gbp),
  ]);
  const scopeAdded = sum(scope.map(item => item.amount_gbp));
  const net = added.minus(withdrawn);
  if (!unavailable && start && end &&
      (records.some(item => microseconds(item.available_at) > microseconds(end.timestamp)) ||
       !new ChangeDecimal(end.net_contributions_gbp!).minus(start.net_contributions_gbp!).eq(net) ||
       !new ChangeDecimal(end.capital_basis_gbp!).minus(start.capital_basis_gbp!).eq(net.plus(scopeAdded)))) {
    unavailable = "The recorded activity and valuation evidence do not agree for these dates. Review the source history before relying on a comparison.";
  }
  const change = start && end && !unavailable
    ? new ChangeDecimal(end.equity_gbp!).minus(start.equity_gbp!) : null;
  const remainder = change?.minus(net).minus(scopeAdded).minus(received).plus(paidFees);
  const gaps = start && end ? history.filter(point =>
    microseconds(point.timestamp) > microseconds(start.timestamp) &&
    microseconds(point.timestamp) < microseconds(end.timestamp) && !point.complete).length : 0;

  return <section className="history-group value-changes"
    aria-label={usingCheckpoint ? "What changed since your saved value?" : "Changes between recorded values"}>
    <h2>{usingCheckpoint ? "What changed since your saved value?" : "What changed between recorded values?"}</h2>
    <p className="position-note">Compare dated journal valuations, not live account balances.
      The comparison uses recorded evidence only and cannot establish that all transactions or prices are present.</p>
    {usingCheckpoint && <p className="position-note">The saved point is a date, not a frozen value.
      Later evidence effective at that time can change the current replay; compare any retained earlier report.</p>}
    {!usingCheckpoint && <p className="position-note">Selecting an earlier value does not save it as your comparison point.</p>}
    {shownCandidates.length > 0 && !data.recorded_corrections.length && <label className="value-change-select">
      Earlier recorded valuation
      <select value={start?.timestamp ?? selected?.timestamp ?? (checkpointTimestamp ? "" : shownCandidates[shownCandidates.length - 1].timestamp)}
        onChange={event => {
          const index = candidates.findIndex(point => point.timestamp === event.target.value);
          if (index >= 0) onSelect({ portfolioId: data.portfolio_id, sourceMode: data.source_mode,
            timestamp: event.target.value, fromLatest: candidates.length - index });
        }}>
        {!selected && !start && checkpointTimestamp && <option value="">Choose an earlier value</option>}
        {selected && !start && <option value={selected.timestamp}>Previously selected date unavailable</option>}
        {shownCandidates.map(point => <option key={point.timestamp} value={point.timestamp}>
          {timestamp(point.timestamp)} · {pounds(point.equity_gbp, true)}
        </option>)}
      </select>
    </label>}
    {data.source_mode === "demo" && shownCandidates.length > 0 &&
      <p className="position-note">Demo dates move on refresh; a selected earlier point stays in the same position in the synthetic history.</p>}
    {candidates.length > 100 && <p>Showing the most recent 100 earlier complete valuations and your selected date if older.</p>}
    {unavailable ? <p role="status">{unavailable}</p> : start && end && change && remainder && <>
      <p>From {timestamp(start.timestamp)} ({pounds(start.equity_gbp, true)}) to {timestamp(end.timestamp)} ({pounds(end.equity_gbp, true)}), the recorded value changed by <strong>{pounds(change.toString(), true)}</strong>.</p>
      {gaps > 0 && <p className="account-attention">There {gaps === 1 ? "is" : "are"} {gaps} incomplete valuation {gaps === 1 ? "point" : "points"} between these dates. The endpoint comparison does not reconstruct the missing history.</p>}
      <dl className="health-facts">
        <div><dt>Money added from outside</dt><dd>{pounds(added.toString(), true)}</dd></div>
        <div><dt>Money withdrawn to outside</dt><dd>{pounds(withdrawn.toString(), true)}</dd></div>
        <div><dt>Value added by including accounts</dt><dd>{pounds(scopeAdded.toString(), true)}</dd></div>
        <div><dt>Recorded interest and dividends</dt><dd>{pounds(received.toString(), true)}</dd></div>
        <div><dt>Recorded trade and account charges</dt><dd>{pounds(paidFees.toString(), true)}</dd></div>
        <div><dt>Unexplained remainder</dt><dd>{pounds(remainder.toString(), true)}</dd></div>
      </dl>
      <p className="position-note">The remainder is the value change after adding withdrawals and recorded charges and subtracting deposits, newly included account value and recorded income. It can include price movements, missing records or other differences; it is not a verified investment return. Charges and income are only those explicitly recorded. Transfers between tracked accounts do not change the portfolio total.</p>
      <details className="help-disclosure"><summary>Supporting recorded activity ({records.length})</summary>
        <p>Records effective after the earlier valuation and through the later valuation, inclusive. Same-time records are not shown in journal order.</p>
        {records.length === 0 ? <p>No activity in these categories is recorded for these dates; that does not prove none occurred.</p>
          : <ul>{records.slice(0, 100).map(item => <li key={item.id}>
            {item.description} · effective {item.timestamp}; available {item.available_at}. {item.evidence}
          </li>)}</ul>}
        {records.length > 100 && <p>Only the first 100 records are listed here.</p>}
        <NavLink to="/investment?view=history#investment-records">View record history</NavLink>
        {" · "}<NavLink to="/investment?view=history#investment-performance">View valuation history</NavLink>
      </details>
    </>}
  </section>;
}

function RecentActivity({ data, compact = false }: { data: OpeningSnapshot; compact?: boolean }) {
  const { activity, truncated } = useMemo(() => {
    const sorted = [
    ...data.recorded_flow_events.map(event => ({
      id: event.id, timestamp: event.timestamp, clockLabel: "Effective at",
      available: event.available_at, evidence: event.evidence,
      description: event.type === "transfer"
        ? `Transfer ${pounds(event.amount_gbp, true)} from ${accountDisplay(data, event.account_id)} to ${event.destination_account_id === null ? "unknown account" : accountDisplay(data, event.destination_account_id)}`
        : `${event.type === "contribution" ? "Money added" : "Money withdrawn"}: ${pounds(event.amount_gbp, true)} · ${accountDisplay(data, event.account_id)}`,
    })),
    ...data.recorded_income_events.map(event => ({
      id: event.id, timestamp: event.timestamp, clockLabel: "Effective at",
      available: event.available_at, evidence: event.evidence,
      description: `${event.type === "interest" ? "Interest" : "Dividend"} received: ${pounds(event.amount_gbp, true)} · ${accountDisplay(data, event.account_id)}`,
    })),
    ...data.recorded_fee_events.map(event => ({
      id: event.id, timestamp: event.timestamp, clockLabel: "Effective at",
      available: event.available_at, evidence: event.evidence,
      description: `${event.type === "buy" ? "Purchase" : "Sale"}: ${event.quantity} units of ${productReference(data, event.product_id)} · ${accountDisplay(data, event.account_id)} · recorded fee ${pounds(event.fee_gbp, true)}`,
    })),
    ...data.recorded_account_fees.map(event => ({
      id: event.id, timestamp: event.timestamp, clockLabel: "Effective at",
      available: event.available_at, evidence: event.evidence,
      description: `Account/platform charge: ${pounds(event.amount_gbp, true)} · ${accountDisplay(data, event.account_id)}`,
    })),
    ...data.recorded_corrections.map(event => ({
      id: event.id, timestamp: event.timestamp, clockLabel: "Correction recorded at",
      available: event.available_at, evidence: event.evidence,
      description: `Correction recorded for ${label(event.replaced_type)}${event.account_id === null ? ""
        : ` · ${accountDisplay(data, event.account_id)}`}`,
    })),
    ].sort((a, b) => {
      const left = microseconds(a.timestamp), right = microseconds(b.timestamp);
      return left === right ? 0 : left > right ? -1 : 1;
    });
    const cutoff = sorted.length > 5 ? microseconds(sorted[4].timestamp) : null;
    const selected = cutoff === null ? sorted : sorted.filter(item => microseconds(item.timestamp) >= cutoff);
    return { activity: selected.slice(0, 100), truncated: selected.length > 100 };
  }, [data]);
  return <section className="history-group" aria-label="Recent recorded activity">
    <h2>Recent recorded activity</h2>
    <p className="position-note">{compact
      ? "Up to three recorded money movements, income, purchases or sales, and corrections. Same-time entries may be omitted and are not in journal order; prices and other records are not listed."
      : "Latest dated money movements, income, purchases or sales, and corrections recorded in the journal. Same-time entries are not shown in journal order. This is not a complete explanation of changes in value; prices and other records are not listed here."}</p>
    {truncated && !compact && <p>More entries share the last displayed time; only the first 100 are shown here. See record history.</p>}
    {activity.length === 0 ? <p>No such activity has been recorded since setup.</p>
      : <ul>{(compact ? activity.slice(0, 3) : activity).map(item => <li key={item.id}>
        {item.description} · {timestamp(item.timestamp)}
        {!compact && <details className="help-disclosure"><summary>Source details</summary>
          <p>{item.clockLabel} {item.timestamp}; available at {item.available}. {item.evidence}</p>
        </details>}
      </li>)}</ul>}
    <NavLink to="/investment?view=history#investment-records">View record history</NavLink>
  </section>;
}

const ChartDecimal = Decimal.clone({ precision: 300 });

function intervalReturn(current: OpeningSnapshot["valuation_history"][number],
  previous: OpeningSnapshot["valuation_history"][number] | undefined) {
  if (!previous || !previous.complete || previous.twr_pct === null ||
      !current.complete || current.twr_pct === null) return null;
  const start = new ChartDecimal(previous.twr_pct).plus(100);
  const end = new ChartDecimal(current.twr_pct).plus(100);
  if (start.lte(0) || end.lt(0)) return null;
  return end.minus(start).div(start).mul(100).toString();
}

function InvestmentGrowthHistory({ history }: { history: OpeningSnapshot["valuation_history"] }) {
  const returnMaskId = `investment-return-gap-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const chartHistory = history.slice(-500);
  const chartKnown = chartHistory.filter(point =>
    point.complete && point.equity_gbp !== null && point.capital_basis_gbp !== null);
  const values = chartKnown.flatMap(point =>
    [new Decimal(point.equity_gbp!), new Decimal(point.capital_basis_gbp!)]);
  const low = values.length
    ? values.slice(1).reduce((value, candidate) => Decimal.min(value, candidate), values[0])
    : new Decimal(0);
  const high = values.length
    ? values.slice(1).reduce((value, candidate) => Decimal.max(value, candidate), values[0])
    : new Decimal(0);
  const spread = high.minus(low);
  const padding = spread.isZero() ? Decimal.max(high.abs().mul("0.05"), new Decimal(1)) : spread.mul("0.1");
  const minimum = low.minus(padding), maximum = high.plus(padding);
  const range = maximum.minus(minimum);
  const firstTime = chartHistory.length ? microseconds(chartHistory[0].timestamp) : 0n;
  const lastTime = chartHistory.length ? microseconds(chartHistory[chartHistory.length - 1].timestamp) : firstTime;
  const timeRange = lastTime > firstTime ? lastTime - firstTime : 1n;
  const left = 72, right = 780, top = 20, bottom = 220;
  const x = (point: OpeningSnapshot["valuation_history"][number]) =>
    left + Number(microseconds(point.timestamp) - firstTime) / Number(timeRange) * (right - left);
  const y = (value: string) =>
    top + new Decimal(maximum.minus(value)).div(range).toNumber() * (bottom - top);
  const returnKnown = chartHistory.filter(point => point.complete && point.twr_pct !== null);
  const returnValues = returnKnown.map(point => new ChartDecimal(point.twr_pct!));
  const returnLow = returnValues.length
    ? returnValues.slice(1).reduce((value, candidate) => ChartDecimal.min(value, candidate), returnValues[0])
    : new ChartDecimal(0);
  const returnHigh = returnValues.length
    ? returnValues.slice(1).reduce((value, candidate) => ChartDecimal.max(value, candidate), returnValues[0])
    : new ChartDecimal(0);
  const returnSpread = returnHigh.minus(returnLow);
  const returnPadding = returnSpread.isZero()
    ? ChartDecimal.max(returnHigh.abs().mul("0.05"), new ChartDecimal(1))
    : returnSpread.mul("0.1");
  const returnMinimum = returnLow.minus(returnPadding), returnMaximum = returnHigh.plus(returnPadding);
  const returnRange = returnMaximum.minus(returnMinimum);
  const returnY = (value: string) =>
    top + returnMaximum.minus(value).div(returnRange).toNumber() * (bottom - top);
  const lineSegments = (field: "equity_gbp" | "capital_basis_gbp" | "twr_pct",
    coordinate: (value: string) => number = y) => {
    const segments: string[][] = [];
    let segment: string[] = [];
    for (const point of chartHistory) {
      const value = point.complete ? point[field] : null;
      if (value === null) {
        if (segment.length) segments.push(segment);
        segment = [];
      } else {
        segment.push(`${x(point)},${coordinate(value)}`);
      }
    }
    if (segment.length) segments.push(segment);
    return segments;
  };
  const tableStart = Math.max(0, history.length - 20);
  const latest = history.slice(tableStart).map((point, index) => {
    const previous = history[tableStart + index - 1];
    return { point, previous, interval: intervalReturn(point, previous) };
  }).reverse();
  const dateLabel = (value: string) => new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium", timeZone: "UTC",
  }).format(Date.parse(value));

  return <section className="history-group">
    <h2>Recorded growth history</h2>
    <p className="position-note">These are recorded values, not live prices. Lines join recorded points; missing information stays as a gap.</p>
    {history.length === 0
      ? <p>No valuation-relevant events are recorded yet.</p>
      : <>
        {chartKnown.length > 0 ? <figure className="history-chart">
          <figcaption>Recorded value and capital added <span>{chartKnown.length} complete of {chartHistory.length} recent record times</span></figcaption>
          <p className="position-note">Capital added means the starting value plus net deposits, withdrawals and included-account additions; it excludes recorded gains or losses.</p>
          <svg viewBox="0 0 800 260" role="img" aria-label="Recorded portfolio value and capital added; incomplete valuation times are gaps">
            {[0, 1, 2, 3].map(index => {
              const value = maximum.minus(range.mul(index).div(3));
              const yAt = top + index / 3 * (bottom - top);
              return <g key={index}>
                <line x1={left} x2={right} y1={yAt} y2={yAt} className="chart-baseline" />
                <text x={left - 8} y={yAt + 4} textAnchor="end" className="chart-label">{pounds(value.toFixed(2))}</text>
              </g>;
            })}
            {lineSegments("equity_gbp").map((segment, index) =>
              segment.length > 1 && <polyline key={`equity-${index}`} points={segment.join(" ")}
                fill="none" stroke="#21745f" strokeWidth="2" />)}
            {lineSegments("capital_basis_gbp").map((segment, index) =>
              segment.length > 1 && <polyline key={`basis-${index}`} points={segment.join(" ")}
                fill="none" stroke="#69777c" strokeWidth="2" strokeDasharray="5 4" />)}
            {chartKnown.map(point => <g key={point.timestamp}>
              <circle cx={x(point)} cy={y(point.equity_gbp!)} r="3" fill="#21745f">
                <title>{`${dateLabel(point.timestamp)} · Value ${pounds(point.equity_gbp)}`}</title>
              </circle>
              <circle cx={x(point)} cy={y(point.capital_basis_gbp!)} r="2.5" fill="#69777c">
                <title>{`${dateLabel(point.timestamp)} · Capital added ${pounds(point.capital_basis_gbp)}`}</title>
              </circle>
            </g>)}
            {chartHistory.filter(point => !point.complete).map(point =>
              <g key={point.timestamp}>
                <line x1={x(point)} x2={x(point)} y1={top} y2={bottom}
                  stroke="#a63747" strokeDasharray="3 4" />
                <title>{`${dateLabel(point.timestamp)} · ${point.gap_reason}`}</title>
              </g>)}
            <text x={left} y="246" className="chart-label">{dateLabel(chartHistory[0].timestamp)}</text>
            <text x={right} y="246" textAnchor="end" className="chart-label">{dateLabel(chartHistory[chartHistory.length - 1].timestamp)}</text>
          </svg>
          <div className="investment-chart-legend"><span><i className="investment-legend-value" />Recorded value</span><span><i className="investment-legend-capital" />Capital added</span><span><i className="investment-legend-gap" />Incomplete record</span></div>
        </figure> : <p>No complete contemporaneous valuation is available in the recent history.</p>}
        {returnKnown.length > 0 ? <figure className="history-chart">
          <figcaption>Return since tracking began <span>{returnKnown.length} available of {chartHistory.length} recent record times</span></figcaption>
          <p className="position-note">This return separates investment growth from money added or withdrawn. Missing history means no return, not a zero return.
            It is not a benchmark or prediction.</p>
          <svg viewBox="0 0 800 260" role="img" aria-label="Time-weighted return since baseline; unavailable returns are gaps">
            <defs>
              <mask id={returnMaskId} maskUnits="userSpaceOnUse" x="0" y="0" width="800" height="260">
                <rect x="0" y="0" width="800" height="260" fill="white" />
                {chartHistory.filter(point => !point.complete || point.twr_pct === null).map(point =>
                  <rect key={point.timestamp} x={x(point) - 5} y={top} width="10" height={bottom - top} fill="black" />)}
              </mask>
            </defs>
            {[0, 1, 2, 3].map(index => {
              const value = returnMaximum.minus(returnRange.mul(index).div(3));
              const yAt = top + index / 3 * (bottom - top);
              return <g key={index}>
                <line x1={left} x2={right} y1={yAt} y2={yAt} className="chart-baseline" />
                <text x={left - 8} y={yAt + 4} textAnchor="end" className="chart-label">{percent(value.toString())}</text>
              </g>;
            })}
            <g mask={`url(#${returnMaskId})`}>
              {lineSegments("twr_pct", returnY).map((segment, index) =>
                segment.length > 1 && <polyline key={`return-${index}`} points={segment.join(" ")}
                  fill="none" stroke="#21745f" strokeWidth="2" />)}
              {returnKnown.map(point => <circle key={point.timestamp} cx={x(point)} cy={returnY(point.twr_pct!)} r="3" fill="#21745f">
                <title>{`${point.timestamp} · TWR ${percent(point.twr_pct)}`}</title>
              </circle>)}
            </g>
            {chartHistory.filter(point => !point.complete || point.twr_pct === null).map(point =>
              <g key={point.timestamp}>
                <line className="return-chart-gap" x1={x(point)} x2={x(point)} y1={top} y2={bottom}
                  stroke="#a63747" strokeDasharray="3 4" />
                <title>{`${point.timestamp} · ${point.gap_reason ?? "Time-weighted return unavailable."}`}</title>
              </g>)}
            <text x={left} y="246" className="chart-label">{dateLabel(chartHistory[0].timestamp)}</text>
            <text x={right} y="246" textAnchor="end" className="chart-label">{dateLabel(chartHistory[chartHistory.length - 1].timestamp)}</text>
          </svg>
          <div className="investment-chart-legend"><span><i className="investment-legend-value" />Recorded return</span><span><i className="investment-legend-gap" />Return unavailable</span></div>
        </figure> : <p>No time-weighted return is available in the recent history.</p>}
        <details className="help-disclosure"><summary>Detailed history and calculation notes</summary>
        <p className="position-note">The chart shows the latest {chartHistory.length} of {history.length} event clocks; the table shows the latest {latest.length}. Source-availability times and gaps are retained.
          Percentages are rounded for display and points near a gap may be masked. Exact UTC times are in the table.</p>
        <p className="position-note">Return since previous clock compares consecutive recorded time-weighted returns, not calendar periods or benchmarks. It is unavailable when either clock lacks a return or the starting unit value is zero; no gap is bridged.</p>
        <div className="table-scroll" tabIndex={0} aria-label="Recorded growth history">
          <table>
            <thead><tr><th>Event clock (UTC)</th><th>Status and gap reason</th><th>Available at</th><th>Value</th><th>Capital basis</th><th>Gain since baseline</th><th>TWR</th><th>Return since previous clock</th><th>Maximum drawdown</th></tr></thead>
            <tbody>{latest.map(({ point, previous, interval }) => <tr key={point.timestamp}>
              <td>{point.timestamp.includes(".") ? point.timestamp : timestamp(point.timestamp)}</td><td>{point.complete ? "Complete" : `Gap: ${point.gap_reason}`}</td>
              <td>{timestamp(point.source_available_at)}</td><td>{pounds(point.equity_gbp)}</td>
              <td>{pounds(point.capital_basis_gbp)}</td><td>{pounds(point.gain_since_baseline_gbp)}</td>
              <td>{percent(point.twr_pct)}</td>
              <td>{percent(interval)}{interval !== null && previous && <div>From {previous.timestamp}</div>}</td>
              <td>{percent(point.max_drawdown_pct)}</td>
            </tr>)}</tbody>
          </table>
        </div>
        </details>
      </>}
  </section>;
}

type RecordingAccount = { id: string; name: string; kind: "broker" | "deposit" };
type RecordingProduct = { id: string; name: string; sleeve: "equity" | "bond";
  quantity_increment: string; minimum_notional_gbp: string };
type CorrectableEvent = {
  id: string; type: "contribution" | "withdrawal" | "transfer" | "interest" | "dividend" |
    "buy" | "sell" | "account_fee" | "split" | "mark";
  account_id: string; currency: "GBP"; timestamp: string; available_at: string; evidence: string;
  amount_gbp?: string; destination_account_id?: string; product_id?: string;
  quantity?: string; price_gbp?: string; fee_gbp?: string; ratio?: string;
  role?: "core" | "dynamic" | "unassigned"; role_evidence?: string };
type RecordingSession = { enabled: boolean; csrf: string | null;
  opened: boolean; accounts: RecordingAccount[]; products: RecordingProduct[];
  latest_correctable_event: CorrectableEvent | null; correctable_events: CorrectableEvent[];
  current_balances: Record<string, string>;
  current_holdings: { account_id: string; product_id: string; quantity: string }[];
  portfolio_id?: string; revision?: number };
type RecordingPreview = { preview_id: string; revision: number; event: Record<string, unknown>;
  before: InvestmentSnapshot; after: InvestmentSnapshot;
  expires_at: string };
type OpeningHoldingInput = { account_id: string; product_id: string; quantity: string; price_gbp: string };
type ScopeHoldingInput = OpeningHoldingInput & { existing: boolean };
type RecordingDraftValues = {
  kind: string; role: string; assignmentAsset: string; accountId: string; destinationAccountId: string;
  productId: string; amount: string; price: string; quantity: string; fee: string; source: string;
  evidence: string; roleEvidence: string; effectiveAt: string; availableAt: string;
  balances: Record<string, string>; openingHoldings: OpeningHoldingInput[];
  correctionEventId: string; profileAction: string; profileTarget: string; profileEntityId: string;
  profileName: string; newProductId: string; newProductSleeve: string; newProductIncrement: string;
  newProductMinimum: string; newAccountId: string; newAccountName: string; newAccountBinding: string;
  newAccountKind: string; newAccountCash: string; scopeBalances: Record<string, string>;
  scopeHoldings: ScopeHoldingInput[]; ratio: string;
  termsRate?: string; termsBasis?: string; termsEffective?: string; termsMaturity?: string; termsNotice?: string;
};
type RecordingDraft = { version: 1; portfolio_id: string; revision: number;
  route: string; values: RecordingDraftValues };

const RECORDING_DRAFT_PREFIX = "goldscout.recording-draft.v1.";

function recordingDraftKey(portfolioId: string, route: string) {
  return `${RECORDING_DRAFT_PREFIX}${encodeURIComponent(portfolioId)}.${encodeURIComponent(route)}`;
}

function isRecordingDraft(value: unknown, portfolioId: string, revision: number,
  route: string): value is RecordingDraft {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const draft = value as Record<string, unknown>;
  const values = draft.values;
  if (typeof values !== "object" || values === null || Array.isArray(values)) return false;
  const fields = values as Record<string, unknown>;
  const strings = [
    "kind", "role", "assignmentAsset", "accountId", "destinationAccountId", "productId",
    "amount", "price", "quantity", "fee", "source", "evidence", "roleEvidence",
    "effectiveAt", "availableAt", "correctionEventId", "profileAction", "profileTarget",
    "profileEntityId", "profileName", "newProductId", "newProductSleeve", "newProductIncrement",
    "newProductMinimum", "newAccountId", "newAccountName", "newAccountBinding", "newAccountKind",
    "newAccountCash", "ratio",
  ];
  const isText = (item: unknown) => typeof item === "string" && item.length <= 2000;
  const termsFields = ["termsRate", "termsBasis", "termsEffective", "termsMaturity", "termsNotice"];
  const termsCount = termsFields.filter(key => key in fields).length;
  const isStringMap = (item: unknown) => typeof item === "object" && item !== null &&
    !Array.isArray(item) && Object.keys(item).length <= 100 &&
    Object.values(item).every(value => typeof value === "string" && value.length <= 200);
  const isOpeningRows = (item: unknown) => Array.isArray(item) && item.length <= 5000 &&
    item.every(row => typeof row === "object" && row !== null &&
      isText(row.account_id) && isText(row.product_id) &&
      isText(row.quantity) && isText(row.price_gbp));
  const isScopeRows = (item: unknown) => Array.isArray(item) && item.length <= 5000 &&
    item.every(row => typeof row === "object" && row !== null &&
      isText(row.account_id) && isText(row.product_id) &&
      isText(row.quantity) && isText(row.price_gbp) && typeof row.existing === "boolean");
  return draft.version === 1 && draft.portfolio_id === portfolioId &&
    draft.revision === revision && draft.route === route &&
    (termsCount === 0 || termsCount === termsFields.length && termsFields.every(key => isText(fields[key]))) &&
    Object.keys(fields).length === strings.length + 4 + termsCount &&
    strings.every(key => isText(fields[key])) &&
    isStringMap(fields.balances) && isOpeningRows(fields.openingHoldings) &&
    isStringMap(fields.scopeBalances) && isScopeRows(fields.scopeHoldings);
}

function losesReportedMetrics(preview: RecordingPreview): string[] {
  const { before, after } = preview;
  return [
    ...(before.complete && !after.complete ? ["complete portfolio value"] : []),
    ...(before.twr_pct !== null && after.twr_pct === null ? ["return since tracking began"] : []),
    ...(before.max_drawdown_pct !== null && after.max_drawdown_pct === null ? ["largest recorded fall"] : []),
  ];
}

function RecordingImpact({ preview }: { preview: RecordingPreview }) {
  const lost = losesReportedMetrics(preview);
  const newWarnings = preview.after.warnings.filter(item => !preview.before.warnings.includes(item));
  const observation = preview.event.type === "cash_observation"
    ? preview.after.cash_observations.find(item => item.account_id === preview.event.account_id &&
      item.timestamp === preview.event.timestamp && item.available_at === preview.event.available_at)
    : undefined;
  return <section className="recording-impact" aria-label="Valuation and performance impact">
    <h4>What this record changes</h4>
    {observation && <section aria-label="Preview cash comparison">
      <h5>Cash comparison at the observation time</h5>
      <dl className="health-facts">
        <div><dt>Observed balance</dt><dd>{pounds(observation.balance_gbp, true)}</dd></div>
        <div><dt>Difference from journal at that time</dt><dd>{pounds(observation.difference_gbp, true)}</dd></div>
      </dl>
      <p>{new Decimal(observation.difference_gbp).isZero()
        ? "The amounts match at this observation time. This does not resolve earlier differences or verify custody."
        : "These amounts differ. Saving preserves the observation; it does not adjust cash or resolve the difference."}</p>
    </section>}
    <dl className="health-facts">
      <div><dt>Complete portfolio value</dt><dd>{preview.before.complete ? "Available" : "Unavailable"}
        {" → "}{preview.after.complete ? "Available" : "Unavailable"}</dd></div>
      <div><dt>Return since tracking began</dt><dd>{percent(preview.before.twr_pct)}{" → "}{percent(preview.after.twr_pct)}</dd></div>
      <div><dt>Largest recorded fall</dt><dd>{percent(preview.before.max_drawdown_pct)}{" → "}{percent(preview.after.max_drawdown_pct)}</dd></div>
    </dl>
    {lost.length > 0 && <p role="alert" className="account-attention">
      This record would make previously available figures unavailable: {lost.join(", ")}.
      Cancel the preview if you need to check missing evidence first. Do not change the transaction's true dates or invent prices to retain a return.
      Later evidence cannot automatically repair a historical gap, and corrections do not create a replacement baseline.
    </p>}
    {preview.after.twr_pct === null && lost.length === 0 && <p>
      Return reporting is unavailable after this record. An accepted accounting record does not establish complete performance history.
    </p>}
    {newWarnings.length > 0 && <div><h5>New evidence warnings</h5><ul>
      {newWarnings.map(warning => <li key={warning}>{warning}</li>)}
    </ul></div>}
    <p className="position-note">This is a replay of the proposed record against the selected journal revision, not provider verification.
      Complete value is completeness for its evidence date, not proof of current information.</p>
  </section>;
}

const recordNames: Record<string, string> = {
  contribution: "Money added", withdrawal: "Money withdrawn", transfer: "Transfer between my accounts",
  interest: "Interest received", dividend: "Dividend received", account_fee: "Account or platform charge",
  cash_observation: "Check a cash balance", savings_terms: "Record savings terms",
  buy: "Investment bought", sell: "Investment sold", mark: "Update an investment price",
  split: "Share or unit split", role_assignment: "Assign an optional role",
  profile_change: "Change an account or investment", scope_addition: "Add an existing account",
  correction: "Correct an earlier record", opening: "Starting balances",
};
const recordGroups = [
  { id: "money", name: "Money, income and charges", kinds: ["contribution", "withdrawal", "transfer", "interest", "dividend", "account_fee"] },
  { id: "investment", name: "Investments", kinds: ["buy", "sell", "mark", "split"] },
  { id: "balance", name: "Check a cash balance", kinds: ["cash_observation"] },
  { id: "savings", name: "Savings", kinds: ["savings_terms"] },
  { id: "other", name: "Something else", kinds: ["role_assignment", "profile_change", "scope_addition", "correction"] },
];

const RECORD_REVIEW_FIELDS = [
  ["action", "Action"], ["target", "Account or investment"], ["account_id", "Account"],
  ["destination_account_id", "Destination account"], ["product_id", "Investment"],
  ["entity_id", "Selected account or investment"], ["role", "Optional role"], ["amount_gbp", "Amount"],
  ["amount", "Amount or units labelled"], ["role_evidence", "Role allocation evidence"],
  ["balance_gbp", "Observed balance"], ["quantity", "Quantity"],
  ["price_gbp", "Price per unit"], ["fee_gbp", "Recorded fee"], ["ratio", "Split ratio"],
  ["currency", "Currency"], ["name", "Name"], ["source", "Source"],
  ["annual_rate_pct", "Annual rate (%)"], ["rate_basis", "Rate basis"],
  ["effective_from", "Terms effective from"], ["maturity_date", "Maturity date"], ["notice_days", "Notice days"],
  ["timestamp", "Applies / recorded at"], ["available_at", "Source available from"],
  ["evidence", "Source description / correction reason"],
] as const;

function recordReviewValue(key: string, value: unknown, event: Record<string, unknown>,
  session: RecordingSession): string {
  if (value === null && ["annual_rate_pct", "rate_basis", "maturity_date", "notice_days"].includes(key)) return "Not recorded";
  if (key === "product_id" && value === null && event.type === "role_assignment") return "Cash";
  if (typeof value !== "string" && typeof value !== "number") return "Unavailable";
  const text = String(value);
  if (key === "account_id" || key === "destination_account_id") {
    const account = session.accounts.find(item => item.id === text);
    return account ? `${account.name} (${account.id})` : text;
  }
  if (key === "product_id") {
    const product = session.products.find(item => item.id === text);
    return product ? `${product.name} (${product.id})` : text;
  }
  if (key === "entity_id") {
    const collection = event.target === "account" ? session.accounts : session.products;
    const item = collection.find(candidate => candidate.id === text);
    return item ? `${item.name} (${item.id})` : text;
  }
  if (key === "amount_gbp" || key === "balance_gbp" || key === "price_gbp" || key === "fee_gbp")
    return pounds(text, true);
  if (key === "amount" && event.type === "role_assignment")
    return event.product_id === null ? pounds(text, true) : `${text} units`;
  if (key === "timestamp" || key === "available_at") return reviewTimestamp(text);
  if (key === "currency") return text.toUpperCase();
  if (key === "action" || key === "target" || key === "role")
    return label(text);
  if (key === "quantity") return `${text} units`;
  return text;
}

function RecordReview({ preview, session }: { preview: RecordingPreview; session: RecordingSession }) {
  const event = preview.event;
  const kind = typeof event.type === "string" ? event.type : "record";
  const priorTerms = kind === "savings_terms"
    ? preview.before.accounts.find(account => account.id === event.account_id)?.terms : null;
  const fields = RECORD_REVIEW_FIELDS.filter(([key]) => event[key] !== undefined);
  const balances = typeof event.balances_gbp === "object" && event.balances_gbp !== null
    ? event.balances_gbp as Record<string, unknown> : null;
  const holdings = Array.isArray(event.holdings)
    ? event.holdings.filter((item): item is Record<string, unknown> =>
      typeof item === "object" && item !== null) : [];
  const replacement = typeof event.replacement_event === "object" && event.replacement_event !== null
    ? event.replacement_event as Record<string, unknown> : null;
  const replacementFields = replacement
    ? RECORD_REVIEW_FIELDS.filter(([key]) => replacement[key] !== undefined) : [];
  const newAccount = typeof event.account === "object" && event.account !== null
    ? event.account as Record<string, unknown> : null;
  const newProduct = typeof event.product === "object" && event.product !== null
    ? event.product as Record<string, unknown> : null;
  const defaultRole = (item: Record<string, unknown>) =>
    typeof item.type === "string" && ["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell", "account_fee"].includes(item.type)
      && item.role === undefined;
  return <section className="record-review" aria-label="Record summary">
    <h4>{recordNames[kind] ?? label(kind)} — check before saving</h4>
    <p>The system checked the record's format and accounting, not whether your source is correct.
      Preview valid until {reviewTimestamp(preview.expires_at)}.</p>
    {kind === "savings_terms" && <section aria-label="Previous savings terms">
      <h5>Previous recorded terms</h5>
      {priorTerms ? <dl>
        <div><dt>Annual rate</dt><dd>{priorTerms.annual_rate_pct ?? "Not recorded"}{priorTerms.rate_basis ? ` (${priorTerms.rate_basis})` : ""}</dd></div>
        <div><dt>Effective from</dt><dd>{priorTerms.effective_from}</dd></div>
        <div><dt>Maturity date</dt><dd>{priorTerms.maturity_date ?? "Not recorded"}</dd></div>
        <div><dt>Notice days</dt><dd>{priorTerms.notice_days ?? "Not recorded"}</dd></div>
        <div><dt>Observed / available</dt><dd>{reviewTimestamp(priorTerms.observed_at)} / {reviewTimestamp(priorTerms.available_at)}</dd></div>
        <div><dt>Source</dt><dd>{priorTerms.source}</dd></div>
      </dl> : <p>No previous terms recorded.</p>}
      <p>The new record replaces all displayed terms. Blank optional fields become unknown, even if previously recorded.</p>
    </section>}
    <dl>{fields.map(([key, title]) => <div key={key}><dt>{kind === "savings_terms" && key === "timestamp" ? "Terms observed at" : title}</dt>
      <dd>{recordReviewValue(key, event[key], event, session)}</dd></div>)}</dl>
    {defaultRole(event) && <p>Optional role: Unassigned — no role chosen.</p>}
    {replacement && <section aria-label="Correction replacement details">
      <h5>Replacement details</h5>
      <p>Corrects an earlier {typeof replacement.type === "string" ? label(replacement.type) : "journal"}
        {" "}record at {typeof replacement.timestamp === "string" ? reviewTimestamp(replacement.timestamp) : "an unavailable time"}.
        Original evidence remains in the journal.</p>
      <dl>{replacementFields.map(([key, title]) => <div key={key}><dt>{title}</dt>
        <dd>{recordReviewValue(key, replacement[key], replacement, session)}</dd></div>)}</dl>
      {defaultRole(replacement) && <p>Optional role: Unassigned — no role chosen.</p>}
    </section>}
    {newAccount && <p>Account added to scope: {String(newAccount.name ?? "Name unavailable")}
      {" "}({String(newAccount.id ?? "ID unavailable")}), {String(newAccount.kind ?? "classification unavailable")}.</p>}
    {newProduct && <p>Product added: {String(newProduct.name ?? "Name unavailable")}
      {" "}({String(newProduct.id ?? "ID unavailable")}), {String(newProduct.sleeve ?? "classification unavailable")}.</p>}
    {balances && <section aria-label="Opening cash by account"><h5>Cash included in this record</h5><dl>
      {Object.entries(balances).map(([id, value]) => {
        const account = session.accounts.find(item => item.id === id);
        return <div key={id}><dt>{account ? `${account.name} (${id})` : id}</dt>
          <dd>{typeof value === "string" ? pounds(value, true) : "Unavailable"}</dd></div>;
      })}
    </dl></section>}
    {holdings.length > 0 && <section aria-label="Holdings included in this record"><h5>Holdings included</h5><ul>
      {holdings.map((holding, index) => {
        const account = session.accounts.find(item => item.id === holding.account_id);
        const product = session.products.find(item => item.id === holding.product_id);
        const priceValue = holding.price_gbp;
        return <li key={`${String(holding.account_id)}:${String(holding.product_id)}:${index}`}>
          {account?.name ?? String(holding.account_id ?? "Account unavailable")} / {product?.name ?? String(holding.product_id ?? "Product unavailable")}:
          {" "}{String(holding.quantity ?? "Quantity unavailable")} units; price {typeof priceValue === "string" ? pounds(priceValue, true) : "unknown — not zero"}.
        </li>;
      })}
    </ul></section>}
    <details className="recording-event">
      <summary>Exact event and evidence being appended</summary>
      <p>Journal revision {preview.revision}. This is the exact event used for the summary above.</p>
      <pre>{JSON.stringify(event, null, 2)}</pre>
    </details>
  </section>;
}

async function recordingRequest<T>(path: string, csrf?: string, payload?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(10000),
    headers: payload === undefined ? undefined : {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const code = typeof body === "object" && body !== null && "error" in body
      ? String(body.error) : "recording_request_failed";
    throw new RecordingRequestError(code);
  }
  return body as T;
}

function InvestmentRecording() {
  const { search } = useLocation();
  const navigate = useNavigate();
  const [session, setSession] = useState<RecordingSession | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kind, setKind] = useState("opening");
  const [priceTargetStatus, setPriceTargetStatus] = useState<"matched" | "stale" | null>(null);
  const [cashTargetStatus, setCashTargetStatus] = useState<"matched" | "stale" | null>(null);
  const [updateTargetStatus, setUpdateTargetStatus] = useState<"matched" | "stale" | null>(null);
  const [role, setRole] = useState<"core" | "dynamic" | "unassigned">("unassigned");
  const [assignmentAsset, setAssignmentAsset] = useState<"cash" | "holding">("cash");
  const [accountId, setAccountId] = useState("");
  const [destinationAccountId, setDestinationAccountId] = useState("");
  const [productId, setProductId] = useState("");
  const [amount, setAmount] = useState("");
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [fee, setFee] = useState("");
  const [source, setSource] = useState("");
  const [termsRate, setTermsRate] = useState("");
  const [termsBasis, setTermsBasis] = useState("");
  const [termsEffective, setTermsEffective] = useState("");
  const [termsMaturity, setTermsMaturity] = useState("");
  const [termsNotice, setTermsNotice] = useState("");
  const [evidence, setEvidence] = useState("");
  const [roleEvidence, setRoleEvidence] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [availableAt, setAvailableAt] = useState("");
  const [balances, setBalances] = useState<Record<string, string>>({});
  const [openingHoldings, setOpeningHoldings] = useState<OpeningHoldingInput[]>([]);
  const [correctionEventId, setCorrectionEventId] = useState("");
  const [profileAction, setProfileAction] = useState<"add_product" | "rename" | "retire">("add_product");
  const [profileTarget, setProfileTarget] = useState<"account" | "product">("product");
  const [profileEntityId, setProfileEntityId] = useState("");
  const [profileName, setProfileName] = useState("");
  const [newProductId, setNewProductId] = useState("");
  const [newProductSleeve, setNewProductSleeve] = useState<"equity" | "bond">("equity");
  const [newProductIncrement, setNewProductIncrement] = useState("");
  const [newProductMinimum, setNewProductMinimum] = useState("");
  const [newAccountId, setNewAccountId] = useState("");
  const [newAccountName, setNewAccountName] = useState("");
  const [newAccountBinding, setNewAccountBinding] = useState("");
  const [newAccountKind, setNewAccountKind] = useState<"broker" | "deposit">("broker");
  const [newAccountCash, setNewAccountCash] = useState("");
  const [scopeBalances, setScopeBalances] = useState<Record<string, string>>({});
  const [scopeHoldings, setScopeHoldings] = useState<ScopeHoldingInput[]>([]);
  const [ratio, setRatio] = useState("");
  const [preview, setPreview] = useState<RecordingPreview | null>(null);
  const [previewVersion, setPreviewVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingState] = useState(() => {
    try { return { pending: pendingRecording(window.localStorage), error: false }; }
    catch { return { pending: null, error: true }; }
  });
  const [pending, setPending] = useState<PendingRecording | null>(pendingState.pending);
  const [storageError, setStorageError] = useState(pendingState.error);
  const [draftKey, setDraftKey] = useState<string | null>(null);
  const [draftStatus, setDraftStatus] = useState<"loading" | "none" | "saved" | "stale" | "error">("loading");
  const [draftReady, setDraftReady] = useState(false);
  const [draftDirty, setDraftDirty] = useState(false);
  const [draftError, setDraftError] = useState("");

  useEffect(() => {
    let active = true;
    setSession(null);
    setPreview(null);
    setDraftReady(false);
    setDraftDirty(false);
    setDraftKey(null);
    setDraftStatus("loading");
    setDraftError("");
    setKind("");
    setRole("unassigned");
    setAssignmentAsset("cash");
    setAccountId("");
    setDestinationAccountId("");
    setProductId("");
    setAmount("");
    setPrice("");
    setQuantity("");
    setFee("");
    setSource("");
    setTermsRate(""); setTermsBasis(""); setTermsEffective(""); setTermsMaturity(""); setTermsNotice("");
    setEvidence("");
    setRoleEvidence("");
    setEffectiveAt("");
    setAvailableAt("");
    setBalances({});
    setOpeningHoldings([]);
    setCorrectionEventId("");
    setProfileAction("add_product");
    setProfileTarget("product");
    setProfileEntityId("");
    setProfileName("");
    setNewProductId("");
    setNewProductSleeve("equity");
    setNewProductIncrement("");
    setNewProductMinimum("");
    setNewAccountId("");
    setNewAccountName("");
    setNewAccountBinding("");
    setNewAccountKind("broker");
    setNewAccountCash("");
    setScopeBalances({});
    setScopeHoldings([]);
    setRatio("");
    setPriceTargetStatus(null);
    setCashTargetStatus(null);
    setUpdateTargetStatus(null);
    setMessage(null);
    async function refreshSession() {
      try {
        const result = await recordingRequest<RecordingSession>(
          "/api/investment/v1/recording/session");
        if (!active) return;
        setSession(result);
        setLoadError(null);
        const params = new URLSearchParams(search);
        const missingPrice = params.get("record") === "missing-price";
        const cashCheck = params.get("record") === "cash-check";
        const accountUpdate = params.get("record") === "account-update";
        const requestedAccount = params.get("account_id");
        const requestedProduct = params.get("product_id");
        const account = result.accounts.find(item => item.id === requestedAccount && item.kind === "broker");
        const cashAccount = result.accounts.find(item => item.id === requestedAccount);
        const product = result.products.find(item => item.id === requestedProduct);
        const stillHeld = result.current_holdings.some(item =>
          item.account_id === requestedAccount && item.product_id === requestedProduct);
        setUpdateTargetStatus(null);
        if (accountUpdate) {
          const requestedKind = params.get("kind") ?? "";
          const allowed = cashAccount?.kind === "broker"
            ? ["contribution", "withdrawal", "transfer", "interest", "dividend", "account_fee", "buy", "sell", "cash_observation", "mark"]
            : ["contribution", "withdrawal", "transfer", "interest", "account_fee", "cash_observation", "savings_terms"];
          const matches = result.opened && cashAccount && allowed.includes(requestedKind) &&
            (requestedKind !== "mark" || (product && stillHeld));
          setPreview(null);
          setPriceTargetStatus(null);
          setCashTargetStatus(null);
          setAccountId(matches ? cashAccount.id : "");
          setProductId(matches && requestedKind === "mark" && product ? product.id : "");
          setKind(matches ? requestedKind : "");
          setUpdateTargetStatus(matches ? "matched" : "stale");
          setDestinationAccountId(result.accounts.find(item => item.id !== requestedAccount)?.id ?? "");
        } else if (missingPrice) {
          setCashTargetStatus(null);
          if (result.opened) {
            setKind("mark");
            if (account && product && stillHeld) {
              setAccountId(account.id);
              setProductId(product.id);
              setPriceTargetStatus("matched");
            } else {
              setAccountId("");
              setProductId("");
              setPriceTargetStatus("stale");
            }
          } else {
            setKind("opening");
            setAccountId("");
            setProductId("");
            setPriceTargetStatus("stale");
          }
        } else if (cashCheck) {
          setPriceTargetStatus(null);
          if (result.opened) {
            setKind("cash_observation");
            setAccountId(cashAccount?.id ?? "");
            setCashTargetStatus(cashAccount ? "matched" : "stale");
          } else {
            setKind("opening");
            setAccountId("");
            setCashTargetStatus("stale");
          }
        } else {
          setPriceTargetStatus(null);
          setCashTargetStatus(null);
          if (result.accounts.length && !accountId) setAccountId(result.accounts[0].id);
          if (result.products.length && !productId) setProductId(result.products[0].id);
          setKind(result.opened ? "" : "opening");
        }
        if (!accountUpdate && result.accounts.length > 1 && !destinationAccountId)
          setDestinationAccountId(result.accounts[1].id);
        setBalances(current => Object.keys(current).length ? current :
          Object.fromEntries(result.accounts.map(item => [item.id, ""])));
        setScopeBalances(current => Object.keys(current).length ? current :
          Object.fromEntries(result.accounts.map(item => [item.id, ""])));
        setScopeHoldings(current => current.length ? current : result.current_holdings.map(holding => ({
          ...holding, price_gbp: "", existing: true,
        })));
        if (result.portfolio_id && Number.isSafeInteger(result.revision) && result.revision! >= 0) {
          const key = recordingDraftKey(result.portfolio_id, search);
          setDraftKey(key);
          try {
            const raw = window.localStorage.getItem(key);
            if (raw === null) {
              setDraftStatus("none");
            } else if (raw.length > 1_000_000) {
              setDraftStatus("error");
              setDraftError("Saved recording draft is too large to restore. It was left unchanged.");
            } else {
              const saved: unknown = JSON.parse(raw);
              if (!isRecordingDraft(saved, result.portfolio_id, result.revision!, search)) {
                setDraftStatus("stale");
                setDraftError("This saved draft no longer matches the current journal revision or record type. Discard it before entering a new record.");
              } else {
                const values = saved.values;
                setKind(values.kind);
                setRole(values.role as "core" | "dynamic" | "unassigned");
                setAssignmentAsset(values.assignmentAsset as "cash" | "holding");
                setAccountId(values.accountId);
                setDestinationAccountId(values.destinationAccountId);
                setProductId(values.productId);
                setAmount(values.amount); setPrice(values.price); setQuantity(values.quantity);
                setFee(values.fee); setSource(values.source); setEvidence(values.evidence);
                setTermsRate(values.termsRate ?? ""); setTermsBasis(values.termsBasis ?? "");
                setTermsEffective(values.termsEffective ?? ""); setTermsMaturity(values.termsMaturity ?? "");
                setTermsNotice(values.termsNotice ?? "");
                setRoleEvidence(values.roleEvidence); setEffectiveAt(values.effectiveAt);
                setAvailableAt(values.availableAt); setBalances(values.balances);
                setOpeningHoldings(values.openingHoldings);
                setCorrectionEventId(values.correctionEventId);
                setProfileAction(values.profileAction as "add_product" | "rename" | "retire");
                setProfileTarget(values.profileTarget as "account" | "product");
                setProfileEntityId(values.profileEntityId); setProfileName(values.profileName);
                setNewProductId(values.newProductId);
                setNewProductSleeve(values.newProductSleeve as "equity" | "bond");
                setNewProductIncrement(values.newProductIncrement);
                setNewProductMinimum(values.newProductMinimum);
                setNewAccountId(values.newAccountId); setNewAccountName(values.newAccountName);
                setNewAccountBinding(values.newAccountBinding);
                setNewAccountKind(values.newAccountKind as "broker" | "deposit");
                setNewAccountCash(values.newAccountCash); setScopeBalances(values.scopeBalances);
                setScopeHoldings(values.scopeHoldings); setRatio(values.ratio);
                setDraftDirty(true);
                setDraftStatus("saved");
              }
            }
          } catch {
            setDraftStatus("error");
            setDraftError("The saved recording draft could not be read. It was not changed.");
          }
        } else {
          setDraftKey(null);
          setDraftStatus("none");
        }
        setDraftReady(true);
      } catch (error) {
        if (active) setLoadError(error instanceof Error ? error.message : "recording_session_unavailable");
      }
    }
    void refreshSession();
    return () => { active = false; };
  }, [search]);
  useEffect(() => {
    if (!draftReady || !draftDirty || !draftKey ||
        draftStatus === "stale" || draftStatus === "error" || preview || pending || !session ||
        !session.portfolio_id || typeof session.revision !== "number" ||
        !Number.isSafeInteger(session.revision)) return;
    try {
      const values: RecordingDraftValues = {
        kind, role, assignmentAsset, accountId, destinationAccountId, productId,
        amount, price, quantity, fee, source, evidence, roleEvidence, effectiveAt,
        availableAt, balances, openingHoldings, correctionEventId, profileAction,
        profileTarget, profileEntityId, profileName, newProductId, newProductSleeve,
        newProductIncrement, newProductMinimum, newAccountId, newAccountName,
        newAccountBinding, newAccountKind, newAccountCash, scopeBalances,
        scopeHoldings, ratio, termsRate, termsBasis, termsEffective, termsMaturity, termsNotice,
      };
      const raw = JSON.stringify({
        version: 1, portfolio_id: session.portfolio_id, revision: session.revision,
        route: search, values,
      } satisfies RecordingDraft);
      if (raw.length > 1_000_000) throw new Error("Recording draft exceeds the browser storage limit.");
      window.localStorage.setItem(draftKey, raw);
      setDraftStatus("saved");
      setDraftError("");
    } catch (error) {
      setDraftStatus("error");
      setDraftError(error instanceof Error ? error.message : "The recording draft could not be saved.");
    }
  }, [draftReady, draftDirty, draftKey, draftStatus, preview, pending, session, search,
    kind, role, assignmentAsset, accountId, destinationAccountId, productId, amount,
    price, quantity, fee, source, evidence, roleEvidence, effectiveAt, availableAt,
    balances, openingHoldings, correctionEventId, profileAction, profileTarget,
    profileEntityId, profileName, newProductId, newProductSleeve, newProductIncrement,
    newProductMinimum, newAccountId, newAccountName, newAccountBinding, newAccountKind,
    newAccountCash, scopeBalances, scopeHoldings, ratio, termsRate, termsBasis, termsEffective, termsMaturity, termsNotice]);
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key !== PENDING_RECORDING_KEY && event.key !== null) return;
      try { setPending(pendingRecording(window.localStorage)); setStorageError(false); }
      catch { setStorageError(true); }
    };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, []);
  async function makePreview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session?.csrf) return;
    setBusy(true);
    setMessage(null);
    try {
      if (!navigator.locks) throw new Error("This browser cannot coordinate safe recording across tabs.");
      const version = recordingVersion(window.localStorage);
      const unfinished = pendingRecording(window.localStorage);
      if (unfinished) {
        setPending(unfinished);
        throw new Error("Another recording has an unfinished save. Resolve it before recording anything else.");
      }
      const id = crypto.randomUUID();
      const { timestamp, available_at: available } = recordingClocks(effectiveAt, availableAt,
        kind === "profile_change" || kind === "scope_addition");
      const common = { id, timestamp, available_at: available, evidence };
      if (["savings_terms", "account_fee"].includes(kind) && updateTargetStatus === "matched" &&
          accountId !== new URLSearchParams(search).get("account_id"))
        throw new Error("This draft does not match the guided account. Discard it and start again from the account review.");
      let value: Record<string, unknown>;
      if (kind === "opening") {
        const holdings = openingHoldings.filter(item =>
          item.account_id && item.product_id && item.quantity).map(item => ({
            ...item, price_gbp: item.price_gbp === "" ? null : item.price_gbp,
          }));
        value = { ...common, type: "opening", balances_gbp: balances, holdings };
      } else if (kind === "correction") {
        const original = session.correctable_events.find(item => item.id === correctionEventId);
        if (!original) throw new Error("Select an eligible recorded event to correct.");
        const replacement: Record<string, unknown> = {
          ...original, id: crypto.randomUUID(), available_at: available, evidence,
        };
        if (["contribution", "withdrawal", "interest", "dividend", "transfer", "account_fee"].includes(original.type))
          replacement.amount_gbp = amount;
        if (original.type === "interest" || original.type === "dividend")
          replacement.role_evidence = roleEvidence;
        if (original.type === "dividend") replacement.product_id = productId;
        if (original.type === "transfer") replacement.destination_account_id = destinationAccountId;
        if (["buy", "sell"].includes(original.type)) {
          replacement.product_id = productId;
          replacement.quantity = quantity;
          replacement.price_gbp = price;
          replacement.fee_gbp = fee;
        }
        if (original.type === "split") {
          replacement.product_id = productId;
          replacement.ratio = ratio;
        }
        if (original.type === "mark") {
          replacement.product_id = productId;
          replacement.price_gbp = price;
        }
        value = { ...common, type: "correction",
          original_event_id: original.id, replacement_event: replacement };
      } else if (kind === "profile_change") {
        if (profileAction === "add_product") {
          value = { ...common, type: kind, action: profileAction,
            product: { id: newProductId, name: profileName, sleeve: newProductSleeve,
              currency: "GBP", quantity_increment: newProductIncrement,
              minimum_notional_gbp: newProductMinimum } };
        } else if (profileAction === "rename") {
          value = { ...common, type: kind, action: profileAction,
            target: profileTarget, entity_id: profileEntityId, name: profileName };
        } else {
          value = { ...common, type: kind, action: profileAction,
            target: profileTarget, entity_id: profileEntityId };
        }
      } else if (kind === "scope_addition") {
        const account = { id: newAccountId, name: newAccountName,
          kind: newAccountKind, binding: newAccountBinding, currency: "GBP" };
        const holdings = scopeHoldings.filter(item =>
          item.account_id && item.product_id && item.quantity && item.price_gbp)
          .map(({ existing: _existing, ...item }) => item);
        value = { ...common, type: kind, account,
          balances_gbp: { ...scopeBalances, [newAccountId]: newAccountCash }, holdings };
      } else if (kind === "cash_observation") {
        value = { ...common, type: kind, account_id: accountId, balance_gbp: amount, source };
      } else if (kind === "savings_terms") {
        const validDate = (date: string) => {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date.startsWith("0000")) return false;
          const parsed = new Date(`${date}T00:00:00Z`);
          return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date;
        };
        if (!session.accounts.some(account => account.id === accountId && account.kind === "deposit"))
          throw new Error("Choose an included savings account for these terms.");
        if (!source.trim() || !evidence.trim()) throw new Error("Enter a source and source description for these terms.");
        if (!validDate(termsEffective) || termsMaturity && (!validDate(termsMaturity) || termsMaturity < termsEffective))
          throw new Error("Enter a valid effective date and, if supplied, a maturity date on or after it.");
        if (termsRate && (!/^(?:0|[1-9][0-9]{0,14})(?:\.[0-9]{1,8})?$/.test(termsRate) ||
            new Decimal(termsRate).greaterThan(100) || !["AER", "gross"].includes(termsBasis)) ||
            !termsRate && termsBasis)
          throw new Error("Enter a valid annual rate up to 100 and its basis, or leave both unknown.");
        if (termsNotice && (!/^(?:0|[1-9][0-9]{0,3})$/.test(termsNotice) || Number(termsNotice) > 3650))
          throw new Error("Notice days must be a whole number from 0 to 3650, or blank if unknown.");
        value = { ...common, type: kind, account_id: accountId, source,
          annual_rate_pct: termsRate || null, rate_basis: termsBasis || null,
          effective_from: termsEffective, maturity_date: termsMaturity || null,
          notice_days: termsNotice === "" ? null : Number(termsNotice) };
      } else if (kind === "role_assignment") {
        if (role === "unassigned") throw new Error("Choose core (long-term) or dynamic (experimental) when assigning a role. Unassigned means no role has been chosen.");
        value = { ...common, type: kind, account_id: accountId, role,
          product_id: assignmentAsset === "holding" ? productId : null, amount };
      } else {
        value = standardRecord(kind, common, { accountId, destinationAccountId, productId,
          role, roleEvidence, amount, quantity, price, fee, ratio });
      }
      const result = await navigator.locks.request("goldscout-recording-confirmation", () => {
        if (recordingVersion(window.localStorage) !== version) throw new RecordingPreviewChangedError();
        if (pendingRecording(window.localStorage))
          throw new Error("Another recording has an unfinished save. Resolve it before recording anything else.");
        return recordingRequest<RecordingPreview>(
          "/api/investment/v1/recording/preview", session.csrf ?? undefined,
          { request_id: crypto.randomUUID(), event: value });
      });
      if (!validate(result.before) || !validate(result.after)
          || result.before.source_mode !== "recorded" || result.after.source_mode !== "recorded"
          || result.before.portfolio_id !== result.after.portfolio_id
          || !Number.isSafeInteger(result.revision) || result.revision < 0
          || result.before.revision !== result.revision || result.after.revision !== result.revision + 1
          || typeof result.preview_id !== "string" || !result.preview_id
          || typeof result.expires_at !== "string" || !Number.isFinite(Date.parse(result.expires_at)))
        throw new Error("The preview's valuation and performance evidence failed validation. No confirmation is available; request a fresh preview.");
      if (recordingVersion(window.localStorage) !== version) throw new RecordingPreviewChangedError();
      setPreviewVersion(version);
      setPreview(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "preview_rejected");
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmPreview() {
    if (!session?.csrf || (!preview && !pending)) return;
    const eventId = preview?.event.id;
    const attempt = pending ?? (typeof eventId === "string" && preview &&
      Number.isSafeInteger(preview.revision) && preview.revision >= 0
      ? { previewId: preview.preview_id, eventId, expectedRevision: preview.revision + 1 } : null);
    if (!attempt) { setMessage("The preview has no recording reference. Do not confirm it."); return; }
    if (!pending && preview && Date.parse(preview.expires_at) <= Date.now()) {
      setMessage("The preview expired. Cancel it and request a new preview; no confirmation was sent.");
      return;
    }
    if (!navigator.locks) {
      setMessage("This browser cannot coordinate safe recording across tabs. No confirmation was sent.");
      return;
    }
    setBusy(true);
    setMessage(null);
    setPending(attempt);
    let reviewNotice = "";
    try {
      await navigator.locks.request("goldscout-recording-confirmation", async () => {
        if (!pending && recordingVersion(window.localStorage) !== previewVersion)
          throw new RecordingPreviewChangedError();
        await confirmRecording(window.localStorage, attempt, previewId =>
          recordingRequest("/api/investment/v1/recording/confirm", session.csrf ?? undefined,
            { preview_id: previewId }));
        if (preview && preview.preview_id === attempt.previewId &&
            preview.revision + 1 === attempt.expectedRevision) {
          try {
            advanceReviewAfterSave(preview.before.portfolio_id, attempt.expectedRevision, preview.event);
          } catch {
            reviewNotice = "The record was saved, but review progress could not be updated. Check your review before continuing.";
          }
        }
      });
      setPending(null);
      setPreview(null);
      let savedDraftNotice = "";
      try {
        if (draftKey) window.localStorage.removeItem(draftKey);
        setDraftStatus("none");
        setDraftDirty(false);
      } catch {
        savedDraftNotice = " The old browser draft could not be cleared; it is bound to the previous journal revision and will not be restored.";
      }
      setMessage(`Record committed. Refreshing the recorded portfolio.${savedDraftNotice}`);
      const updateAccount = new URLSearchParams(search).get("account_id");
      if (updateTargetStatus === "matched" && updateAccount)
        navigate(`/investment/update?account_id=${encodeURIComponent(updateAccount)}`,
          { state: { recordSaved: true, reviewNotice: [savedDraftNotice, reviewNotice].filter(Boolean).join(" ") } });
      else {
        navigate(`${window.location.pathname}${search}`, { replace: true, state: { recordSaved: true, recordSavedId: attempt.eventId,
          reviewNotice: [savedDraftNotice, reviewNotice].filter(Boolean).join(" ") } });
        window.location.reload();
      }
    } catch (error) {
      if (error instanceof RecordingPreviewExpiredError || error instanceof RecordingPreviewChangedError) {
        try { setPending(pendingRecording(window.localStorage)); }
        catch { setStorageError(true); }
        setMessage(error.message);
        return;
      }
      try {
        const unfinished = pendingRecording(window.localStorage);
        if (unfinished) setPending(unfinished);
      } catch { setStorageError(true); }
      setMessage(error instanceof RecordingSavedStorageError
        ? `${error.message} Do not enter it again; check the journal and browser storage.`
        : `The save outcome is unknown: ${error instanceof Error ? error.message : "confirmation unavailable"}. The record may already be saved. Do not enter it again. Retry the same confirmation, or check the journal and terminal before taking further action.`);
    } finally {
      setBusy(false);
    }
  }

  if (storageError) return <section className="history-group" aria-label="Manual recording">
    <h2>Recording is unavailable</h2><p role="alert">{message ?? "The browser could not check for an unfinished save. Do not enter the record again. Check the journal and browser storage before continuing."}</p>
  </section>;
  if (loadError) return <section className="history-group" aria-label="Manual recording">
    <h2>Manual portfolio records</h2><p role="alert">Recording controls unavailable: {loadError}
      {pending && ` A save for record ${pending.eventId} may already have completed. Do not enter it again; check the journal and terminal.`}</p>
  </section>;
  if (!session) return <section className="history-group" aria-label="Manual recording">
    <h2>Manual portfolio records</h2><p role="status">Checking recording controls.</p>
  </section>;
  if (!session.enabled && pending) return <section className="history-group" aria-label="Manual recording">
    <h2>Check an unfinished save</h2><p role="alert">Record {pending.eventId} may already have been saved.
      Recording is disabled, so this page cannot retry the confirmation. Do not enter it again; check the journal and terminal.</p>
  </section>;
  if (!session.enabled) return <section className="history-group" aria-label="Manual recording">
    <h2>Viewing only</h2>
    <p>Manual recording is unavailable in this session. Normal local launches allow recording after setup.</p>
    <details className="help-disclosure"><summary>Use a recorded workspace</summary>
      <p>If you started with <code>--investment-read-only</code>, restart without that option to add records.
        Keep the same <code>--workspace</code> or <code>--investment-database</code> selection.
        Synthetic demos do not save records.</p>
    </details>
  </section>;
  if (pending && (!preview || pending.previewId !== preview.preview_id)) return <section className="history-group" aria-label="Manual recording">
    <h2>Check an unfinished save</h2>
    <p role="alert">Record {pending.eventId} may already have been saved. Do not enter it again.
      {pending.expectedRevision === null
        ? " This older save reference has no expected revision, so it cannot be verified automatically. Check the journal and terminal before taking further action."
        : " Retry this same confirmation. If the session or preview has expired, check the journal and terminal before taking further action."}</p>
    <button type="button" disabled={busy || pending.expectedRevision === null} onClick={() => void confirmPreview()}>Retry same confirmation</button>
    {message && <p role="alert">{message}</p>}
  </section>;
  if (updateTargetStatus === "stale") return <section className="record-review" role="alert">
    <h2>This account update is no longer available</h2>
    <p>The account, record type or held investment no longer matches the current recording session. No alternative was selected.</p>
    <NavLink to="/investment">Review your current accounts</NavLink>
  </section>;
  const opened = session.opened;
  const correction = session.correctable_events.find(item => item.id === correctionEventId) ?? null;
  const recordType = kind === "correction" ? correction?.type : kind;
  const availableKinds = opened
    ? ["contribution", "withdrawal", "transfer", "interest", "dividend", "account_fee", "buy", "sell", "split",
      "mark", "cash_observation", "savings_terms", "role_assignment", "profile_change", "scope_addition",
      ...(session.correctable_events.length ? ["correction"] : [])]
    : ["opening"];
  const selectedProduct = session.products.find(product => product.id === productId);
  const discardRecordingDraft = () => {
    try {
      if (draftKey) window.localStorage.removeItem(draftKey);
      setDraftStatus("none");
      setDraftDirty(false);
      setDraftError("");
    } catch {
      setDraftStatus("error");
      setDraftError("The saved draft could not be cleared from this browser.");
    }
  };

  return <section className="history-group investment-recording" aria-label="Manual recording">
    <h2>Record details</h2>
    <p>Enter a completed transaction or sourced figure. Review the preview before saving; entries are not independently verified.</p>
    {draftStatus === "saved" && <p className="position-note">
      A draft was restored from this browser. Its preview and confirmation were not saved; check all details and request a fresh preview.
    </p>}
    {draftStatus === "stale" || draftStatus === "error" ? <div role="alert">
      <p>{draftError}</p><button type="button" onClick={discardRecordingDraft}>Discard saved draft</button>
    </div> : null}
    {draftStatus === "none" && session.portfolio_id && <p className="position-note">
      Unsaved form details are saved in this browser on this computer. Previews are always requested afresh.
      If an earlier save had an uncertain result, its exact retry reference is retained and a new entry is blocked until it is resolved.
    </p>}
    {updateTargetStatus === "matched" && <p className="position-note">
      Started from a guided account update. Check the preselected account against your source.
      {" "}<NavLink to={`/investment/update?account_id=${encodeURIComponent(new URLSearchParams(search).get("account_id") ?? "")}`}>Return to the account review</NavLink>.
    </p>}
    {priceTargetStatus === "matched" && <p className="position-note" role="status">
      Started from a missing-price reminder. The account and investment are preselected from the saved portfolio; verify them and use a source that supports the price and both dates. This record does not repair earlier gaps automatically.
    </p>}
    {priceTargetStatus === "stale" && <p className="position-note" role="status">
      This missing-price reminder no longer matches a held investment in the current records. Nothing was preselected; check the current holdings before choosing an account or investment.
    </p>}
    {cashTargetStatus === "matched" && kind === "cash_observation" && <p className="position-note" role="status">
      Started from an account cash check. Verify the source, amount and dates; this observation does not update recorded cash.
    </p>}
    {cashTargetStatus === "stale" && <p className="position-note" role="status">
      This account is no longer available for a cash check. Check the current records and select an available account.
    </p>}
    {preview ? <div className="recording-preview" role="group" aria-label="Review record before confirmation">
      <h3>Review before recording</h3>
      <dl className="health-facts">
        <div><dt>Current recorded value</dt><dd>{pounds(preview.before.equity_gbp)}</dd></div>
        <div><dt>Value after this record</dt><dd>{pounds(preview.after.equity_gbp)}</dd></div>
      </dl>
      <RecordReview preview={preview} session={session} />
      <RecordingImpact preview={preview} />
      <p className="position-note">Only confirm if the account, figures, dates, source and before/after values match the record you checked.</p>
      {pending && <p role="alert">This record may already have been saved. Do not enter it again; retry the same confirmation.</p>}
      <button type="button" disabled={busy}
        onClick={() => void confirmPreview()}>{pending ? "Retry same confirmation" : "Confirm and append record"}</button>
      <button type="button" disabled={busy || Boolean(pending)} onClick={() => setPreview(null)}>Cancel preview</button>
    </div> : <form className="recording-form" onSubmit={makePreview}
      onChange={() => setDraftDirty(true)}
      onClickCapture={event => {
        if (event.target instanceof Element && event.target.closest("button")) setDraftDirty(true);
      }}>
      {opened && <label>What are you recording?<select required value={kind}
        onChange={event => {
          setKind(event.target.value); setCorrectionEventId("");
          if (event.target.value === "savings_terms") {
            if (!session.accounts.some(account => account.id === accountId && account.kind === "deposit")) setAccountId("");
            setTermsRate(""); setTermsBasis(""); setTermsEffective(""); setTermsMaturity(""); setTermsNotice(""); setSource("");
          }
          setCashTargetStatus(null);
          setFee(""); setEffectiveAt(""); setAvailableAt(""); setEvidence(""); setRole("unassigned"); setRoleEvidence("");
        }}>
        <option value="">Choose a record</option>
        {recordGroups.map(group => <optgroup key={group.id} label={group.name}>
          {group.kinds.filter(value => availableKinds.includes(value))
            .map(value => <option key={value} value={value}>{recordNames[value]}</option>)}
        </optgroup>)}
      </select></label>}
      <details className="help-disclosure"><summary>Which record should I choose?</summary>
        <p>Money added or withdrawn changes recorded cash; a transfer moves cash between tracked accounts. Buy or sell records a completed trade; a price update changes valuation only.</p>
        <p>A cash check compares a source with recorded cash without changing it. Record an account/platform charge as a cost, not as an external withdrawal. Add an account when bringing existing assets into view, not when depositing money.</p>
        <p>Roles are optional labels, not targets or trade approval. Match the role already recorded; unassigned is not core or dynamic.</p>
      </details>
      {["contribution", "withdrawal"].includes(kind) && <p className="recording-preflight">
        Before previewing: a deposit or withdrawal needs complete, source-supported valuation evidence for every held investment at the transaction time,
        available by that time. This includes holdings in other accounts. Record supported prices first; do not invent or backdate them.
        Missing flow-time value can make historical performance unavailable, and a later price cannot repair it.
      </p>}
      {(!opened || kind !== "") && <>
      {kind === "correction" && <label>Event to correct<select required value={correctionEventId}
        onChange={event => {
          setCorrectionEventId(event.target.value);
          const item = session.correctable_events.find(candidate => candidate.id === event.target.value);
          if (item?.amount_gbp) setAmount(item.amount_gbp);
          if (item?.quantity) setQuantity(item.quantity);
          if (item?.price_gbp) setPrice(item.price_gbp);
          if (item?.fee_gbp) setFee(item.fee_gbp);
          if (item?.ratio) setRatio(item.ratio);
          setRoleEvidence(item?.role_evidence ?? "");
          if (item?.product_id) setProductId(item.product_id);
          if (item?.account_id) setAccountId(item.account_id);
          if (item?.destination_account_id) setDestinationAccountId(item.destination_account_id);
        }}>
        <option value="">Choose an earlier record</option>
        {session.correctable_events.map(item => <option key={item.id} value={item.id}>
          {label(item.type)} · {item.id} · {timestamp(item.timestamp)}
        </option>)}
      </select></label>}
      {["contribution", "withdrawal", "transfer", "interest", "dividend", "account_fee", "buy", "sell", "role_assignment"].includes(kind) &&
        <label>Optional role<select aria-label="Optional role" aria-describedby="record-role-help" value={role} onChange={event => {
          setRole(event.target.value as "core" | "dynamic" | "unassigned"); setRoleEvidence("");
        }}>
          <option value="unassigned">Unassigned — no role chosen</option><option value="core">Core — long-term</option>
          <option value="dynamic">Dynamic — experimental</option>
        </select><small className="field-help" id="record-role-help">Match the role of the money or units involved.
          Unassigned is not core or dynamic. Assigning a role requires choosing core or dynamic; it does not move money or approve trades.</small></label>}
      {kind === "role_assignment" && <label>What are you labelling?<select value={assignmentAsset}
        onChange={event => setAssignmentAsset(event.target.value as "cash" | "holding")}>
        <option value="cash">Cash</option><option value="holding">Holding quantity</option>
      </select></label>}
      {kind === "opening" && <>
        <fieldset><legend>Starting cash balances at the same date and time</legend>
          {session.accounts.map(account => <label key={account.id}>{account.name} ({label(account.kind)}) — GBP
            <input required inputMode="decimal" value={balances[account.id] ?? ""}
              onChange={event => setBalances(current => ({ ...current, [account.id]: event.target.value }))} />
          </label>)}
        </fieldset>
        <fieldset><legend>Investments you already own (add every holding)</legend>
          <p>For example, 12.5 units of a fund in an investment account is one holding.
            Add all your holdings, not just ones with known prices. Only the price may be left blank if unknown.</p>
          {openingHoldings.map((holding, index) => <div className="recording-row" key={index}>
            <label>Account<select required value={holding.account_id}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, account_id: event.target.value } : item))}>
              <option value="">Select</option>{session.accounts.filter(account => account.kind === "broker")
                .map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
            </select></label>
            <label>Investment<select required value={holding.product_id}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, product_id: event.target.value } : item))}>
              <option value="">Select</option>{session.products.map(product =>
                <option key={product.id} value={product.id}>{product.name} ({product.id})</option>)}
            </select></label>
            <label>Quantity<input required inputMode="decimal" value={holding.quantity}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, quantity: event.target.value } : item))} /></label>
            <label>GBP price per unit (leave blank if unknown)<input inputMode="decimal" value={holding.price_gbp}
              onChange={event => setOpeningHoldings(current => current.map((item, row) =>
                row === index ? { ...item, price_gbp: event.target.value } : item))} /></label>
            <button type="button" onClick={() => setOpeningHoldings(current =>
              current.filter((_, row) => row !== index))}>Remove holding</button>
          </div>)}
          <button type="button" onClick={() => setOpeningHoldings(current => [
            ...current, { account_id: "", product_id: "", quantity: "", price_gbp: "" },
          ])}>Add existing holding</button>
        </fieldset>
      </>}
      {kind === "profile_change" && <>
        <label>What do you want to change?<select value={profileAction} onChange={event =>
          setProfileAction(event.target.value as "add_product" | "rename" | "retire")}>
          <option value="add_product">Add an investment</option>
          <option value="rename">Rename an account or investment</option>
          <option value="retire">Stop using an account or investment (retire)</option>
        </select></label>
        {profileAction !== "add_product" && <>
          <label>Account or investment?<select value={profileTarget} onChange={event =>
            setProfileTarget(event.target.value as "account" | "product")}>
            <option value="account">Account</option><option value="product">Investment</option>
          </select></label>
          <label>Which account or investment?<select required value={profileEntityId}
            onChange={event => setProfileEntityId(event.target.value)}>
            <option value="">Select</option>
            {(profileTarget === "account" ? session.accounts : session.products).map(item =>
              <option key={item.id} value={item.id}>{item.name} ({item.id})</option>)}
          </select></label>
        </>}
        {profileAction === "add_product" && <label>Investment reference (unique ID)<input required maxLength={200}
          aria-label="Investment reference (unique ID)" aria-describedby="new-investment-reference"
          value={newProductId} onChange={event => setNewProductId(event.target.value)} />
          <small className="field-help" id="new-investment-reference">Choose a unique local reference, such as house-fund.
            This is not a ticker lookup. It cannot change later.</small></label>}
        {profileAction !== "retire" && <label>{profileAction === "add_product" ? "Investment name" : "New name"}
          <input required maxLength={200} value={profileName}
            onChange={event => setProfileName(event.target.value)} /></label>}
        {profileAction === "add_product" && <>
          <label>Investment type<select value={newProductSleeve} onChange={event =>
            setNewProductSleeve(event.target.value as "equity" | "bond")}>
            <option value="equity">Shares / equity fund</option><option value="bond">Bond / bond fund</option>
          </select></label>
          <label>Smallest quantity step<input required inputMode="decimal" value={newProductIncrement}
            aria-label="Smallest quantity step" aria-describedby="new-investment-step"
            onChange={event => setNewProductIncrement(event.target.value)} />
            <small className="field-help" id="new-investment-step">The unit step supported by your provider, not the quantity you own.
              Check its dealing rules for this investment and account. If unknown, pause rather than guessing.</small></label>
          <label>Minimum purchase (GBP)<input required inputMode="decimal" value={newProductMinimum}
            aria-label="Minimum purchase (GBP)" aria-describedby="new-investment-minimum"
            onChange={event => setNewProductMinimum(event.target.value)} />
            <small className="field-help" id="new-investment-minimum">The smallest permitted purchase value in pounds.
              Enter 0 only if your provider confirms no minimum. Only GBP-priced investments are supported.</small></label>
        </>}
        <p className="form-note">Names can change. Unique references, account links, currency, investment type and quantity rules cannot.
          Retirement needs zero cash and holdings; it keeps the old records rather than deleting them.</p>
      </>}
      {kind === "scope_addition" && <>
        <label>Account reference (unique ID)<input required maxLength={200} value={newAccountId}
          aria-label="Account reference (unique ID)" aria-describedby="new-account-reference"
          onChange={event => setNewAccountId(event.target.value)} />
          <small className="field-help" id="new-account-reference">Choose a unique local reference, such as house-savings. It cannot change later.</small></label>
        <label>Account name<input required maxLength={200} value={newAccountName}
          onChange={event => setNewAccountName(event.target.value)} /></label>
        <label>Account link nickname<input required maxLength={200} value={newAccountBinding}
          aria-label="Account link nickname" aria-describedby="new-account-binding"
          onChange={event => setNewAccountBinding(event.target.value)} />
          <small className="field-help" id="new-account-binding">A unique, fixed local alias linking records to this account.
            Use a nickname, never an account number, password or token. It does not connect to your provider.</small></label>
        <label>Account type<select value={newAccountKind} onChange={event =>
          {
            const next = event.target.value as "broker" | "deposit";
            setNewAccountKind(next);
            if (next === "deposit")
              setScopeHoldings(current => current.filter(holding => holding.existing));
          }}>
          <option value="broker">Investment / broker account</option><option value="deposit">Savings / deposit account</option>
        </select></label>
        <fieldset><legend>Cash in every account at the same date and time (GBP)</legend>
          {session.accounts.map(account => <label key={account.id}>{account.name} ({account.id})
            <input required inputMode="decimal" value={scopeBalances[account.id] ?? ""}
              onChange={event => setScopeBalances(current => ({
                ...current, [account.id]: event.target.value,
              }))} />
          </label>)}
          <label>{newAccountName || "New account"} balance
            <input required inputMode="decimal" value={newAccountCash}
              onChange={event => setNewAccountCash(event.target.value)} />
          </label>
        </fieldset>
        <fieldset><legend>All existing and new holdings with prices at the same time</legend>
          {scopeHoldings.map((holding, index) => <div className="recording-row" key={`${holding.account_id}-${holding.product_id}-${index}`}>
            <label>Account<select required disabled={holding.existing} value={holding.account_id}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, account_id: event.target.value } : item))}>
              {session.accounts.filter(account => account.kind === "broker").map(account =>
                <option key={account.id} value={account.id}>{account.name}</option>)}
              {newAccountId && newAccountKind === "broker" &&
                <option value={newAccountId}>{newAccountName || newAccountId}</option>}
            </select></label>
            <label>Investment<select required disabled={holding.existing} value={holding.product_id}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, product_id: event.target.value } : item))}>
              {session.products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}
            </select></label>
            <label>Quantity<input required readOnly={holding.existing} inputMode="decimal"
              value={holding.quantity} onChange={event => setScopeHoldings(current =>
                current.map((item, row) => row === index ? { ...item, quantity: event.target.value } : item))} /></label>
            <label>GBP price per unit<input required inputMode="decimal" value={holding.price_gbp}
              onChange={event => setScopeHoldings(current => current.map((item, row) =>
                row === index ? { ...item, price_gbp: event.target.value } : item))} /></label>
            {!holding.existing && <button type="button" onClick={() =>
              setScopeHoldings(current => current.filter((_, row) => row !== index))}>Remove holding</button>}
          </div>)}
          {newAccountKind === "broker" && <button type="button" disabled={!newAccountId || !session.products.length}
            onClick={() => setScopeHoldings(current => [
            ...current, { account_id: newAccountId, product_id: session.products[0]?.id ?? "",
              quantity: "", price_gbp: "", existing: false },
          ])}>Add new-account holding</button>}
        </fieldset>
        <p className="form-note">Add an existing account, not new money. Enter cash for every account and all holdings at one time, using sources available then. A valid starting value is required; do not guess dates or balances.</p>
      </>}
      {kind !== "opening" && kind !== "correction" && <label>Account<select required value={accountId}
        disabled={["savings_terms", "account_fee"].includes(kind) && updateTargetStatus === "matched"}
        onChange={event => {
          setAccountId(event.target.value); setRoleEvidence("");
          if (kind === "savings_terms") {
            setTermsRate(""); setTermsBasis(""); setTermsEffective(""); setTermsMaturity(""); setTermsNotice("");
            setSource(""); setEvidence(""); setEffectiveAt(""); setAvailableAt("");
          }
          if (event.target.value) setCashTargetStatus(null);
        }}>
        <option value="">Choose account</option>
        {session.accounts.filter(account => kind !== "savings_terms" || account.kind === "deposit").map(account => <option key={account.id} value={account.id}>{account.name} ({label(account.kind)}
          {session.accounts.filter(item => item.name === account.name && item.kind === account.kind).length > 1 && ` · ${account.id}`})</option>)}
      </select></label>}
      {(kind === "transfer" || kind === "correction" && correction?.type === "transfer") &&
        <label>Destination account<select required value={destinationAccountId}
        onChange={event => setDestinationAccountId(event.target.value)}>
        {session.accounts.filter(account => account.id !== accountId)
          .map(account => <option key={account.id} value={account.id}>{account.name}</option>)}
      </select></label>}
      {(["dividend", "buy", "sell", "mark", "split"].includes(kind) ||
        kind === "correction" && ["dividend", "buy", "sell", "mark", "split"].includes(recordType ?? "") ||
        kind === "role_assignment" && assignmentAsset === "holding") && <label>Investment<select required value={productId}
        onChange={event => { setProductId(event.target.value); setRoleEvidence(""); }}>
        <option value="">Choose investment</option>
        {session.products.map(product => <option key={product.id} value={product.id}>{product.name} ({product.id})</option>)}
      </select></label>}
      {(["contribution", "withdrawal", "interest", "dividend", "transfer", "account_fee", "cash_observation", "role_assignment"]
        .includes(kind) || kind === "correction" &&
        ["contribution", "withdrawal", "interest", "dividend", "transfer", "account_fee"].includes(recordType ?? "")) &&
        <label>{kind === "cash_observation" ? "Observed GBP balance" :
          kind === "correction" ? "Corrected GBP amount" :
          kind === "role_assignment" && assignmentAsset === "holding" ? "Number of units to label" :
          kind === "account_fee" ? "Charge amount (GBP)" : "GBP amount"}
          <input required inputMode="decimal"
            value={amount}
            onChange={event => setAmount(event.target.value)} />
        </label>}
      {kind === "cash_observation" && <label>Statement/source description<input required maxLength={200}
        value={source} onChange={event => setSource(event.target.value)} /></label>}
      {kind === "savings_terms" && <>
        <p className="form-note">Record only terms supported by your source. This replaces all displayed terms; blank optional fields become unknown, even if previously recorded. These facts do not accrue interest or establish eligibility.</p>
        <p className="form-note">Check and record earlier transactions across all tracked accounts first. Once appended, these terms can prevent earlier events in any account from being recorded.</p>
        <label>Annual rate (%) (optional)<input inputMode="decimal" value={termsRate}
          onChange={event => setTermsRate(event.target.value)} /></label>
        <label>Rate basis<select value={termsBasis} onChange={event => setTermsBasis(event.target.value)}>
          <option value="">Not recorded</option><option value="AER">AER</option><option value="gross">Gross</option>
        </select></label>
        <label>Terms effective from<input required type="date" value={termsEffective}
          onChange={event => setTermsEffective(event.target.value)} /></label>
        <label>Maturity date (optional)<input type="date" value={termsMaturity}
          onChange={event => setTermsMaturity(event.target.value)} /></label>
        <label>Notice days (optional)<input inputMode="numeric" value={termsNotice}
          onChange={event => setTermsNotice(event.target.value)} /></label>
        <label>Terms source<input required maxLength={200} value={source} onChange={event => setSource(event.target.value)} /></label>
      </>}
      {(["interest", "dividend"].includes(kind) || kind === "correction" &&
        ["interest", "dividend"].includes(recordType ?? "")) && <label>Why does this income belong to this role?
        <textarea required maxLength={200} value={roleEvidence}
          aria-label="Why does this income belong to this role?" aria-describedby="income-role-help"
          onChange={event => setRoleEvidence(event.target.value)} />
          <small className="field-help" id="income-role-help">Describe the source showing which cash or investment earned it.
            Income is not automatically assigned to another role.</small></label>}
      {["buy", "sell"].includes(kind) ||
        kind === "correction" && ["buy", "sell"].includes(recordType ?? "") ? <>
        <label>Quantity<input required inputMode="decimal" value={quantity}
          onChange={event => setQuantity(event.target.value)} /></label>
        <label>GBP price per unit<input required inputMode="decimal" value={price}
          onChange={event => setPrice(event.target.value)} /></label>
        <label>Recorded fee in GBP<input required inputMode="decimal" value={fee}
          onChange={event => setFee(event.target.value)} /></label>
        {selectedProduct && <small className="form-note">Smallest supported quantity step: {selectedProduct.quantity_increment}.
          Enter the actual units bought or sold; this step is a rule, not a suggested purchase size.</small>}
      </> : null}
      {(kind === "mark" || kind === "correction" && correction?.type === "mark") &&
        <label>GBP price per unit<input required inputMode="decimal" value={price}
        onChange={event => setPrice(event.target.value)} /></label>}
      {["buy", "sell", "mark"].includes(recordType ?? "") && <p className="form-note">
        Enter a verified GBP price per unit. For a trade, enter the actual fee; 0 only if you confirmed no fee.</p>}
      {(kind === "split" || kind === "correction" && recordType === "split") &&
        <label>Split ratio<input required inputMode="decimal" value={ratio}
          aria-label="Split ratio" aria-describedby="record-split-help"
          onChange={event => setRatio(event.target.value)} />
          <small className="field-help" id="record-split-help">New units for each old unit.
            For example, 2 means two new units for each old unit. Use the actual split notice, not this example.</small></label>}
      {(kind !== "profile_change" && kind !== "scope_addition") && <label>
        {kind === "correction" ? "Correction recorded at (local time)" : kind === "savings_terms" ? "Terms observed at (local time)" : "Applies at (local time)"}
        <input required type="datetime-local" value={effectiveAt}
          aria-describedby="record-date-help" onChange={event => setEffectiveAt(event.target.value)} />
      </label>}
      {(kind === "profile_change" || kind === "scope_addition") && <>
        <label>Applies and available at (local time)
          <input required type="datetime-local" value={effectiveAt}
            onChange={event => setEffectiveAt(event.target.value)} />
        </label>
        <p className="form-note">For this change, the date it applies and the date the source was available must be the same.
          Do not use this form to turn later information into earlier evidence.</p>
      </>}
      {kind === "correction" && correction && <p className="position-note">
        Correcting {label(correction.type)} {correction.id} from {timestamp(correction.timestamp)}. The original stays in the journal; this correction is replayed through later records and makes past performance unavailable. No new starting value is created.
      </p>}
      {kind !== "profile_change" && kind !== "scope_addition" && <label>Source available from (local time)
        <input required type="datetime-local" value={availableAt}
          onChange={event => setAvailableAt(event.target.value)} />
      </label>}
      <p className="form-note" id="record-date-help">Times use {Intl.DateTimeFormat().resolvedOptions().timeZone}; the preview also shows UTC.
        {kind === "correction"
          ? " The original transaction date stays. Enter when the correction applies and when its evidence became available; availability cannot be earlier. Describe older supporting sources below."
          : kind === "savings_terms" ? " Terms observed at is when you observed the terms; the separate effective date may be in the future. Source available from cannot precede observation."
          : ' "Applies at" is when it happened or the value applied. "Source available from" is when the information first existed—not when entered—and may be later.'}
        {" "}Use dates supported by your source.</p>
      <label>Source description / correction reason<textarea required maxLength={200}
        aria-label="Source description / correction reason" aria-describedby="record-source-help"
        value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      <p className="form-note" id="record-source-help">Name the source and what it covers; do not include account numbers, credentials or full statements. For a correction, explain the error and supporting source.</p>
      <button type="submit" disabled={busy}>Preview record</button>
      </>}
    </form>}
    {message && <p role="status">{message}</p>}
  </section>;
}

type ReviewCheckpoint = {
  portfolioId: string;
  valuationTimestamp: string;
  savedAt: string;
  journalRevision: number;
};

type ManualCheckpointSave =
  | { portfolioId: string; status: "saving" | "saved" }
  | { portfolioId: string; status: "failed"; error: string };

let manualCheckpointSave: ManualCheckpointSave | null = null;
const manualCheckpointSubscribers = new Set<() => void>();

function subscribeManualCheckpoint(listener: () => void) {
  manualCheckpointSubscribers.add(listener);
  return () => { manualCheckpointSubscribers.delete(listener); };
}

function updateManualCheckpoint(state: ManualCheckpointSave | null) {
  manualCheckpointSave = state;
  for (const listener of manualCheckpointSubscribers) listener();
}

function currentManualCheckpoint() {
  return manualCheckpointSave;
}

function readReviewCheckpoint(portfolioId: string): ReviewCheckpoint | null {
  const raw = localStorage.getItem(`goldscout.review-checkpoint.v1.${portfolioId}`);
  if (raw === null) return null;
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("The saved review checkpoint is invalid; it was not changed.");
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join(",") !==
    "journalRevision,portfolioId,savedAt,valuationTimestamp")
    throw new Error("The saved review checkpoint is invalid; it was not changed.");
  if (item.portfolioId !== portfolioId || typeof item.valuationTimestamp !== "string" ||
    !Number.isFinite(Date.parse(item.valuationTimestamp)) || typeof item.savedAt !== "string" ||
    !Number.isFinite(Date.parse(item.savedAt)) || !Number.isSafeInteger(item.journalRevision) ||
    (item.journalRevision as number) < 0)
    throw new Error("The saved review checkpoint is invalid; it was not changed.");
  return {
    portfolioId, valuationTimestamp: item.valuationTimestamp,
    savedAt: item.savedAt, journalRevision: item.journalRevision as number,
  };
}

function sameReviewCheckpoint(left: ReviewCheckpoint | null, right: ReviewCheckpoint | null) {
  if (!left || !right) return left === right;
  return left.portfolioId === right.portfolioId && left.valuationTimestamp === right.valuationTimestamp &&
    left.savedAt === right.savedAt && left.journalRevision === right.journalRevision;
}

function PortfolioGoals({ data }: { data: InvestmentSnapshot }) {
  const query = useQuery({ queryKey: ["planning-summary"], queryFn: planningSession, refetchOnMount: "always" });
  const goals = query.data?.goals ?? [];
  const values = recordedAccountValues(data);
  const duplicates = duplicateAccountLinks(goals);
  if (!query.isPending && !query.isError && (!query.data?.enabled || goals.length === 0)) return null;
  return <section className="history-group" aria-label="Your goals">
    <h2>Your goals</h2>
    {query.isPending ? <p role="status">Loading your goals.</p>
      : query.isError ? <p role="status">Your goals could not be loaded. <NavLink to="/planning">Open goals to retry</NavLink>.</p>
      : <>
        <ul>{goals.map(goal => {
          const linked = goal.linked_account_ids.length > 0;
          const amount = linked ? linkedGoalValue(goal, data, values, duplicates)?.toString() ?? null
            : goal.current_amount_gbp;
          return <li key={goal.id}><strong>{goal.name}</strong>
            {" · "}{goal.target_amount_gbp === null ? "Target not set" : `Target ${pounds(goal.target_amount_gbp, true)}`}
            {goal.target_date && ` by ${goal.target_date}`}
            {" · "}{amount === null ? (linked ? "Linked amount unavailable" : "Amount not set")
              : `${linked ? "Latest linked account records" : "User-entered amount"}: ${pounds(amount, true)}`}
          </li>;
        })}</ul>
        <p className="position-note">Linked amounts use latest account records, which may have different dates and may not match the portfolio valuation date. They do not reserve money.</p>
        <NavLink to="/planning">Review goals and contribution illustrations</NavLink>
      </>}
  </section>;
}

function PortfolioRecoveryTools({ available }: { available: boolean }) {
  const [mounted, setMounted] = useState(false);
  return <details className="investment-detail recovery-tools" hidden={!available}
    id="investment-backup" onToggle={event => {
      if (event.currentTarget.open) setMounted(true);
    }}>
    <summary>Backup and recovery tools</summary>
    {mounted && <RecoveryPanel />}
  </details>;
}

export function InvestmentView({ now, valueChangeSelection, setValueChangeSelection,
  reviewDefaults, reviewDefaultsStatus }: {
  now: number; valueChangeSelection: ValueChangeSelection | null;
  setValueChangeSelection: (selection: ValueChangeSelection | null) => void;
  reviewDefaults: { reviewIntervalDays: number; termsIntervalDays: number } | null;
  reviewDefaultsStatus: "loading" | "unavailable" | "ready";
}) {
  const query = useQuery({ queryKey: ["investment"], queryFn: ({ signal }) => loadInvestment(signal), refetchInterval: 60000 });
  const location = useLocation();
  const navigate = useNavigate();
  const activeView = portfolioView(location.search);
  const [toolsVisited, setToolsVisited] = useState(activeView === "tools");
  const data = query.isError ? undefined : query.data;
  const recoveryPortfolioId = query.data?.source_mode === "recorded" ? query.data.portfolio_id : null;
  const hasData = Boolean(data);
  const manualSave = useSyncExternalStore(subscribeManualCheckpoint, currentManualCheckpoint, () => null);
  const checkpointSaving = manualSave?.status === "saving";
  const manualSaveError = data?.source_mode === "recorded" && manualSave?.status === "failed" &&
    manualSave.portfolioId === data.portfolio_id ? manualSave.error : "";
  const [checkpoint, setCheckpoint] = useState<ReviewCheckpoint | null>(null);
  const [checkpointError, setCheckpointError] = useState("");
  const comparisonError = checkpointError || manualSaveError;
  useEffect(() => {
    if (activeView === "tools") setToolsVisited(true);
  }, [activeView]);
  useEffect(() => {
    if (!data || data.source_mode !== "recorded") {
      setCheckpoint(null);
      setCheckpointError("");
      return;
    }
    const portfolioId = data.portfolio_id;
    const key = `goldscout.review-checkpoint.v1.${portfolioId}`;
    function loadCheckpoint() {
      try {
        setCheckpoint(readReviewCheckpoint(portfolioId));
        setCheckpointError("");
      } catch {
        setCheckpoint(null);
        setCheckpointError("The saved comparison point could not be read. It was not changed; check this browser's saved data before continuing.");
      }
    }
    function onStorage(event: StorageEvent) {
      if (event.key === null || event.key === key) loadCheckpoint();
    }
    loadCheckpoint();
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [data?.portfolio_id, data?.source_mode]);

  useEffect(() => {
    if (!data || data.source_mode !== "recorded" || manualSave?.portfolioId !== data.portfolio_id) return;
    if (manualSave.status === "saved") {
      try {
        setCheckpoint(readReviewCheckpoint(data.portfolio_id));
        setCheckpointError("");
      } catch {
        setCheckpoint(null);
        setCheckpointError("The saved comparison point could not be read. It was not changed; check this browser's saved data before continuing.");
      }
    }
  }, [data?.portfolio_id, data?.source_mode, manualSave]);

  function persistReviewCheckpoint(): ReviewCheckpoint | string {
    if (checkpointError) return checkpointError;
    if (!data || data.source_mode !== "recorded" || !data.complete || !data.as_of)
      return "A complete recorded portfolio is required to save this comparison point. No journal record was changed.";
    const outcome = manualCheckpointSave;
    if (outcome?.status === "failed" && outcome.portfolioId === data.portfolio_id) return outcome.error;
    let current: ReviewCheckpoint | null;
    try {
      current = readReviewCheckpoint(data.portfolio_id);
    } catch {
      return "The saved comparison point could not be read. It was not changed; check this browser's saved data before continuing.";
    }
    if (!sameReviewCheckpoint(current, checkpoint)) {
      return "The saved comparison point changed since this page loaded. Retry it before saving. No journal record was changed.";
    }
    const saved: ReviewCheckpoint = {
      portfolioId: data.portfolio_id, valuationTimestamp: data.as_of,
      savedAt: new Date().toISOString(), journalRevision: data.revision,
    };
    try {
      localStorage.setItem(`goldscout.review-checkpoint.v1.${data.portfolio_id}`,
        JSON.stringify(saved));
      return saved;
    } catch {
      return "The browser could not save this review checkpoint. No journal record was changed.";
    }
  }
  function applyCheckpointResult(result: ReviewCheckpoint | string) {
    if (typeof result === "string") {
      setCheckpointError(result);
      return false;
    }
    setCheckpoint(result);
    setCheckpointError("");
    setValueChangeSelection(null);
    return true;
  }
  function markReviewed() {
    return applyCheckpointResult(persistReviewCheckpoint());
  }
  async function saveCheckpointManually() {
    if (!data || data.source_mode !== "recorded" ||
        !data.complete || !data.as_of || comparisonError) return;
    if (manualCheckpointSave?.status === "saving" ||
        (manualCheckpointSave?.status === "failed" && manualCheckpointSave.portfolioId === data.portfolio_id)) return;
    const portfolioId = data.portfolio_id;
    updateManualCheckpoint({ portfolioId, status: "saving" });
    try {
      if (!navigator.locks) throw new Error("This browser cannot coordinate comparison points across tabs.");
      const result = await navigator.locks.request("goldscout-recording-confirmation", async () => {
        const latest = await loadInvestment(new AbortController().signal);
        if (latest.source_mode !== "recorded" || latest.portfolio_id !== portfolioId ||
            latest.revision !== data.revision)
          throw new Error("The journal changed. Refresh the portfolio before saving this comparison point.");
        return persistReviewCheckpoint();
      });
      if (typeof result !== "string") applyCheckpointResult(result);
      updateManualCheckpoint(typeof result === "string"
        ? { portfolioId, status: "failed", error: result }
        : { portfolioId, status: "saved" });
    } catch (reason) {
      const error = `${reason instanceof Error ? reason.message : "The comparison point could not be saved."} No journal record was changed.`;
      updateManualCheckpoint({ portfolioId, status: "failed", error });
    }
  }
  function retryCheckpoint() {
    if (!data || data.source_mode !== "recorded") return;
    try {
      setCheckpoint(readReviewCheckpoint(data.portfolio_id));
      setCheckpointError("");
      if (manualCheckpointSave?.status === "failed" && manualCheckpointSave.portfolioId === data.portfolio_id)
        updateManualCheckpoint(null);
    } catch {
      setCheckpointError("The saved comparison point could not be read. It was not changed; check this browser's saved data before continuing.");
    }
  }
  useEffect(() => {
    if (!location.hash) return;
    const targetView = portfolioFragmentView(location.hash);
    if (targetView && targetView !== activeView) {
      navigate(portfolioViewLocation(targetView, location.hash), { replace: true });
      return;
    }
    const target = document.getElementById(location.hash.slice(1));
    if (!target) return;
    if (target instanceof HTMLDetailsElement) target.open = true;
    for (let parent = target.parentElement; parent; parent = parent.parentElement) {
      if (parent instanceof HTMLDetailsElement) parent.open = true;
    }
    target.scrollIntoView();
  }, [activeView, hasData, data?.portfolio_id, data?.revision, data?.source_mode,
    location.hash, location.key, navigate]);
  const metricValues = data ? [
    pounds(data.equity_gbp),
    pounds(data.net_contributions_gbp),
    pounds(data.since_baseline_gain_gbp),
  ] : [];
  return <article className="investment-view">
    <div className="record-toolbar">
      {data?.source_mode === "recorded" && <NavLink className="record-action secondary-record-action" to="/investment/record">Add a record</NavLink>}
      <span className="refresh-note">Refresh reloads the selected portfolio snapshot; it does not fetch provider balances.</span>
      <button className="icon-button refresh" aria-label="Refresh investment"
        title="Reload the selected portfolio snapshot; no provider balances are fetched"
        disabled={query.isFetching} onClick={() => void query.refetch()}>
        <RefreshCw size={19} className={query.isFetching ? "spin" : ""} />
      </button>
    </div>
    <nav className="portfolio-view-nav" aria-label="Portfolio views">
      <Link to="/investment" aria-current={activeView === "overview" ? "page" : undefined}>Overview</Link>
      <Link to="/investment?view=history" aria-current={activeView === "history" ? "page" : undefined}>History and insights</Link>
      <Link to="/investment?view=tools" aria-current={activeView === "tools" ? "page" : undefined}>Tools</Link>
    </nav>
    {query.isPending ? <p role="status">Loading investment records</p>
      : !data ? <div className="error-state" role="alert"><ShieldAlert size={30} /><h2>Investment data unavailable</h2>
        <p>{query.error instanceof Error ? query.error.message : "The investment source could not be reached."}</p>
        <button type="button" className="error-retry" disabled={query.isFetching}
          onClick={() => void query.refetch()}>{query.isFetching ? "Retrying..." : "Retry loading records"}</button></div>
        : <>
          <div className="demo-band"><span>{data.source_mode === "demo" ? "SYNTHETIC DEMO" : "MANUAL RECORDS"}</span>
            <span>{data.source_mode === "demo"
              ? "Examples, not your accounts."
              : "Entered by you; not independently verified."}</span></div>
          <section id="investment-overview" aria-label="Valuation evidence">
            <p className="position-note">Valuation recorded {timestamp(data.as_of)}
              {data.as_of !== null && <> (age: {evidenceAge(data.as_of, now)})</>}.
              {" "}{data.complete ? "Complete for that date; not live." : "Incomplete; full total unavailable."}</p>
          </section>
          {comparisonError && <div>
            {checkpointError && <p role="alert">{checkpointError}</p>}
            {manualSaveError && manualSaveError !== checkpointError && <p role="alert">{manualSaveError}</p>}
            <button type="button" onClick={retryCheckpoint}>Retry comparison point</button>
          </div>}
          <div hidden={activeView !== "overview"}>
            <section className="metrics portfolio-overview-metric" aria-label="Investment totals">
              <div><span>Portfolio value</span><strong className={metricValues[0].length <= 12 ? "metric-amount-nowrap" : undefined}>{metricValues[0]}</strong><small>GBP · recorded value</small></div>
            </section>
            <InvestmentNextSteps data={data} />
            <PerformanceLimitations data={data} compact />
            {data.source_mode === "recorded" ? <AccountMaintenance data={data} now={now} mode="overview"
              defaults={reviewDefaults} defaultsStatus={reviewDefaultsStatus}
              onFinishReview={markReviewed}
              refreshSnapshot={() => loadInvestment(new AbortController().signal)} />
              : <section id="account-review" className="history-group" aria-label="Example accounts">
                <h2>Accounts</h2>
                <p className="position-note">These are synthetic examples, not your accounts.</p>
                {data.accounts.filter(account => account.status === "active").map(account =>
                  <article className="account-maintenance-card account-overview-row" key={account.id}>
                    <div className="account-maintenance-heading"><h3>{accountDisplay(data, account.id)}</h3>
                      <span>{account.kind === "deposit" ? "Savings account" : "Investment account"}</span></div>
                    <NavLink className="text-link" to="/investment?view=history#investment-account-summary">
                      View recorded evidence
                    </NavLink>
                  </article>)}
                {!data.accounts.some(account => account.status === "active") && <p>No active example accounts.</p>}
              </section>}
            {data.source_mode === "recorded" && <PortfolioGoals data={data} />}
            <RecentActivity data={data} compact />
          </div>
        </>}
    {data && <div hidden={activeView !== "history"}>
          <section className="history-group">
            <h2>Recorded money and gain</h2>
            <section className="metrics" aria-label="Recorded contribution and gain">
              <div><span>Net money added</span><strong className={metricValues[1].length <= 12 ? "metric-amount-nowrap" : undefined}>{metricValues[1]}</strong><small>Deposits less withdrawals</small></div>
              <div><span>Gain since tracking began</span><strong className={metricValues[2].length <= 12 ? "metric-amount-nowrap" : undefined}>{metricValues[2]}</strong><small>Net of recorded fees</small></div>
            </section>
            <p className="position-note">Net money added is not investment gain. Missing history is unavailable, not zero.</p>
            <details className="help-disclosure"><summary>About these figures and dates</summary>
              <p>Net money added is deposits less withdrawals; transfers between tracked accounts do not count. It is not investment gain.</p>
              <p>Gain since tracking began subtracts recorded net flows and newly included account value from the value change. It reflects only activity and charges entered in the journal.</p>
              <p>Percentage return is separate and needs complete evidence at relevant dates. Missing history is unavailable, not zero.</p>
            </details>
          </section>
          {comparisonError
            ? <p role="status">The value comparison is unavailable while the saved comparison point needs attention.</p>
            : <RecordedValueChanges data={data} selection={valueChangeSelection}
              onSelect={setValueChangeSelection}
              checkpointTimestamp={checkpoint?.portfolioId === data.portfolio_id
                ? checkpoint.valuationTimestamp : null} />}
          <section className="history-group review-checkpoint" aria-label="Review continuity">
            <h2>Save a value for next time</h2>
            <p className="position-note">This saves the complete portfolio value's date and journal revision in this browser only.
              It replaces the previous comparison point; it does not mark accounts checked or verify their balances.</p>
            {checkpoint && <p>Saved {timestamp(checkpoint.savedAt)} at journal revision {checkpoint.journalRevision}.
              {" "}The saved comparison point is the complete recorded value dated {timestamp(checkpoint.valuationTimestamp)}.
              {checkpoint.journalRevision !== data.revision && " Journal records have changed since that checkpoint."}</p>}
            {!checkpoint && !comparisonError && <p>No comparison point is saved in this browser yet.</p>}
            <button type="button" disabled={data.source_mode !== "recorded" || !data.complete || !data.as_of ||
              Boolean(comparisonError) || checkpointSaving}
              onClick={() => void saveCheckpointManually()}>Save this value for the next comparison</button>
            {checkpointSaving && <p role="status">Saving this comparison point.</p>}
            {(!data.complete || !data.as_of) && <p role="status">A comparison point is unavailable until a complete recorded value exists.</p>}
          </section>
          <details className="investment-detail">
            <summary>Accounts and recorded holdings</summary>
            <AccountAndHoldingsSummary data={data} now={now} />
          </details>
          <details className="investment-detail">
            <summary>Recent recorded activity</summary>
            <RecentActivity data={data} />
          </details>
          <section className="history-group">
            <h2>Performance summary</h2>
            <dl className="health-facts">
              <div><dt>Return since tracking began</dt><dd>{percent(data.twr_pct)}</dd></div>
              <div><dt>Largest recorded fall</dt><dd>{percent(data.max_drawdown_pct)}</dd></div>
            </dl>
            <PerformanceLimitations data={data} />
            <details className="help-disclosure"><summary>About these figures</summary>
              <p>Returns adjust for recorded deposits and withdrawals and need complete values at key dates. Missing is not zero; later prices cannot fill past gaps.</p>
              <p>Past results do not predict future results.</p>
            </details>
          </section>
          <details className="investment-detail" id="investment-performance">
            <summary>Detailed growth history</summary>
            <InvestmentGrowthHistory history={data.valuation_history} />
          </details>
          <details className="investment-detail" id="investment-roles">
            <summary>Optional fund labels</summary>
          <section className="history-group" aria-label="Management role accounting">
            <h2>Management roles</h2>
            <p>These labels separate long-term funds (core) from experimental funds (dynamic). Unassigned means you have not chosen a role.
              They are not recommended allocations or risk limits.</p>
            <p className="position-note">{data.source_mode === "demo"
              ? "Synthetic role attribution is shown only to demonstrate accounting. It is not a target, approved allocation, or recommendation."
              : "Roles are operator-attributed independently of account and asset class."} Unassigned funds are not assumed to be core or dynamic.</p>
            <p className="position-note">Recorded role balances reflect the latest journal, not necessarily the portfolio valuation clock. Latest role change: {timestamp(data.role_accounting_at, "None")}.</p>
            <div className="table-scroll" tabIndex={0}><table>
              <thead><tr><th>Role</th><th>Cash</th><th>Recorded value</th></tr></thead>
              <tbody>{data.role_accounting.map(item => <tr key={item.role}><td>{label(item.role)}</td>
                <td>{pounds(item.cash_gbp)}</td><td>{pounds(item.value_gbp)}</td></tr>)}</tbody>
            </table></div>
            {data.role_accounting.map(item => <details key={item.role}>
              <summary>{label(item.role)} by account and holding</summary>
              <ul>
                {item.accounts.map(account => <li key={account.account_id}>
                  {accountReference(data, account.account_id)}: {pounds(account.cash_gbp)} recorded cash
                </li>)}
                {item.holdings.map(holding => <li key={`${holding.account_id}:${holding.product_id}`}>
                  {accountReference(data, holding.account_id)} · {productReference(data, holding.product_id)}: {holding.quantity} units
                </li>)}
              </ul>
            </details>)}
            {data.role_assignments.length > 0 && <details className="investment-detail">
              <summary>Role attribution history ({data.role_assignments.length})</summary>
              <div className="table-scroll" tabIndex={0} aria-label="Role attribution history">
              <table><thead><tr><th>Attribution clock</th><th>Role</th><th>Account</th><th>Asset</th><th>Amount / quantity</th><th>Evidence</th></tr></thead>
                <tbody>{data.role_assignments.slice(-100).map(item => <tr key={item.id}>
                  <td>{timestamp(item.timestamp)}</td><td>{label(item.role)}</td><td>{accountReference(data, item.account_id)}</td>
                  <td>{productReference(data, item.product_id)}</td><td>{item.amount}</td><td>{item.evidence}</td>
                </tr>)}</tbody></table>
              </div>
            </details>}
          </section>
          </details>
          <details className="investment-detail" id="investment-records">
            <summary>Journal records</summary>
          <section className="history-group" aria-label="Recorded fees and income">
            <h2>Recorded fees and income</h2>
            <p className="position-note">The total includes explicitly recorded trade fees and account/platform charges, including any before the baseline. It is not an all-in cost total: fund charges inside prices, tax, FX and other costs may be missing; spread or slippage are not separately measured.</p>
            <dl className="health-facts">
              <div><dt>Recorded trade and account charges</dt><dd>{pounds(data.recorded_fees_gbp)}</dd></div>
              <div><dt>Recorded interest</dt><dd>{pounds(data.recorded_interest_gbp)}</dd></div>
              <div><dt>Recorded dividends</dt><dd>{pounds(data.recorded_dividends_gbp)}</dd></div>
              <div><dt>Recorded income total</dt><dd>{pounds(data.recorded_income_gbp)}</dd></div>
            </dl>
            {data.recorded_fee_events.length > 0 && <>
              <h3>Recorded fill events</h3>
              <p className="position-note">Showing the latest {Math.min(data.recorded_fee_events.length, 100)} journal entries in original order.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded fill fee history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Side</th><th>Role</th><th>Account</th><th>Product</th><th>Quantity</th><th>Price</th><th>Fee</th><th>Available at</th><th>Evidence</th></tr></thead>
                  <tbody>{data.recorded_fee_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{accountReference(data, event.account_id)}</td>
                      <td>{productReference(data, event.product_id)}</td><td>{event.quantity}</td>
                      <td>{pounds(event.price_gbp)}</td><td>{pounds(event.fee_gbp)}</td>
                      <td>{timestamp(event.available_at)}</td><td>{event.evidence}</td></tr>)}</tbody>
                </table>
              </div>
            </>}
            {data.recorded_account_fees.length > 0 && <>
              <h3>Recorded account and platform charges</h3>
              <p className="position-note">Showing the latest {Math.min(data.recorded_account_fees.length, 100)} journal entries in original order.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded account charge history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Role</th><th>Account</th><th>Amount</th><th>Available at</th><th>Evidence</th></tr></thead>
                  <tbody>{data.recorded_account_fees.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.role)}</td>
                      <td>{accountReference(data, event.account_id)}</td><td>{pounds(event.amount_gbp)}</td>
                      <td>{timestamp(event.available_at)}</td><td>{event.evidence}</td></tr>)}</tbody>
                </table>
              </div>
            </>}
            {data.recorded_income_events.length > 0 && <>
              <h3>Recorded income events</h3>
              <p className="position-note">Showing the latest {Math.min(data.recorded_income_events.length, 100)} journal entries in original order.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded income history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Type</th><th>Role</th><th>Account</th><th>Product</th><th>Amount</th><th>Available at</th><th>Evidence</th><th>Role allocation evidence</th></tr></thead>
                  <tbody>{data.recorded_income_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{accountReference(data, event.account_id)}</td>
                      <td>{event.product_id === null ? "Not applicable" : productReference(data, event.product_id)}</td>
                      <td>{pounds(event.amount_gbp)}</td><td>{timestamp(event.available_at)}</td>
                      <td>{event.evidence}</td><td>{event.role_evidence ?? "Not recorded"}</td></tr>)}</tbody>
                </table>
              </div>
            </>}
          </section>
          <p className="position-note">Baseline as of {timestamp(data.baseline_as_of)}. Lifetime gain and prior cost basis are unavailable. No valuation freshness threshold is configured.</p>
          {data.recorded_flow_events.length > 0 &&
            <section className="history-group" aria-label="Recorded capital flows">
              <h2>Recorded contributions, withdrawals and transfers</h2>
              <p className="position-note">Showing the latest {Math.min(data.recorded_flow_events.length, 100)} of {data.recorded_flow_events.length} journal entries in original order. These are operator-recorded events, not independently verified movements. Transfers between included accounts do not change net external contributions.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Recorded capital flow history">
                <table>
                  <thead><tr><th>Effective at (UTC)</th><th>Type</th><th>Role</th><th>Account</th><th>Other side</th><th>Recorded amount</th><th>Available at</th><th>Evidence</th></tr></thead>
                  <tbody>{data.recorded_flow_events.slice(-100).map(event =>
                    <tr key={event.id}><td>{timestamp(event.timestamp)}</td><td>{label(event.type)}</td>
                      <td>{label(event.role)}</td><td>{accountReference(data, event.account_id)}</td>
                      <td>{event.destination_account_id === null
                        ? (event.type === "contribution" ? "External source" : "External destination")
                        : accountReference(data, event.destination_account_id)}
                      </td>
                      <td>{pounds(event.amount_gbp)}</td><td>{timestamp(event.available_at)}</td>
                      <td>{event.evidence}</td></tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.recorded_corrections.length > 0 &&
            <section className="history-group" id="investment-corrections" aria-label="Evidence-backed corrections">
              <h2>Historical corrections</h2>
              <p className="position-note">Corrections append new evidence and replay downstream accounting. A correction is refused if the revised event breaks later accounting. Any historical correction makes performance unavailable; the system never creates a replacement baseline automatically.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Investment correction history">
                <table>                <thead><tr><th>Correction recorded at (UTC)</th><th>Record type / account</th><th>Role</th><th>Original amount</th><th>Replacement amount</th><th>Evidence available at</th><th>Reason and evidence</th><th>Role allocation evidence</th></tr></thead>
                  <tbody>{data.recorded_corrections.slice(-100).map(item =>
                    <tr key={item.id}><td>{item.timestamp}</td>
                      <td>{label(item.replaced_type)}{item.account_id ? ` · ${accountReference(data, item.account_id)}` : ""}</td>
                      <td>{label(item.role)}</td>
                      <td>{pounds(item.original_amount_gbp)}</td>
                      <td>{pounds(item.replacement_amount_gbp)}</td>
                      <td>{item.available_at}</td><td>{item.evidence}</td><td>{item.role_evidence ?? "Not recorded"}</td></tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.recorded_profile_changes.length > 0 &&
            <section className="history-group" aria-label="Profile changes and scope additions">
              <h2>Account and product history</h2>
              <p className="position-note">Names can change, while account/product identities and financial semantics remain fixed. Retired entries remain visible. Funded account scope additions are unitization-neutral and are not external contributions.</p>
              <p className="position-note">Recorded scope additions: {pounds(data.recorded_scope_additions_gbp)}</p>
              <ul>{data.recorded_profile_changes.slice(-100).map(item =>
                <li key={item.id}>{label(item.action)} · {label(item.target)} {item.name
                  ? `${item.name} (${item.entity_id})`
                  : item.target === "account" ? accountReference(data, item.entity_id)
                    : productReference(data, item.entity_id)}
                  at {timestamp(item.timestamp)}; available at {timestamp(item.available_at)}. {item.evidence}</li>)}</ul>
              {data.recorded_scope_additions.length > 0 &&
                <ul aria-label="Unitization-neutral scope additions">
                  {data.recorded_scope_additions.slice(-100).map(item =>
                    <li key={item.id}>{accountReference(data, item.account_id)}: {pounds(item.amount_gbp)} at {timestamp(item.timestamp)}; {item.evidence}</li>)}
                </ul>}
            </section>}
          </details>
          <details className="investment-detail" id="investment-accounts">
            <summary>Cash observations and savings details</summary>
          <p className="position-note">Savings are separate from investment-account buying power. This journal does not verify deposit protection or account eligibility.</p>
          <CashEvidenceOverview data={data} now={now} />
          {data.accounts.some(account => account.cash_observation !== null) &&
            <section className="history-group"><h2>Observed cash balances</h2>
              <p className="position-note">Operator-entered evidence, not provider verification. Differences compare with recorded cash at that timestamp; they are not corrections or proof of a later balance.</p>
              <p className="position-note">Showing the latest {Math.min(data.cash_observations.length, 100)} of {data.cash_observations.length} recorded observations.</p>
              <div className="table-scroll" tabIndex={0} aria-label="Cash reconciliation history">
                <table>
                  <thead><tr><th>Observed at (UTC)</th><th>Account</th><th>Balance</th><th>Difference (observed minus recorded)</th><th>Available at</th><th>Source and evidence</th></tr></thead>
                  <tbody>{[...data.cash_observations].reverse().slice(0, 100).map((observation, index) =>
                    <tr key={`${observation.account_id}:${observation.timestamp}:${index}`}>
                      <td>{observation.timestamp}</td><td>{accountReference(data, observation.account_id)}</td>
                      <td>{pounds(observation.balance_gbp, true)}</td><td>{pounds(observation.difference_gbp, true)}</td>
                      <td>{observation.available_at}</td>
                      <td>{observation.source} · {observation.evidence}</td>
                    </tr>)}</tbody>
                </table>
              </div>
            </section>}
          {data.accounts.some(account => account.terms !== null) &&
            <section className="history-group"><h2>Reported savings-account terms</h2>
              <p className="position-note">Operator-recorded facts, not current provider verification or an interest-accrual input.</p>
              <dl className="health-facts">{data.accounts.filter(account => account.terms !== null).map(account => {
                const terms = account.terms;
                if (terms === null) return null;
                return <div key={account.id}><dt>{account.name}</dt><dd>
                  {terms.annual_rate_pct === null ? "Rate unavailable" : `${percent(terms.annual_rate_pct)} ${terms.rate_basis}`}
                  {` · Effective ${terms.effective_from} · Observed ${timestamp(terms.observed_at)} · Available ${timestamp(terms.available_at)} · Source: ${terms.source}`}
                  {terms.maturity_date !== null && ` · Maturity ${terms.maturity_date}`}
                  {terms.notice_days !== null && ` · Notice ${terms.notice_days} days`}
                </dd></div>;
              })}</dl>
            </section>}
          </details>
        </div>}
    {(toolsVisited || activeView === "tools") && <div hidden={activeView !== "tools"}>
      <p className="position-note">Optional tools are separate from your portfolio records. Opening this page does not collect provider data or start a backup.</p>
      {data && <>
        <details className="investment-detail" id="investment-monitoring">
          <summary>Optional checks</summary>
          <PortfolioMonitoring portfolioId={data.portfolio_id} revision={data.revision} />
        </details>
        <details className="investment-detail" id="investment-awareness">
          <summary>Optional public context</summary>
          <PortfolioAwareness portfolioId={data.portfolio_id} revision={data.revision} />
        </details>
      </>}
      {recoveryPortfolioId && <PortfolioRecoveryTools key={recoveryPortfolioId}
        available={data?.source_mode === "recorded"} />}
      <EToroPortfolioPanel journal={data} now={now} viewActive={activeView === "tools"} />
    </div>}
  </article>;
}

export function InvestmentRecordingPage() {
  const { state } = useLocation();
  return <article className="investment-view">
    {state?.recordSaved && <p role="status">Previous record {state.recordSavedId} was saved. {state.reviewNotice}</p>}
    <div className="record-toolbar">
      <NavLink className="text-link" to="/investment">Back to investment overview</NavLink>
    </div>
    <section className="investment-boundary" aria-label="Manual recording boundary">
      <ShieldAlert size={18} />
      <p>Enter completed transactions or source figures, then review the preview before saving. Entries are not independently verified; this page cannot place trades or move money.</p>
    </section>
    <InvestmentRecording />
  </article>;
}

export function InvestmentGuide() {
  const { hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    const target = document.getElementById(hash.slice(1));
    if (target instanceof HTMLDetailsElement) target.open = true;
    target?.scrollIntoView();
  }, [hash]);
  return <article className="trading-guide">
    <p className="guide-intro">Your portfolio shows records you enter, not live account balances. GoldScout cannot move money or place trades.</p>
    <section><h2>Common tasks</h2>
      <ul>
        <li><NavLink to="/investment#investment-account-summary">See your accounts and investments</NavLink>.</li>
        <li><NavLink to="/investment/record">Record a completed transaction</NavLink>.</li>
        <li><NavLink to="/investment-guide#recording-prices">Add a checked investment price</NavLink>.</li>
        <li><NavLink to="/investment-guide#performance-gaps">Understand a missing value or return</NavLink>.</li>
        <li><NavLink to="/investment-guide#monitoring-setup">Learn about optional checks</NavLink> or
          {" "}<NavLink to="/investment-guide#backup-recovery">back up your records</NavLink>.</li>
      </ul>
    </section>
    <details className="help-disclosure" id="recording-prices"><summary>Add a checked investment price</summary>
      <p>A price record does not change cash or units. Confirm the price and source before saving.</p>
      <ol>
        <li>Open <NavLink to="/investment/record">Add a record</NavLink> and choose <strong>Update an investment price</strong>.</li>
        <li>Enter the checked price in pounds, when it applied, when the source became available, and where you checked it.</li>
        <li>Review the investment, price, dates and source in the preview before saving.</li>
      </ol>
    </details>
    <details className="help-disclosure" id="performance-gaps"><summary>When a value or return is missing</summary>
      <p>A complete value needs prices for every holding at the same time. If one is missing, the total is unavailable, not a partial sum.</p>
      <p>Returns also need a complete starting value and values around deposits and withdrawals. Later prices cannot fill earlier gaps.</p>
      <p>Corrections preserve the original record and may make past returns unavailable. GoldScout does not create a new starting point automatically.</p>
    </details>
    <details className="help-disclosure" id="monitoring-setup"><summary>Optional checks and market context</summary>
      <p>These separate views use saved evidence; opening them does not collect provider data, verify accounts or change records.</p>
      <p>Ask the person managing your workspace to configure separate local monitoring and Market Intelligence stores. Snapshot comparisons and any provider collection need separate setup and permission.</p>
      <p>Market Intelligence is public information, not investment advice. Never send private account evidence to hosted AI.</p>
    </details>
    <details className="help-disclosure" id="backup-recovery"><summary>Backup and recovery</summary>
      <p>Backups are not automatic. On the portfolio page, open <strong>Tools</strong>, then <strong>Backup and recovery tools</strong> to download a verified snapshot and its receipt. Check the displayed journal location and your browser's download destination first.</p>
      <p>The downloaded ZIP is not encrypted. Store it somewhere protected and separate from this computer. The dashboard can rehearse a selected GoldScout backup by checking its receipt and copying it to a new temporary location; it then removes that temporary copy.</p>
      <p>A restore rehearsal does not install a backup or overwrite the active journal. It does not prove that the selected backup is the newest, that account evidence is correct, or that power-loss durability is guaranteed. Keep original source documents and setup receipts separately.</p>
      <details className="help-disclosure"><summary>Steps for a source checkout</summary>
        <ol>
          <li>Confirm the exact journal path and choose a new protected destination. Never place a restore over the active journal.</li>
          <li>Choose an existing protected destination folder and a new filename. This example assumes <code>.\backups</code> already exists; replace it if needed. The backup command refuses to replace an existing file:</li>
        </ol>
        <pre>python -m investment backup --database .\local-investment\investment.sqlite3 --destination .\backups\investment-backup.sqlite3</pre>
        <p>For the default source workspace, the journal is under <code>.\local-investment</code>. If you selected another workspace, replace the database path with that workspace's journal. Backups are not encrypted automatically.</p>
        <ol start={3}><li>Verify the backup and test restoration to a new path. Never restore over the active journal.</li></ol>
      </details>
      <p>Keep source documents and setup receipts separately, and store a protected backup away from this computer.</p>
    </details>
    <details className="help-disclosure"><summary>About your records</summary>
      <p>Figures are entered by you and are not checked with a bank or broker. Manual recording is available after setup; use <code>--investment-read-only</code> to view without journal writes. Keep it on this computer; there is no sign-in.</p>
      <p>GoldScout does not recommend allocations or approve orders. It cannot place trades or move money.</p>
      <p>Investment values can fall, and no return is guaranteed. Account type does not confirm tax treatment, eligibility or deposit protection.</p>
      <p>Recorded trade fees are not total costs; fund, platform, subscription and FX charges may be missing. Savings rates and maturity dates are dated facts to verify, not comparisons of obtainable outcomes. GoldScout does not calculate wrapper limits, LISA eligibility or fund overlap.</p>
    </details>
  </article>;
}
