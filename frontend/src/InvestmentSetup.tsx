import { useEffect, useRef, useState, type FormEvent } from "react";
import { reviewTimestamp } from "./api";
import { localClock } from "./recording";
import {
  setupConfirm, setupPreview, setupRecoveryPreview, setupSession,
  type SetupConfirmation, type SetupOpening, type SetupPreview, type SetupProfile, type SetupSession,
} from "./setup-api";

type Account = { id: string; binding: string; name: string; kind: "" | "broker" | "deposit"; cash: string };
type Product = { id: string; name: string; sleeve: "" | "equity" | "bond";
  currency: "" | "GBP"; increment: string; minimum: string };
type Holding = { id: string; account: string; product: string; quantity: string; price: string };
const identifier = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;
const newAccount = (): Account => ({ id: identifier("account"), binding: identifier("local"),
  name: "", kind: "", cash: "" });
const steps = ["Accounts", "Investments", "Starting balances", "Review and save"];
const fixedWarning = "Check your choices carefully. You cannot edit these starting records after saving. Some later changes are supported through a separate recording mode.";

function amount(value: string, label: string, positive = false): string {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,8})?$/.test(value) ||
      (positive && !/[1-9]/.test(value)))
    throw new Error(`${label}: enter a number ${positive ? "greater than zero" : "of zero or more"}, with up to eight decimal places. Enter 0 only if your source confirms zero.`);
  return value;
}
function utc(value: string, label: string): string {
  let timestamp: string;
  try { timestamp = localClock(value); }
  catch (error) {
    throw new Error(`${label}: ${error instanceof Error ? error.message : "Invalid local date and time."}`);
  }
  if (Date.parse(timestamp) > Date.now()) throw new Error(`${label} cannot be in the future.`);
  return timestamp;
}

