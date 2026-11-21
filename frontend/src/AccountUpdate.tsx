import { useQuery } from "@tanstack/react-query";
import { NavLink, useLocation } from "react-router-dom";
import Decimal from "decimal.js";
import { accountDisplay, loadInvestment } from "./InvestmentView";
import { evidenceAge, pounds, timestamp } from "./api";
import { AccountCheckCompletion } from "./AccountCheckCompletion";

export function AccountUpdatePage({ now }: { now: number }) {
  const { search, state } = useLocation();
  const accountId = new URLSearchParams(search).get("account_id");
  const query = useQuery({
    queryKey: ["investment"], queryFn: ({ signal }) => loadInvestment(signal),
    refetchOnMount: "always",
  });
  const savedNotice = state?.recordSaved && <p role="status">Your record was saved.
    {state.reviewNotice && ` ${state.reviewNotice}`}</p>;
  if (query.isPending || (state?.recordSaved && (query.isFetching || query.fetchStatus === "paused"))) return <>
    {savedNotice}<p role="status">{query.fetchStatus === "paused"
      ? "Waiting to refresh the account's recorded evidence." : "Loading the account's recorded evidence."}</p>
  </>;
  if (query.isError || !query.data) return <>{savedNotice}<div className="error-state" role="alert">
    <h2>Account evidence is unavailable</h2>
    <p>{state?.recordSaved ? "The latest account evidence could not be refreshed."
      : "No update has been started."} Retry before using the recorded figures.</p>
    <button onClick={() => void query.refetch()}>Retry account review</button>
  </div></>;
  const data = query.data;
  const account = data.accounts.find(item => item.id === accountId && item.status === "active");
  if (!account || data.source_mode !== "recorded") return <>{savedNotice}<section className="record-review" role="alert">
    <h2>This account is not available for updating</h2>
    <p>The account may no longer be included, or this is synthetic data. No other account has been selected in its place.</p>
    <NavLink to="/investment">Return to your portfolio</NavLink>
  </section></>;
  const name = accountDisplay(data, account.id);
  const holdings = data.holdings.filter(item => item.account_id === account.id);
  const observation = account.cash_observation;
  const differs = observation !== null && !new Decimal(observation.difference_gbp).isZero();
  const action = (kind: string, productId?: string) =>
    `/investment/record?record=account-update&account_id=${encodeURIComponent(account.id)}&kind=${kind}` +
    (productId ? `&product_id=${encodeURIComponent(productId)}` : "");
  return <article className="account-update">
    <NavLink to="/investment">Back to your portfolio</NavLink>
    <h2>{name}</h2>
    {savedNotice}
    <p>Use a dated statement or account screen. Work through the changes one at a time; each record gets its own preview and confirmation.</p>
    <p className="position-note">This review does not contact a provider or import the separate eToro snapshot. It cannot certify that your statement or transaction history is complete.</p>

    <section className="record-review" aria-label="Account evidence to review">
      <h3>Start with what is recorded</h3>
      <dl className="health-facts">
        <div><dt>Recorded cash</dt><dd>{pounds(account.cash_gbp, true)}</dd></div>
        <div><dt>Last cash check</dt><dd>{observation === null ? "Never recorded" : <>
          {timestamp(observation.timestamp)} · {evidenceAge(observation.timestamp, now)} old
          <br />Observed {pounds(observation.balance_gbp, true)}; difference at that time {pounds(observation.difference_gbp, true)}.
          <details className="help-disclosure"><summary>Cash-check source</summary>
            <p>Applies at {observation.timestamp}; available at {observation.available_at}. Source: {observation.source}. {observation.evidence}</p>
          </details>
        </>}</dd></div>
        <div><dt>Portfolio valuation date</dt><dd>{timestamp(data.as_of)}{data.as_of && ` · ${evidenceAge(data.as_of, now)} old`}.
          {" "}{data.complete ? "Complete for that date, not a current-account verification." : "Incomplete; a full portfolio value is unavailable."}</dd></div>
      </dl>
      {differs && <p className="account-attention" role="note">The last cash check differed from the journal.
        Find the source-supported transactions or an error in an earlier record; do not enter a balancing deposit or withdrawal just to make the figures match.
        A later match would not resolve the earlier difference.</p>}
      {holdings.length === 0 ? <p>No holdings are recorded for this account. Confirm that against your source; absence in the journal does not prove a cash-only account.</p> :
        <div className="table-scroll" tabIndex={0} aria-label="Account holdings to check"><table>
          <thead><tr><th>Investment</th><th>Recorded units</th><th>Recorded price date</th><th>Next step</th></tr></thead>
          <tbody>{holdings.map(holding => <tr key={holding.product_id}>
            <td>{holding.name}</td><td>{holding.quantity}</td>
            <td>{holding.mark_at === null ? "No price recorded" : <>
              {holding.mark_at}<br />Age: {evidenceAge(holding.mark_at, now)}
            </>}</td>
            <td><NavLink to={action("mark", holding.product_id)}>Update price for {holding.name}</NavLink></td>
          </tr>)}</tbody>
        </table></div>}
      <p className="position-note">Cash and units reflect the latest journal entries, not necessarily the valuation date above. No automatic freshness threshold or provider agreement is implied.</p>
    </section>

    <section className="history-group" aria-label="Guided account update">
      <h3>What has happened since your records?</h3>
      <div className="account-update-steps">
        <section>
          <h4>1. Record the actual changes</h4>
          <p>Check dated transactions across all tracked accounts first, in time order. Later records can prevent earlier transactions from being appended, even for another account. A new balance alone does not explain deposits, interest, trades or missing records.</p>
          <ul>
            <li><NavLink to={action("contribution")}>Money came in from outside my tracked accounts</NavLink></li>
            <li><NavLink to={action("withdrawal")}>Money left my tracked accounts</NavLink></li>
            <li><NavLink to={action("transfer")}>I transferred money between tracked accounts</NavLink></li>
            <li><NavLink to={action("interest")}>Interest was paid</NavLink></li>
            <li><NavLink to={action("account_fee")}>An account or platform charge was paid</NavLink></li>
            {account.kind === "broker" && <>
              <li><NavLink to={action("dividend")}>A dividend was paid</NavLink></li>
              <li><NavLink to={action("buy")}>I bought an investment</NavLink></li>
              <li><NavLink to={action("sell")}>I sold an investment</NavLink></li>
            </>}
          </ul>
          <p className="position-note">Before recording a deposit or withdrawal, check valuation evidence at the transaction time for every holding in the portfolio, not just this account.
            Missing flow-time value can leave historical performance unavailable; a later price cannot repair it.</p>
          <NavLink to="/investment/record">Other changes or correct an earlier record</NavLink>
        </section>
        {account.kind === "deposit" && <section>
          <h4>Review savings terms</h4>
          <p>Record dated terms from your source. They are display facts, not interest accrual or proof of eligibility.</p>
          {account.terms === null ? <p>No savings terms are recorded.</p> : <dl className="health-facts">
            <div><dt>Rate</dt><dd>{account.terms.annual_rate_pct === null ? "Unavailable"
              : `${account.terms.annual_rate_pct}% ${account.terms.rate_basis ?? "basis unavailable"}`}</dd></div>
            <div><dt>Effective from</dt><dd>{account.terms.effective_from}</dd></div>
            <div><dt>Observed</dt><dd>{timestamp(account.terms.observed_at)}</dd></div>
            <div><dt>Available from</dt><dd>{timestamp(account.terms.available_at)}</dd></div>
            <div><dt>Maturity</dt><dd>{account.terms.maturity_date ?? "Not recorded"}</dd></div>
            <div><dt>Notice</dt><dd>{account.terms.notice_days === null ? "Not recorded" : `${account.terms.notice_days} days`}</dd></div>
            <div><dt>Source</dt><dd>{account.terms.source}</dd></div>
          </dl>}
          <NavLink to={action("savings_terms")}>Record this account's savings terms</NavLink>
        </section>}
        <section>
          <h4>2. Compare the cash balance</h4>
          <p>After recording transactions, compare cash with your dated source. A cash check records the observation and its difference; it does not overwrite your balance.</p>
          <NavLink to={action("cash_observation")}>Check this account's cash</NavLink>
        </section>
        <section>
          <h4>3. Review quantities and prices</h4>
          <p>Compare every holding's units with your source. Use transaction, split or correction records for quantity changes, not a price update.
            Use the investment links above for source-supported GBP prices.</p>
          <p className="position-note">If your statement uses another currency or a product the journal cannot represent, pause. Do not copy a provider headline value into a GBP balance or invent an exchange rate.</p>
        </section>
      </div>
    </section>
    <AccountCheckCompletion data={data} accountId={account.id} />
  </article>;
}
