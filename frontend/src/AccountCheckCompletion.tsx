import { useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { markAccountInReview } from "./AccountMaintenance";
import { readReviewCycle } from "./guided-review";
import type { ReviewCycle } from "./guided-review";
import { loadInvestment } from "./InvestmentView";
import type { InvestmentSnapshot } from "./InvestmentView";

export function AccountCheckCompletion({ data, accountId }: { data: InvestmentSnapshot; accountId: string }) {
  const navigate = useNavigate();
  const [available, setAvailable] = useState(false);
  const [cycle, setCycle] = useState<ReviewCycle | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    try {
      const cycle = readReviewCycle(data.portfolio_id);
      setCycle(cycle);
      setAvailable(Boolean(cycle && cycle.revision === data.revision && cycle.accountIds.includes(accountId)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The guided review is unavailable.");
    }
  }, [data.portfolio_id, data.revision, accountId]);

  async function complete() {
    setBusy(true);
    try {
      if (cycle && await markAccountInReview(data, accountId, cycle,
        () => loadInvestment(new AbortController().signal)))
        navigate("/investment#account-review");
      else setError("The review or journal changed. Return to your portfolio and refresh before continuing.");
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : "Review progress could not be saved."} A reminder date may have been saved; check it before continuing. No journal record was changed.`);
    } finally { setBusy(false); }
  }

  return <section className="record-review" aria-label="Finish checking this account">
    <h3>Finished checking this account?</h3>
    <p>If there were no changes, you can continue without adding a record. Checking an account does not update its recorded balances or prices.</p>
    {available ? <button type="button" disabled={busy} onClick={() => void complete()}>
      I checked this account — continue review
    </button> : <NavLink to="/investment#account-review">Return to account review</NavLink>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
