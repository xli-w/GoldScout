import { useState } from "react";
import { NavLink } from "react-router-dom";
import type { InvestmentSnapshot } from "./InvestmentView";
import type { EToroSnapshot } from "./EToroPortfolio";
import { addBridgePair, bridgeRows, unambiguousBridgeRows, type BridgePair } from "./etoro-bridge-model";

export function EToroJournalBridge({ snapshot, journal }: {
  snapshot: EToroSnapshot; journal: InvestmentSnapshot;
}) {
  const [accountId, setAccountId] = useState("");
  const [accountConfirmed, setAccountConfirmed] = useState(false);
  const [sourceKey, setSourceKey] = useState("");
  const [productId, setProductId] = useState("");
  const [pairs, setPairs] = useState<BridgePair[]>([]);
  const [error, setError] = useState("");
  const accounts = journal.accounts.filter(account => account.kind === "broker" && account.status === "active");
  const account = accounts.find(item => item.id === accountId);
  const holdings = journal.holdings.filter(item => item.account_id === accountId);
  const rows = bridgeRows(snapshot);
  const eligible = unambiguousBridgeRows(rows);
  const unpairedHoldings = holdings.filter(holding => !pairs.some(pair => pair.productId === holding.product_id));
  const unpairedRows = eligible.filter(row => !pairs.some(pair => pair.sourceKey === row.key));
  const ambiguous = rows.length - eligible.length;
  const changeAccount = (id: string) => {
    setAccountId(id); setAccountConfirmed(false); setSourceKey(""); setProductId(""); setPairs([]); setError("");
  };
  const addPair = () => {
    const updated = account && accountConfirmed
      ? addBridgePair(rows, holdings, pairs, sourceKey, productId) : null;
    if (!updated) {
      setError("Choose one available provider row and one different recorded holding.");
      return;
    }
    setPairs(updated);
    setSourceKey(""); setProductId(""); setError("");
  };
  return <section className="etoro-journal-bridge" aria-label="Review eToro against journal">
    <h3>Review this eToro account against your records</h3>
    <p>Make temporary, explicit pairings to inspect the two views side by side. Nothing is imported,
      saved, matched or updated. Pairings disappear when the view or source changes.</p>
    <p className="position-note">eToro account {snapshot.account_id} · real environment · source time {snapshot.source_as_of} UTC.
      Journal revision {journal.revision}; latest recorded units and cash are not necessarily from the provider's source time.</p>
    <label className="bridge-field">Journal account
      <select value={accountId} onChange={event => changeAccount(event.target.value)}>
        <option value="">Choose the corresponding recorded investment account</option>
        {accounts.map(item => <option key={item.id} value={item.id}>{item.name} ({item.id})</option>)}
      </select>
    </label>
    {accounts.length === 0 && <p>No active recorded investment account is available. The provider account has not been added to the journal.</p>}
    {account && <label className="setup-check">
      <input type="checkbox" checked={accountConfirmed} onChange={event => {
        setAccountConfirmed(event.target.checked); setPairs([]); setSourceKey(""); setProductId(""); setError("");
      }} />
      I checked that eToro account {snapshot.account_id} is the same account as {account.name} ({account.id}).
    </label>}
    {account && accountConfirmed && <>
      <p>Journal cash: GBP {account.cash_gbp ?? "unavailable"}. eToro available cash: {snapshot.account_totals.accountAvailableCash ?? "unavailable"} {snapshot.account_currency}.
        These are different source figures, not a GBP cash reconciliation; the separate provider GBP balance is not cash.</p>
      <p className="position-note">Instrument type, individual position legs, complete coverage and the exact relationship between net units and journal units are not verified.
        Copy positions, leverage, short positions, currencies and pending orders may make these figures incomparable.
        An equal number is not a match; a different number does not identify a transaction or error.</p>
      {ambiguous > 0 && <p className="account-attention">{ambiguous} provider {ambiguous === 1 ? "row shares" : "rows share"} an instrument ID across direct or copy groups.
        These rows cannot be paired here; check the underlying positions without adding or netting them.</p>}
      {rows.length === 0 && <p>No instrument aggregates were returned. This does not prove that the account has no holdings.</p>}
      {holdings.length === 0 && <p>No holdings are recorded for this account. Do not treat an omitted holding as zero.</p>}
      {unpairedRows.length > 0 && unpairedHoldings.length > 0 && <div className="bridge-pair-controls">
        <label className="bridge-field">Provider row
          <select value={sourceKey} onChange={event => { setSourceKey(event.target.value); setError(""); }}>
            <option value="">Choose a provider row</option>
            {unpairedRows.map(row => <option key={row.key} value={row.key}>
              {row.group} · instrument {row.instrumentId} · {row.units} net units · {row.currency}
            </option>)}
          </select>
        </label>
        <label className="bridge-field">Recorded holding
          <select value={productId} onChange={event => { setProductId(event.target.value); setError(""); }}>
            <option value="">Choose a recorded holding</option>
            {unpairedHoldings.map(item => <option key={item.product_id} value={item.product_id}>
              {item.name} ({item.product_id}) · {item.quantity} units
            </option>)}
          </select>
        </label>
        <button type="button" onClick={addPair}>Add to this review</button>
      </div>}
      {error && <p role="alert">{error}</p>}
      {pairs.length > 0 && <ul aria-label="Temporary holding pairings">{pairs.map(pair => {
        const row = rows.find(item => item.key === pair.sourceKey);
        const holding = holdings.find(item => item.product_id === pair.productId);
        if (!row || !holding) return null;
        return <li key={pair.sourceKey}>
          {row.group} instrument {row.instrumentId}: {row.units} provider net units ({row.currency});
          {" "}{holding.name} ({holding.product_id}): {holding.quantity} recorded units.
          {" "}<button type="button" onClick={() => setPairs(current => current.filter(item => item.sourceKey !== pair.sourceKey))}>
            Remove pairing for instrument {row.instrumentId}
          </button>
        </li>;
      })}</ul>}
      <p>{rows.length - pairs.length} provider {rows.length - pairs.length === 1 ? "row is" : "rows are"} not paired;
        {" "}{holdings.length - pairs.length} recorded {holdings.length - pairs.length === 1 ? "holding is" : "holdings are"} not paired.
        Unpaired does not mean absent or zero.</p>
      <NavLink to={`/investment/update?account_id=${encodeURIComponent(account.id)}`}>Update {account.name} using independent evidence</NavLink>
      <p className="position-note">Check actual dated statements, product type, transactions, GBP prices and permitted retention before recording.
        Do not use a provider headline value as cash or invent a transaction to remove a difference.</p>
    </>}
  </section>;
}