export function InvestmentSetup({ onComplete }: { onComplete: () => void }) {
  const [session, setSession] = useState<SetupSession | null>(null);
  const [accounts, setAccounts] = useState<Account[]>(() => [newAccount()]);
  const [products, setProducts] = useState<Product[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [effective, setEffective] = useState("");
  const [available, setAvailable] = useState("");
  const [evidence, setEvidence] = useState("");
  const [scope, setScope] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [step, setStep] = useState(0);
  const [preview, setPreview] = useState<SetupPreview | null>(null);
  const [receipt, setReceipt] = useState<SetupConfirmation | null>(null);
  const [confirmAttempted, setConfirmAttempted] = useState(false);
  const [recoveryEntry, setRecoveryEntry] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorSummary = useRef<HTMLDivElement>(null);
  const portfolioId = useRef(identifier("portfolio"));
  const openingId = useRef(identifier("opening"));
  const recovery = session?.state === "recoverable" ? session.recovery : undefined;

  useEffect(() => {
    let active = true;
    setupSession().then(value => { if (active) setSession(value); })
      .catch(() => { if (active) setError("Setup status is unavailable. Reload to retry; no creation has been requested."); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!dirty || receipt?.status === "completed") return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const abandon = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (link && !window.confirm("Leave setup? This private draft exists only in memory and will be lost.")) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    document.addEventListener("click", abandon, true);
    return () => { window.removeEventListener("beforeunload", warn); document.removeEventListener("click", abandon, true); };
  }, [dirty, receipt]);
  useEffect(() => { heading.current?.focus(); }, [step, preview, receipt, session?.enabled]);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  useEffect(() => {
    if (!preview) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview]);

  const run = async (action: () => Promise<void>) => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { await action(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Setup is unavailable. No further action was taken."); }
    finally { lock.current = false; setBusy(false); }
  };
  const changed = () => { setDirty(true); setPreview(null); setReviewed(false); setError(""); };
  const changeAccount = (id: string, patch: Partial<Account>) => {
    changed(); setAccounts(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const changeProduct = (id: string, patch: Partial<Product>) => {
    changed(); setProducts(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const changeHolding = (id: string, patch: Partial<Holding>) => {
    changed(); setHoldings(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const next = (event: FormEvent) => {
    event.preventDefault();
    if (!scope) { setError("Confirm that you have chosen the GBP accounts you want to track."); return; }
    setError(""); setStep(value => value + 1);
  };
  const makePreview = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (session?.state === "recoverable" && (!recoveryEntry || !recovery?.requires_opening))
        throw new Error("The original setup details are unavailable. Keep all setup files unchanged and reload for recovery guidance.");
      const timestamp = utc(effective, "Balances as of");
      const availableAt = utc(available, "Source available from");
      if (availableAt < timestamp) throw new Error('"Source available from" cannot be earlier than "Balances as of". Check the dates against your source; do not guess.');
      const wireAccounts: SetupProfile["accounts"] = accounts.map(a => {
        if (!a.name.trim() || !a.kind) throw new Error("Give each account a name and choose its type.");
        return { id: a.id, binding: a.binding, name: a.name.trim(), kind: a.kind, currency: "GBP" };
      });
      const wireProducts: SetupProfile["products"] = products.map(p => {
        if (!p.name.trim() || !p.sleeve || p.currency !== "GBP") throw new Error("Give each investment a name, choose its type and confirm that it is priced in GBP.");
        return { id: p.id, name: p.name.trim(), sleeve: p.sleeve, currency: p.currency,
          quantity_increment: amount(p.increment, `${p.name} smallest quantity step`, true),
          minimum_notional_gbp: amount(p.minimum, `${p.name} minimum purchase`) };
      });
      if (!evidence.trim()) throw new Error("Briefly describe where your starting figures came from.");
      const opening: SetupOpening = {
        id: recoveryEntry && recovery ? recovery.opening_id : openingId.current,
        type: "opening", timestamp, available_at: availableAt, evidence: recoveryEntry ? evidence : evidence.trim(),
        balances_gbp: Object.fromEntries(accounts.map(a => [a.id, amount(a.cash, `${a.name} cash`)])),
        holdings: holdings.map(h => {
          if (!accounts.some(a => a.id === h.account && a.kind === "broker") ||
              !products.some(p => p.id === h.product)) throw new Error("Choose an investment account and investment for every holding.");
          return { account_id: h.account, product_id: h.product,
            quantity: amount(h.quantity, "Holding quantity", true),
            price_gbp: h.price === "" ? null : amount(h.price, "Unit price", true) };
        }),
      };
      const result = await setupPreview(session?.csrf ?? "", {
        request_id: identifier("request"),
        profile: recoveryEntry && recovery ? recovery.profile : { schema_version: 2, portfolio_id: portfolioId.current, currency: "GBP", created_at: timestamp,
          accounts: wireAccounts, products: wireProducts, policy: null },
        opening,
      });
      setPreview(result); setReviewed(false); setNow(Date.now()); setStep(3);
    });
  };
  const confirm = () => {
    if (!preview || !reviewed || (!confirmAttempted && Date.parse(preview.expires_at) <= Date.now())) return;
    void run(async () => {
      setConfirmAttempted(true);
      try {
        const result = await setupConfirm(session?.csrf ?? "", preview.preview_id);
        setReceipt(result);
        if (result.status === "completed") { setDirty(false); onComplete(); }
      } catch {
        throw new Error("The save outcome is unknown or the request was refused. Records may already have been saved. Do not start another setup or delete files. Use Retry same confirmation to check this exact request, or reload for recovery guidance. Error details may be in the terminal window used to start the dashboard.");
      }
    });
  };
  const recover = () => {
    void run(async () => {
      try {
        const result = await setupRecoveryPreview(session?.csrf ?? "");
        setPreview(result); setReviewed(false); setNow(Date.now()); setStep(3); setDirty(true);
      } catch {
        throw new Error("The previous setup could not be verified for recovery. Keep all setup files unchanged. If starting figures were not retained, re-entering and explicitly reviewing the original figures is required. Do not guess balances or create a replacement. Check the terminal window used to start the dashboard for error details.");
      }
    });
  };
  const reenterOpening = () => {
    if (!recovery?.requires_opening || confirmAttempted) return;
    setAccounts(recovery.profile.accounts.map(a => ({ ...a, cash: "" })));
    setProducts(recovery.profile.products.map(p => ({
      id: p.id, name: p.name, sleeve: p.sleeve, currency: p.currency,
      increment: p.quantity_increment, minimum: p.minimum_notional_gbp,
    })));
    setHoldings([]); setEffective(""); setAvailable(""); setEvidence("");
    setPreview(null); setReviewed(false); setError(""); setScope(true);
    setStep(2); setRecoveryEntry(true); setDirty(true);
  };
  const expired = preview !== null && Date.parse(preview.expires_at) <= now;
  const blocked = session && (!session.enabled || !["empty", "recoverable"].includes(session.state));

  return <section className="investment-setup" aria-label="Investment setup" aria-busy={busy}>
    <p className="eyebrow">WELCOME TO GOLDSCOUT</p>
    <h1 ref={heading} tabIndex={-1}>Let's set up your portfolio</h1>
    <p className="setup-intro">Keep a record of your savings and investments in pounds. You'll add your accounts,
      enter starting balances, then check everything before saving.</p>
    <p className="position-note">Manual records only. This does not connect to a bank or broker, move money or make investment decisions.</p>
    <p className="setup-draft-note">{session?.state === "recoverable"
      ? "Leaving or reloading loses any re-entered figures. Existing setup files may already contain saved records."
      : "Your draft is not saved until you confirm. Leaving or reloading this page loses your progress."}</p>
    <details className="help-disclosure">
      <summary>Before you start: privacy, storage and later changes</summary>
      <p>Have statements or other dated records ready. Use nicknames, not account numbers, passwords or credentials.</p>
      <p>{fixedWarning} Account identities, bindings and currencies, and investment classifications and quantity rules stay fixed.
        Retirement requires zero balances or holdings. Adding a funded account later needs a complete valuation at one time;
        corrections preserve the original records and make historical performance unavailable.</p>
      <p>Preview and navigation create no workspace files. Only confirmation can create records, and setup does not enable later recording.
        For recovery, existing files may already contain committed records.</p>
      {session && <p className="setup-workspace">Saved on this computer in: <strong>{session.workspace}</strong>.</p>}
      <p>Use a private folder you control. Git ignore is not encryption, access control or a backup.
        The dashboard has no user login; keep it on this computer's loopback address.</p>
    </details>
    {error && <div role="alert" tabIndex={-1} ref={errorSummary} className="investment-warnings">{error}</div>}
    {receipt ? <div role="status" className="recording-preview">
      <h2>{receipt.status === "completed" ? "Setup completed — read-only" : "Setup partially completed — preserve the workspace"}</h2>
      <p>{receipt.committed
        ? "Your starting records were saved. Do not repeat setup or delete its files."
        : "Saving your starting records has not been confirmed. Some setup files may remain; do not replace them."}</p>
      <details className="help-disclosure"><summary>Save receipt and technical details</summary>
        <p>{receipt.revision === null ? "No committed journal revision." : `Journal revision: ${receipt.revision}.`} {receipt.message}</p>
        <p>Journal committed: {receipt.committed ? "yes" : "no"}.</p>
      </details>
      {receipt.status === "completed" ? <>
        <p>Recording remains disabled. Enable later recording separately and arrange a verified backup.</p>
        <button type="button" onClick={onComplete}>Open read-only investment view</button>
      </>       : <p>Setup did not finish completely. Keep the whole setup folder unchanged and reload this page for recovery guidance.
        Do not recreate, delete, overwrite or manually repair any files.</p>}
    </div> : blocked ? <div className="investment-warnings">
      <h2>{session.state === "ready" ? "Existing investment records" : "Setup cannot create this workspace"}</h2>
      {session.message && <details className="help-disclosure"><summary>Technical reason setup cannot continue</summary><p>{session.message}</p></details>}
      {session.state === "ready" ? <button type="button" onClick={onComplete}>Open read-only investment view</button>
        : <p>{session.state === "manual_unopened"
          ? "These records were already initialized but have no starting balances. The wizard cannot adopt or overwrite them. Keep the files unchanged and use the existing manual recording workflow for this journal."
          : session.state === "recoverable"
            ? "A previous setup was interrupted. Keep all files unchanged and use only the recovery options offered here. Nothing resumes automatically."
            : "Keep existing files unchanged. Check the terminal window used to start the dashboard for error details. No automatic repair, deletion or replacement is available here."}</p>}
    </div> : !session ? <p role="status">Waiting for setup status…</p>
      : <>
        <ol className="setup-steps" aria-label="Setup progress">{steps.map((name, index) =>
          (session.state !== "recoverable" || index >= 2) &&
          <li key={name} aria-current={index === step ? "step" : undefined}>{index + 1}. {name}</li>)}</ol>
        <h2>{session.state === "recoverable" && !preview && !recoveryEntry ? "Recovery" : steps[step]}</h2>
        {preview ? <div className="recording-preview" role="group" aria-label="Review setup before confirmation">
          <p>{session.state === "recoverable"
            ? "Check the recovered records below. Starting records may already be saved; confirmation continues only this verified recovery."
            : "Nothing has been saved yet. Check the names, amounts, dates and source below."}</p>
          <p className="position-note">Preview valid until {reviewTimestamp(preview.expires_at)}.</p>
          <p><strong>{preview.report.complete && preview.report.equity_gbp !== null
            ? `Total starting value: GBP ${preview.report.equity_gbp}`
            : "Total starting value unavailable: some prices or dates are incomplete. Missing is not zero."}</strong></p>
          {preview.report.warnings.length > 0 && <div className="investment-warnings" role="status">
            <h3>Check before saving</h3>
            <ul>{preview.report.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
          </div>}
          <h3>Accounts and cash (GBP)</h3>
          <ul>{preview.profile.accounts.map(a => <li key={a.id}>{a.name} — {a.kind === "deposit" ? "Savings / deposit" : "Investment / broker"};
            cash GBP {preview.opening.balances_gbp[a.id] ?? "Unavailable"}</li>)}</ul>
          <h3>What you already own</h3>
          {preview.opening.holdings.length === 0 ? <p>No investment holdings declared.</p> : <ul>{preview.opening.holdings.map((h, index) =>
            <li key={index}>{preview.profile.accounts.find(a => a.id === h.account_id)?.name ?? h.account_id} /
              {" "}{preview.profile.products.find(p => p.id === h.product_id)?.name ?? h.product_id}:
              quantity {h.quantity}; unit price {h.price_gbp === null ? "Unknown — not zero" : `GBP ${h.price_gbp}`}</li>)}</ul>}
          <h3>Investment details</h3>
          {preview.profile.products.length === 0 ? <p>No investments included.</p> : <ul>{preview.profile.products.map(p =>
            <li key={p.id}>{p.name} — {p.sleeve === "equity" ? "Shares / equity" : "Bonds"}, {p.currency};
              smallest quantity step {p.quantity_increment}; minimum purchase GBP {p.minimum_notional_gbp}</li>)}</ul>}
          <dl className="review-dates">
            <div><dt>Balances and prices as of</dt><dd>{reviewTimestamp(preview.opening.timestamp)}</dd></div>
            <div><dt>Source available from</dt><dd>{reviewTimestamp(preview.opening.available_at)}</dd></div>
          </dl>
          <p>Where these figures came from: {preview.opening.evidence}</p>
          <p>Performance tracking starts from: {preview.report.baseline_as_of === null ? "Not available yet" : reviewTimestamp(preview.report.baseline_as_of)}.
            Profit or loss before this date is unknown.</p>
          <details className="help-disclosure"><summary>How starting values and performance are recorded</summary>
            <p>All opening roles are unassigned: you have not labelled funds as core (long-term) or dynamic (experimental).
              No allocation policy is set and proposals remain blocked.</p>
            <ul>{preview.report.limitations.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
          </details>
          <details className="recording-event">
            <summary>Technical details: exact profile and starting record</summary>
            <p>Portfolio: {preview.profile.portfolio_id}; profile inception (UTC): {preview.profile.created_at}.
              Opening record: {preview.opening.id}. Account IDs and bindings are included below.</p>
            <pre>{JSON.stringify({ profile: preview.profile, opening: preview.opening }, null, 2)}</pre>
          </details>
          <p>{fixedWarning}</p>
          <label className="setup-check"><input type="checkbox" checked={reviewed} disabled={busy || confirmAttempted}
            onChange={event => setReviewed(event.target.checked)} />
            I reviewed every account, investment, amount, date and source. I understand the starting records cannot be edited after saving.</label>
          {expired && !confirmAttempted && <p role="alert">Preview expired. Edit and request a fresh preview before confirming.</p>}
          <div className="setup-actions">
            <button type="button" disabled={busy || confirmAttempted} onClick={() => { setPreview(null); setReviewed(false); setStep(2); }}>
              {session.state === "recoverable" && !recoveryEntry ? "Back to recovery guidance" : "Edit evidence"}</button>
            <button type="button" disabled={busy || !reviewed || (expired && !confirmAttempted)} onClick={confirm}>
              {confirmAttempted ? "Retry same confirmation" : "Confirm and create local records"}</button>
          </div>
        </div> : session.state === "recoverable" && !recoveryEntry ? <div className="recording-preview">
          <h3>Finish an interrupted setup</h3>
          <p>A previous setup did not finish. Starting records may already be saved. Keep the whole setup folder unchanged;
            do not start again or delete files.</p>
          <ol>
            <li>Use the recovery option below to check the original setup.</li>
            <li>If asked, copy the exact original starting figures from your source. Never guess missing amounts.</li>
            <li>Check every recovered account, investment, amount, date and source before confirming.</li>
          </ol>
          <p>The check alone does not save, repair or overwrite records.</p>
          <details className="help-disclosure"><summary>How recovery protects existing records</summary>
            <p>Preview verifies the setup receipt and exact existing evidence. Only a verified match can continue.
              An unrelated or approximately matching journal cannot be adopted.</p>
            {session.message && <p>{session.message}</p>}
          </details>
          {recovery?.can_preview && <button type="button" disabled={busy} onClick={recover}>Preview verified recovery</button>}
          {recovery?.requires_opening && <button type="button" disabled={busy} onClick={reenterOpening}>Re-enter original opening evidence</button>}
          {!recovery || (!recovery.can_preview && !recovery.requires_opening)
            ? <p>The previous setup cannot be verified. Keep all files unchanged and check the terminal window used to start the dashboard.
              A replacement setup cannot be started here.</p> : null}
        </div> : <form className="recording-form setup-form" onSubmit={step === 2 ? makePreview : next}>
          <fieldset disabled={busy}>
            <legend>{steps[step]}</legend>
            {step === 0 && <>
              <p>Add only the GBP savings and investment accounts you want this portfolio to cover.</p>
              <p className="position-note">Use a nickname such as "House savings" or "Investment account" — never an account number or password.</p>
              {accounts.map((a, index) => <div className="setup-row" key={a.id}>
                <label>Account {index + 1} name<input required maxLength={120} value={a.name}
                  placeholder="e.g. House savings"
                  onChange={event => changeAccount(a.id, { name: event.target.value })} /></label>
                <label>Account {index + 1} type<select required value={a.kind}
                  onChange={event => { const kind = event.target.value; if (kind === "broker" || kind === "deposit") changeAccount(a.id, { kind }); }}>
                  <option value="">Choose account type</option><option value="deposit">Savings / deposit account</option><option value="broker">Investment / broker account</option>
                </select></label>
                <button type="button" disabled={accounts.length === 1} onClick={() => {
                  changed(); setAccounts(rows => rows.filter(row => row.id !== a.id));
                  setHoldings(rows => rows.filter(row => row.account !== a.id));
                }}>Remove account {index + 1}</button>
              </div>)}
              <button type="button" disabled={accounts.length >= 50} onClick={() => { changed(); setAccounts(rows => [...rows, newAccount()]); }}>Add account</button>
              <label className="setup-check"><input type="checkbox" required checked={scope}
                onChange={event => { changed(); setScope(event.target.checked); }} />
                These are the GBP accounts I want to track in this portfolio.</label>
              <details className="help-disclosure"><summary>Which account type should I choose?</summary>
                <p>Choose savings / deposit for cash savings. Choose investment / broker for an account that can hold shares or funds,
                  including its uninvested cash. Account type does not verify ISA/LISA status, tax treatment, eligibility or deposit protection.</p>
              </details>
            </>}
            {step === 1 && <>
              <p>Add the shares, funds or bonds you want to record. If you only track cash savings, you can continue without adding any.</p>
              <p className="position-note">Only GBP-priced equity and bond investments are supported. Copy the details from your provider; don't guess.</p>
              <p className="example-note"><strong>Fictional example:</strong> "Example shares fund" is the investment.
                If you own 12.5 units in an investment account, that is a holding. Add the investment here;
                enter its account, 12.5 units and any known price in Starting balances. Do not copy these example figures.</p>
              <details className="help-disclosure"><summary>Help with investment types and quantity rules</summary>
                <p>Equity means shares or share-based funds. Bonds are debt investments, not protected savings.
                  GBP pricing does not remove any underlying foreign-currency risk.</p>
                <p>The smallest quantity step is the unit increment your provider supports, such as 1 whole unit or 0.001 units.
                  The minimum purchase is the smallest supported purchase value in pounds. Enter 0 only if the provider confirms no minimum.
                  No provider lookup is performed. If you cannot verify a required detail, pause setup rather than inventing it.</p>
                <p>Look in your provider's dealing rules or product documents, or ask its support team for the exact investment and account.
                  If the provider prices in pence (GBX), do not enter that number as pounds; verify the GBP equivalent.</p>
              </details>
              {products.map((p, index) => <div className="setup-row" key={p.id}>
                <label>Investment {index + 1} name<input required maxLength={120} value={p.name}
                  onChange={event => changeProduct(p.id, { name: event.target.value })} /></label>
                <label>Investment {index + 1} type<select required value={p.sleeve} onChange={event => {
                  const sleeve = event.target.value; if (sleeve === "equity" || sleeve === "bond") changeProduct(p.id, { sleeve });
                }}><option value="">Choose investment type</option><option value="equity">Shares / equity fund</option><option value="bond">Bond / bond fund</option></select></label>
                <label>Investment {index + 1} pricing currency<select required value={p.currency} onChange={event => {
                  if (event.target.value === "GBP") changeProduct(p.id, { currency: "GBP" });
                }}><option value="">Confirm currency</option><option value="GBP">GBP</option></select></label>
                <label>Investment {index + 1} smallest quantity step<input required inputMode="decimal" maxLength={34} value={p.increment}
                  aria-describedby={`setup-increment-${p.id}`}
                  onChange={event => changeProduct(p.id, { increment: event.target.value })} />
                  <small id={`setup-increment-${p.id}`} className="field-help">The provider's unit step, not how many you own. For example, 1 or 0.001; check your provider.</small></label>
                <label>Investment {index + 1} minimum purchase (GBP)<input required inputMode="decimal" maxLength={34} value={p.minimum}
                  aria-describedby={`setup-minimum-${p.id}`}
                  onChange={event => changeProduct(p.id, { minimum: event.target.value })} />
                  <small id={`setup-minimum-${p.id}`} className="field-help">The provider's smallest purchase in pounds. For example, £10; if unknown, pause rather than guessing.</small></label>
                <button type="button" onClick={() => { changed(); setProducts(rows => rows.filter(row => row.id !== p.id));
                  setHoldings(rows => rows.filter(row => row.product !== p.id)); }}>Remove investment {index + 1}</button>
              </div>)}
              <button type="button" disabled={products.length >= 100} onClick={() => { changed(); setProducts(rows => [...rows,
                { id: identifier("product"), name: "", sleeve: "", currency: "", increment: "", minimum: "" }]); }}>Add investment</button>
            </>}
            {step === 2 && <>
              {recoveryEntry && recovery && <section aria-label="Recovered fixed profile">
                <h3>Your original accounts and investments</h3>
                <p>Re-enter the original cash, holdings in their original order, dates and source description exactly, including decimal formatting
                  and any unknown prices. Recovery requires an exact match; approximate figures are refused. These accounts and investment rules cannot be changed here.</p>
                <ul>{recovery.profile.accounts.map(a => <li key={a.id}>{a.name} — {a.kind === "deposit" ? "Savings / deposit" : "Investment / broker"}, {a.currency}</li>)}</ul>
                <ul>{recovery.profile.products.map(p => <li key={p.id}>{p.name} — {p.sleeve}, {p.currency}; ID {p.id};
                  smallest quantity step {p.quantity_increment}; minimum purchase GBP {p.minimum_notional_gbp}</li>)}</ul>
                <details className="recording-event"><summary>Exact original profile</summary>
                  <p>Portfolio: {recovery.profile.portfolio_id}; inception (UTC): {recovery.profile.created_at}.
                    Original opening ID: {recovery.opening_id}. Account IDs, bindings and product metadata are fixed.
                    Recovery verifies the original confirmed opening hash.</p>
                  <pre>{JSON.stringify(recovery.profile, null, 2)}</pre></details>
              </section>}
              <p>Enter what you had at one point in time: cash in every account and all investments you already owned.</p>
              <h3>1. When do the figures apply?</h3>
              <p className="position-note">Use the same date and time for all balances and prices. Enter dates in {Intl.DateTimeFormat().resolvedOptions().timeZone};
                the review shows local time alongside exact UTC time.</p>
              <p className="example-note"><strong>Two different dates:</strong> a statement might describe balances at 6 pm on 1 July
                but first become available at 9 am on 2 July. Enter those two times, not today's typing time.
                This is an example, not suggested dates for your records.</p>
              <details className="help-disclosure"><summary>Why are there two dates?</summary>
                <p>"Balances as of" is the time the figures describe. "Source available from" is when that information actually became available,
                  not the time you are typing it. For example, a statement may describe an earlier date but be published later.</p>
                <p>Late information or unknown prices can prevent a complete starting value or performance starting point (baseline).
                  Earlier transactions, purchase costs and past gains are not reconstructed.</p>
              </details>
              <label>Balances as of (local time)<input required type="datetime-local" value={effective}
                onChange={event => { changed(); setEffective(event.target.value); }} /></label>
              <label>Source available from (local time)<input required type="datetime-local" value={available}
                onChange={event => { changed(); setAvailable(event.target.value); }} /></label>
              <h3>2. Cash in each account</h3>
              <p>Enter cash only, not the value of investments held in the account. Enter 0 if your source confirms no cash. Don't leave a cash balance blank.</p>
              {accounts.map(a => <label key={a.id}>Cash for {a.name} (GBP)<input required inputMode="decimal" maxLength={34}
                value={a.cash} onChange={event => changeAccount(a.id, { cash: event.target.value })} /></label>)}
              <h3>3. Investments you own</h3>
              <p>Add every investment holding in the accounts you chose. Leave only the unit price blank if unknown; it is not treated as zero.
                If you add no rows, you are declaring that you hold no investments.</p>
              {holdings.map((h, index) => <div className="setup-row" key={h.id}>
                <label>Holding {index + 1} account<select required value={h.account} onChange={event => changeHolding(h.id, { account: event.target.value })}>
                  <option value="">Choose broker</option>{accounts.filter(a => a.kind === "broker").map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                <label>Holding {index + 1} investment<select required value={h.product} onChange={event => changeHolding(h.id, { product: event.target.value })}>
                  <option value="">Choose investment</option>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
                <label>Holding {index + 1} quantity<input required inputMode="decimal" maxLength={34} value={h.quantity}
                  onChange={event => changeHolding(h.id, { quantity: event.target.value })} /></label>
                <label>Holding {index + 1} unit price (GBP, optional)<input inputMode="decimal" maxLength={34} value={h.price}
                  onChange={event => changeHolding(h.id, { price: event.target.value })} /></label>
                <button type="button" onClick={() => { changed(); setHoldings(rows => rows.filter(row => row.id !== h.id)); }}>Remove holding {index + 1}</button>
              </div>)}
              <button type="button" disabled={!products.length || !accounts.some(a => a.kind === "broker") || holdings.length >= 5000}
                onClick={() => { changed(); setHoldings(rows => [...rows, { id: identifier("holding"), account: "", product: "", quantity: "", price: "" }]); }}>Add holding</button>
              <h3>4. Your source</h3>
              <label>Where did these figures come from?<textarea required maxLength={200} value={evidence}
                placeholder="e.g. Savings statement and investment statement, 1 November"
                onChange={event => { changed(); setEvidence(event.target.value); }} /></label>
              <p>Briefly name the source and accounts covered (up to 200 characters). Do not paste account numbers, passwords or full statements.</p>
            </>}
          </fieldset>
          <div className="setup-actions">
            {recoveryEntry ? <button type="button" disabled={busy} onClick={() => { setError(""); setRecoveryEntry(false); }}>Back to recovery guidance</button>
              : step > 0 && <button type="button" disabled={busy} onClick={() => { setError(""); setStep(value => value - 1); }}>Back</button>}
            <button type="submit" disabled={busy}>{step === 2 ? "Preview setup" : "Continue"}</button>
          </div>
        </form>}
      </>}
  </section>;
}
