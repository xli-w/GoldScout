import { useEffect, useRef, useState, type FormEvent } from "react";
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
const steps = ["Accounts", "Products", "Opening evidence", "Review"];
const fixedWarning = "Accounts and products are fixed after creation in this wizard. Adding them later is not supported by this wizard.";

function amount(value: string, label: string, positive = false): string {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,8})?$/.test(value) ||
      (positive && !/[1-9]/.test(value)))
    throw new Error(`${label}: enter an explicit ${positive ? "positive" : "non-negative"} decimal, with at most eight decimal places. Missing is not zero.`);
  return value;
}
function utc(value: string, label: string): string {
  const date = new Date(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match || !Number.isFinite(date.getTime()) || date.getFullYear() !== Number(match[1]) ||
      date.getMonth() + 1 !== Number(match[2]) || date.getDate() !== Number(match[3]) ||
      date.getHours() !== Number(match[4]) || date.getMinutes() !== Number(match[5]))
    throw new Error(`${label}: enter a valid local date and time.`);
  if (date.getTime() > Date.now()) throw new Error(`${label} cannot be in the future.`);
  return date.toISOString();
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
    if (!scope) { setError("Confirm the supported account scope before continuing."); return; }
    setError(""); setStep(value => value + 1);
  };
  const makePreview = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (session?.state === "recoverable" && (!recoveryEntry || !recovery?.requires_opening))
        throw new Error("Original recovery evidence is unavailable. Preserve the workspace and reload for guidance.");
      const timestamp = utc(effective, "Effective time");
      const availableAt = utc(available, "Availability time");
      if (availableAt < timestamp) throw new Error("Availability time cannot be before the effective time.");
      const wireAccounts: SetupProfile["accounts"] = accounts.map(a => {
        if (!a.name.trim() || !a.kind) throw new Error("Every account needs an alias and a classification.");
        return { id: a.id, binding: a.binding, name: a.name.trim(), kind: a.kind, currency: "GBP" };
      });
      const wireProducts: SetupProfile["products"] = products.map(p => {
        if (!p.name.trim() || !p.sleeve || p.currency !== "GBP") throw new Error("Every product needs a name, sleeve and explicit GBP denomination.");
        return { id: p.id, name: p.name.trim(), sleeve: p.sleeve, currency: p.currency,
          quantity_increment: amount(p.increment, `${p.name} increment`, true),
          minimum_notional_gbp: amount(p.minimum, `${p.name} minimum notional`) };
      });
      if (!evidence.trim()) throw new Error("Enter the source description for this opening evidence.");
      const opening: SetupOpening = {
        id: recoveryEntry && recovery ? recovery.opening_id : openingId.current,
        type: "opening", timestamp, available_at: availableAt, evidence: recoveryEntry ? evidence : evidence.trim(),
        balances_gbp: Object.fromEntries(accounts.map(a => [a.id, amount(a.cash, `${a.name} cash`)])),
        holdings: holdings.map(h => {
          if (!accounts.some(a => a.id === h.account && a.kind === "broker") ||
              !products.some(p => p.id === h.product)) throw new Error("Select an included broker account and product for every holding.");
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
        throw new Error("Confirmation outcome is unknown or refused. Do not start another setup. Retry this same confirmation to obtain its receipt, or inspect the terminal and reload for workspace guidance.");
      }
    });
  };
  const recover = () => {
    void run(async () => {
      try {
        const result = await setupRecoveryPreview(session?.csrf ?? "");
        setPreview(result); setReviewed(false); setNow(Date.now()); setStep(3); setDirty(true);
      } catch {
        throw new Error("Recovery preview is unavailable. Preserve the workspace and check the terminal. If the interrupted setup has no opening evidence, re-entering and explicitly reviewing that evidence is required; do not guess balances or initialize another journal.");
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
    <h1 ref={heading} tabIndex={-1}>Set up local investment records</h1>
    <p>Manual, local GBP evidence only. No broker connection, trading, allocation policy or ongoing recording authority is enabled.</p>
    <p className="investment-warnings">{fixedWarning}</p>
    <p>Drafts stay only in this page's memory, not browser storage or disk. Reloading or leaving loses an unfinished draft.
      Preview and navigation create no workspace files; only explicit confirmation creates records.</p>
    {session && <p className="setup-workspace">Selected storage: <strong>{session.workspace}</strong>.
      Use operator-controlled local storage. Git ignore is not encryption, access control or a backup.</p>}
    {error && <div role="alert" tabIndex={-1} ref={errorSummary} className="investment-warnings">{error}</div>}
    {receipt ? <div role="status" className="recording-preview">
      <h2>{receipt.status === "completed" ? "Setup completed — read-only" : "Setup partially completed — preserve the workspace"}</h2>
      <p>{receipt.revision === null ? "No committed journal revision." : `Journal revision: ${receipt.revision}.`} {receipt.message}</p>
      <p>Journal committed: {receipt.committed ? "yes" : "no"}.</p>
      {receipt.status === "completed" ? <>
        <p>Recording remains disabled. Enable later recording separately and arrange a verified backup.</p>
        <button type="button" onClick={onComplete}>Open read-only investment view</button>
      </>       : <p>{receipt.committed ? "The journal is committed, but setup is not fully completed."
        : "No opening journal commit is confirmed; setup is incomplete and operation artifacts may remain."} Do not recreate, delete, overwrite or manually repair them.
      Preserve the workspace and reload for explicit recovery guidance.</p>}
    </div> : blocked ? <div className="investment-warnings">
      <h2>{session.state === "ready" ? "Existing investment records" : "Setup cannot create this workspace"}</h2>
      <p>{session.message}</p>
      {session.state === "ready" ? <button type="button" onClick={onComplete}>Open read-only investment view</button>
        : <p>{session.state === "manual_unopened"
          ? "This journal was already initialized. Use the existing opening workflow; the wizard cannot adopt or overwrite it."
          : session.state === "recoverable"
            ? "An interrupted setup needs explicit recovery. Preserve all artifacts and use the recovery flow offered here. This screen will not silently resume creation."
            : "Preserve existing files and check the terminal for guidance. No automatic repair, deletion or replacement is available here."}</p>}
    </div> : !session ? <p role="status">Waiting for setup status…</p>
      : <>
        <ol className="setup-steps" aria-label="Setup progress">{steps.map((name, index) =>
          (session.state !== "recoverable" || index >= 2) &&
          <li key={name} aria-current={index === step ? "step" : undefined}>{index + 1}. {name}</li>)}</ol>
        <h2>{session.state === "recoverable" && !preview && !recoveryEntry ? "Recovery" : steps[step]}</h2>
        {preview ? <div className="recording-preview" role="group" aria-label="Review setup before confirmation">
          <p>Server-validated preview. Expires at {preview.expires_at} (UTC).</p>
          <p>Portfolio: {preview.profile.portfolio_id}; profile inception (UTC): {preview.profile.created_at}.
            Opening record: {preview.opening.id}.</p>
          <h3>Accounts and cash (GBP)</h3>
          <ul>{preview.profile.accounts.map(a => <li key={a.id}>{a.name} — {a.kind};
            cash GBP {preview.opening.balances_gbp[a.id] ?? "Unavailable"}; ID {a.id}; binding {a.binding}</li>)}</ul>
          <h3>Products</h3>
          {preview.profile.products.length === 0 ? <p>No products included.</p> : <ul>{preview.profile.products.map(p =>
            <li key={p.id}>{p.name} — {p.sleeve}, {p.currency}; quantity increment {p.quantity_increment};
              minimum notional GBP {p.minimum_notional_gbp}; ID {p.id}</li>)}</ul>}
          <h3>Opening holdings</h3>
          {preview.opening.holdings.length === 0 ? <p>No opening holdings declared.</p> : <ul>{preview.opening.holdings.map((h, index) =>
            <li key={index}>{preview.profile.accounts.find(a => a.id === h.account_id)?.name ?? h.account_id} /
              {" "}{preview.profile.products.find(p => p.id === h.product_id)?.name ?? h.product_id}:
              quantity {h.quantity}; unit price {h.price_gbp === null ? "Unknown — not zero" : `GBP ${h.price_gbp}`}</li>)}</ul>}
          <p>Effective (UTC): {preview.opening.timestamp}<br />Available (UTC): {preview.opening.available_at}</p>
          <p>Source evidence: {preview.opening.evidence}</p>
          <p>{preview.report.complete && preview.report.equity_gbp !== null
            ? `Opening valuation: GBP ${preview.report.equity_gbp}`
            : "Opening valuation is incomplete / unavailable; missing prices are not zero."}</p>
          <p>Baseline: {preview.report.baseline_as_of ?? "Unavailable"}. Lifetime gain is unknown.
            All opening roles are unassigned. Policy is null; proposals remain blocked.</p>
          <ul>{[...preview.report.warnings, ...preview.report.limitations].map((warning, index) => <li key={index}>{warning}</li>)}</ul>
          <details className="recording-event">
            <summary>Exact reviewed profile and opening evidence</summary>
            <pre>{JSON.stringify({ profile: preview.profile, opening: preview.opening }, null, 2)}</pre>
          </details>
          <p>{fixedWarning}</p>
          <label className="setup-check"><input type="checkbox" checked={reviewed} disabled={busy || confirmAttempted}
            onChange={event => setReviewed(event.target.checked)} />
            I reviewed every account, product, amount and source clock, and accept the fixed-profile limitation.</label>
          {expired && !confirmAttempted && <p role="alert">Preview expired. Edit and request a fresh preview before confirming.</p>}
          <div className="setup-actions">
            <button type="button" disabled={busy || confirmAttempted} onClick={() => { setPreview(null); setReviewed(false); setStep(2); }}>
              {session.state === "recoverable" && !recoveryEntry ? "Back to recovery guidance" : "Edit evidence"}</button>
            <button type="button" disabled={busy || !reviewed || (expired && !confirmAttempted)} onClick={confirm}>
              {confirmAttempted ? "Retry same confirmation" : "Confirm and create local records"}</button>
          </div>
        </div> : session.state === "recoverable" && !recoveryEntry ? <div className="recording-preview">
          <h3>Interrupted setup — explicit recovery required</h3>
          <p>{session.message}</p>
          <p>Preserve all existing artifacts. Preview verifies the setup receipt and exact existing evidence; it does not create, repair or overwrite records.
            Review the recovered profile and complete opening evidence before confirming recovery.</p>
          <p>If opening evidence was not retained, re-entry is required. Never guess missing amounts.</p>
          {recovery?.can_preview && <button type="button" disabled={busy} onClick={recover}>Preview verified recovery</button>}
          {recovery?.requires_opening && <button type="button" disabled={busy} onClick={reenterOpening}>Re-enter original opening evidence</button>}
          {!recovery || (!recovery.can_preview && !recovery.requires_opening)
            ? <p>Verified recovery metadata is unavailable. Preserve the workspace and check the terminal; no replacement setup can be started here.</p> : null}
        </div> : <form className="recording-form setup-form" onSubmit={step === 2 ? makePreview : next}>
          <fieldset disabled={busy}>
            <legend>{steps[step]}</legend>
            {step === 0 && <>
              <p>Use local aliases, never account numbers or credentials. Choose only the accounts in your supported investment scope.
                Classification does not establish ISA/LISA or other wrapper eligibility.</p>
              {accounts.map((a, index) => <div className="setup-row" key={a.id}>
                <label>Account {index + 1} alias<input required maxLength={120} value={a.name}
                  onChange={event => changeAccount(a.id, { name: event.target.value })} /></label>
                <label>Account {index + 1} kind<select required value={a.kind}
                  onChange={event => { const kind = event.target.value; if (kind === "broker" || kind === "deposit") changeAccount(a.id, { kind }); }}>
                  <option value="">Choose classification</option><option value="deposit">Deposit</option><option value="broker">Broker</option>
                </select></label>
                <button type="button" disabled={accounts.length === 1} onClick={() => {
                  changed(); setAccounts(rows => rows.filter(row => row.id !== a.id));
                  setHoldings(rows => rows.filter(row => row.account !== a.id));
                }}>Remove account {index + 1}</button>
              </div>)}
              <button type="button" disabled={accounts.length >= 50} onClick={() => { changed(); setAccounts(rows => [...rows, newAccount()]); }}>Add account</button>
              <label className="setup-check"><input type="checkbox" required checked={scope}
                onChange={event => { changed(); setScope(event.target.checked); }} />
                These are deliberately selected GBP accounts for this investment journal, not a complete household budget.</label>
            </>}
            {step === 1 && <>
              <p>Deposit-only portfolios need no products. Supported products are GBP-denominated equity or bond holdings only.
                Copy increments and minimum notionals from evidence; no provider lookup or guessed metadata is supplied.</p>
              {products.map((p, index) => <div className="setup-row" key={p.id}>
                <label>Product {index + 1} name<input required maxLength={120} value={p.name}
                  onChange={event => changeProduct(p.id, { name: event.target.value })} /></label>
                <label>Product {index + 1} sleeve<select required value={p.sleeve} onChange={event => {
                  const sleeve = event.target.value; if (sleeve === "equity" || sleeve === "bond") changeProduct(p.id, { sleeve });
                }}><option value="">Choose sleeve</option><option value="equity">Equity</option><option value="bond">Bond</option></select></label>
                <label>Product {index + 1} denomination<select required value={p.currency} onChange={event => {
                  if (event.target.value === "GBP") changeProduct(p.id, { currency: "GBP" });
                }}><option value="">Confirm currency</option><option value="GBP">GBP</option></select></label>
                <label>Product {index + 1} quantity increment<input required inputMode="decimal" maxLength={34} value={p.increment}
                  onChange={event => changeProduct(p.id, { increment: event.target.value })} /></label>
                <label>Product {index + 1} minimum notional (GBP)<input required inputMode="decimal" maxLength={34} value={p.minimum}
                  onChange={event => changeProduct(p.id, { minimum: event.target.value })} /></label>
                <button type="button" onClick={() => { changed(); setProducts(rows => rows.filter(row => row.id !== p.id));
                  setHoldings(rows => rows.filter(row => row.product !== p.id)); }}>Remove product {index + 1}</button>
              </div>)}
              <button type="button" disabled={products.length >= 100} onClick={() => { changed(); setProducts(rows => [...rows,
                { id: identifier("product"), name: "", sleeve: "", currency: "", increment: "", minimum: "" }]); }}>Add product</button>
            </>}
            {step === 2 && <>
              {recoveryEntry && recovery && <section aria-label="Recovered fixed profile">
                <h3>Original profile — locked for recovery</h3>
                <p>Re-enter the exact originally confirmed cash, holdings (including order and unknown prices), clocks and source description.
                  Decimal formatting and evidence must match the original confirmed opening hash. The server verifies that match before allowing recovery.</p>
                <p>Portfolio: {recovery.profile.portfolio_id}; inception (UTC): {recovery.profile.created_at}.
                  Original opening ID: {recovery.opening_id}. Account IDs, bindings and product metadata cannot be edited.</p>
                <ul>{recovery.profile.accounts.map(a => <li key={a.id}>{a.name} — {a.kind}, {a.currency}; ID {a.id}; binding {a.binding}</li>)}</ul>
                <ul>{recovery.profile.products.map(p => <li key={p.id}>{p.name} — {p.sleeve}, {p.currency}; ID {p.id};
                  quantity increment {p.quantity_increment}; minimum notional GBP {p.minimum_notional_gbp}</li>)}</ul>
                <details className="recording-event"><summary>Exact original profile</summary>
                  <pre>{JSON.stringify(recovery.profile, null, 2)}</pre></details>
              </section>}
              <p>Use one common effective clock for all balances and holdings, plus when that evidence actually became available.
                Inputs use your browser's local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}); review shows UTC storage.
                Delayed evidence or unknown prices may leave the baseline unavailable. No previous transactions or cost basis are inferred.</p>
              <label>Effective time (local)<input required type="datetime-local" value={effective}
                onChange={event => { changed(); setEffective(event.target.value); }} /></label>
              <label>Evidence available (local)<input required type="datetime-local" value={available}
                onChange={event => { changed(); setAvailable(event.target.value); }} /></label>
              {accounts.map(a => <label key={a.id}>Cash for {a.name} (GBP)<input required inputMode="decimal" maxLength={34}
                value={a.cash} onChange={event => changeAccount(a.id, { cash: event.target.value })} /></label>)}
              <p>Enter cash for every account, including an explicit evidenced zero when appropriate. Blank is never treated as zero.</p>
              {holdings.map((h, index) => <div className="setup-row" key={h.id}>
                <label>Holding {index + 1} account<select required value={h.account} onChange={event => changeHolding(h.id, { account: event.target.value })}>
                  <option value="">Choose broker</option>{accounts.filter(a => a.kind === "broker").map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                <label>Holding {index + 1} product<select required value={h.product} onChange={event => changeHolding(h.id, { product: event.target.value })}>
                  <option value="">Choose product</option>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
                <label>Holding {index + 1} quantity<input required inputMode="decimal" maxLength={34} value={h.quantity}
                  onChange={event => changeHolding(h.id, { quantity: event.target.value })} /></label>
                <label>Holding {index + 1} unit price (GBP, optional)<input inputMode="decimal" maxLength={34} value={h.price}
                  onChange={event => changeHolding(h.id, { price: event.target.value })} /></label>
                <button type="button" onClick={() => { changed(); setHoldings(rows => rows.filter(row => row.id !== h.id)); }}>Remove holding {index + 1}</button>
              </div>)}
              <button type="button" disabled={!products.length || !accounts.some(a => a.kind === "broker") || holdings.length >= 5000}
                onClick={() => { changed(); setHoldings(rows => [...rows, { id: identifier("holding"), account: "", product: "", quantity: "", price: "" }]); }}>Add holding</button>
              <p>Declare all included long holdings. Leave only the price blank when unknown; this submits null, not zero. No rows means no holdings declared.</p>
              <label>Source evidence description<textarea required maxLength={200} value={evidence}
                onChange={event => { changed(); setEvidence(event.target.value); }} /></label>
              <p>Describe the source and scope (maximum 200 characters); do not paste account numbers, credentials or full statements.</p>
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
