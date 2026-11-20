import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation } from "react-router-dom";
import { reviewTimestamp } from "./api";
import { localClock } from "./recording";
import {
  setupConfirm, setupPreview, setupRecoveryPreview, setupSession,
  type SetupConfirmation, type SetupOpening, type SetupPreview, type SetupProfile, type SetupSession,
} from "./setup-api";
import { EToroPortfolioPanel } from "./EToroPortfolio";

type Account = { id: string; binding: string; name: string; kind: "" | "broker" | "deposit"; cash: string };
type Product = { id: string; name: string; sleeve: "" | "equity" | "bond";
  currency: "" | "GBP"; increment: string; minimum: string };
type Holding = { id: string; account: string; product: string; quantity: string; price: string };
const identifier = (prefix: string) => `${prefix}-${crypto.randomUUID()}`;
const newAccount = (): Account => ({ id: identifier("account"), binding: identifier("local"),
  name: "", kind: "", cash: "" });
const steps = ["Accounts", "Investments held (if any)", "Starting balances", "Review and save"];
const fixedWarning = "Starting accounts and figures cannot be edited in place. Later changes use a separate, previewed recording process.";
const SETUP_DRAFT_PREFIX = "goldscout.setup-draft.v1.";

type SetupDraft = {
  version: 1;
  workspace: string;
  portfolioId: string;
  openingId: string;
  accounts: Account[];
  products: Product[];
  holdings: Holding[];
  effective: string;
  available: string;
  evidence: string;
  scope: boolean;
  step: number;
};

function setupDraftKey(workspace: string) {
  return `${SETUP_DRAFT_PREFIX}${encodeURIComponent(workspace)}`;
}

function isSetupDraft(value: unknown, workspace: string): value is SetupDraft {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const draft = value as Record<string, unknown>;
  const text = (item: unknown, maximum: number) =>
    typeof item === "string" && item.length <= maximum;
  const accountIds = Array.isArray(draft.accounts) ? draft.accounts
    .map(item => typeof item === "object" && item !== null ? item.id : null) : [];
  const productIds = Array.isArray(draft.products) ? draft.products
    .map(item => typeof item === "object" && item !== null ? item.id : null) : [];
  const holdingIds = Array.isArray(draft.holdings) ? draft.holdings
    .map(item => typeof item === "object" && item !== null ? item.id : null) : [];
  return Object.keys(draft).sort().join(",") ===
    "accounts,available,effective,evidence,holdings,openingId,portfolioId,products,scope,step,version,workspace" &&
    draft.version === 1 && draft.workspace === workspace &&
    text(draft.portfolioId, 200) && text(draft.openingId, 200) &&
    Array.isArray(draft.accounts) && draft.accounts.length >= 1 && draft.accounts.length <= 50 &&
    accountIds.every(id => typeof id === "string" && id.length > 0) &&
    new Set(accountIds).size === accountIds.length &&
    draft.accounts.every(item => typeof item === "object" && item !== null &&
      Object.keys(item).sort().join(",") === "binding,cash,id,kind,name" &&
      text(item.id, 200) && text(item.binding, 200) && text(item.name, 120) &&
      text(item.cash, 100) && (item.kind === "" || item.kind === "broker" || item.kind === "deposit")) &&
    Array.isArray(draft.products) && draft.products.length <= 100 &&
    productIds.every(id => typeof id === "string" && id.length > 0) &&
    new Set(productIds).size === productIds.length &&
    draft.products.every(item => typeof item === "object" && item !== null &&
      Object.keys(item).sort().join(",") === "currency,id,increment,minimum,name,sleeve" &&
      text(item.id, 200) && text(item.name, 120) &&
      (item.sleeve === "" || item.sleeve === "equity" || item.sleeve === "bond") &&
      (item.currency === "" || item.currency === "GBP") &&
      text(item.increment, 100) && text(item.minimum, 100)) &&
    Array.isArray(draft.holdings) && draft.holdings.length <= 5000 &&
    holdingIds.every(id => typeof id === "string" && id.length > 0) &&
    new Set(holdingIds).size === holdingIds.length &&
    draft.holdings.every(item => typeof item === "object" && item !== null &&
      Object.keys(item).sort().join(",") === "account,id,price,product,quantity" &&
      text(item.id, 200) && text(item.account, 200) && text(item.product, 200) &&
      text(item.quantity, 100) && text(item.price, 100)) &&
    text(draft.effective, 100) && text(draft.available, 100) &&
    text(draft.evidence, 200) && typeof draft.scope === "boolean" &&
    Number.isInteger(draft.step) && Number(draft.step) >= 0 && Number(draft.step) <= 2;
}

function amountIssue(value: string, label: string, positive = false): string | null {
  if (!/^(?:0|[1-9]\d{0,14})(?:\.\d{1,8})?$/.test(value) ||
      (positive && !/[1-9]/.test(value)))
    return `${label}: enter a number ${positive ? "greater than zero" : "of zero or more"}, with up to eight decimal places. Enter 0 only if your source confirms zero.`;
  return null;
}

function amount(value: string, label: string, positive = false): string {
  const issue = amountIssue(value, label, positive);
  if (issue) throw new Error(issue);
  return value;
}

const fieldTarget = (key: string) => `setup-field-${key}`;

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
  const location = useLocation();
  const [session, setSession] = useState<SetupSession | null>(null);
  const [accounts, setAccounts] = useState<Account[]>(() => [newAccount()]);
  const [products, setProducts] = useState<Product[]>([]);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [effective, setEffective] = useState("");
  const [available, setAvailable] = useState("");
  const [evidence, setEvidence] = useState("");
  const [scope, setScope] = useState(false);
  const [holdingsCoverage, setHoldingsCoverage] = useState<Record<string, boolean>>({});
  const [openingContextConfirmed, setOpeningContextConfirmed] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [step, setStep] = useState(0);
  const [preview, setPreview] = useState<SetupPreview | null>(null);
  const [receipt, setReceipt] = useState<SetupConfirmation | null>(null);
  const [confirmAttempted, setConfirmAttempted] = useState(false);
  const [recoveryEntry, setRecoveryEntry] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [draftStorageError, setDraftStorageError] = useState(false);
  const [draftDiscardAvailable, setDraftDiscardAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const lock = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const stepHeading = useRef<HTMLHeadingElement>(null);
  const errorSummary = useRef<HTMLDivElement>(null);
  const portfolioId = useRef(identifier("portfolio"));
  const openingId = useRef(identifier("opening"));
  const draftReady = useRef(false);
  const recovery = session?.state === "recoverable" ? session.recovery : undefined;
  const investmentAccounts = accounts.filter(account => account.kind === "broker");

  const clearFieldError = (key: string) => setFieldErrors(current => {
    if (!current[key]) return current;
    const next = { ...current };
    delete next[key];
    return next;
  });
  const validateAmountField = (key: string, value: string, label: string, positive = false) => {
    const issue = value === "" ? null : amountIssue(value, label, positive);
    setFieldErrors(current => {
      const next = { ...current };
      if (issue) next[key] = issue;
      else delete next[key];
      return next;
    });
  };

  useEffect(() => {
    let active = true;
    setupSession().then(value => { if (active) setSession(value); })
      .catch(() => { if (active) setError("Setup status is unavailable. Reload to retry; no creation has been requested."); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    const unsavedRecovery = session?.state === "recoverable";
    if (!dirty || receipt?.status === "completed" || (!draftStorageError && !unsavedRecovery)) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    const abandon = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      const message = unsavedRecovery
        ? "Recovery figures are not saved in this browser. Leave and lose the re-entered progress?"
        : "The draft could not be saved in this browser. Leave and lose the unsaved progress?";
      if (link && !window.confirm(message))
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    document.addEventListener("click", abandon, true);
    return () => { window.removeEventListener("beforeunload", warn); document.removeEventListener("click", abandon, true); };
  }, [dirty, draftStorageError, receipt, session?.state]);
  useEffect(() => {
    if (!session) return;
    draftReady.current = false;
    if (session.state !== "empty") return;
    let storedDraftExists = false;
    try {
      const raw = window.localStorage.getItem(setupDraftKey(session.workspace));
      storedDraftExists = raw !== null;
      if (raw !== null) {
        if (raw.length > 1_000_000) throw new Error("Saved setup draft is too large to restore.");
        const value: unknown = JSON.parse(raw);
        if (!isSetupDraft(value, session.workspace))
          throw new Error("Saved setup draft is invalid. It was left unchanged.");
        portfolioId.current = value.portfolioId;
        openingId.current = value.openingId;
        setAccounts(value.accounts);
        setProducts(value.products);
        setHoldings(value.holdings);
        setEffective(value.effective);
        setAvailable(value.available);
        setEvidence(value.evidence);
        setScope(value.scope);
        setStep(value.step);
        setDirty(true);
      }
      setDraftStorageError(false);
      setDraftDiscardAvailable(false);
      draftReady.current = true;
    } catch (failure) {
      setDraftStorageError(true);
      setDraftDiscardAvailable(storedDraftExists);
      setError(failure instanceof Error
        ? failure.message : "The saved setup draft could not be read. It was not changed.");
    }
  }, [session?.state, session?.workspace]);
  useEffect(() => {
    if (!draftReady.current || !dirty || session?.state !== "empty" ||
        recoveryEntry || receipt?.status === "completed") return;
    try {
      const draft: SetupDraft = {
        version: 1, workspace: session.workspace, portfolioId: portfolioId.current,
        openingId: openingId.current, accounts, products, holdings, effective,
        available, evidence, scope, step: Math.min(step, 2),
      };
      const serialized = JSON.stringify(draft);
      if (serialized.length > 1_000_000) throw new Error("Setup draft exceeds the browser storage limit.");
      window.localStorage.setItem(setupDraftKey(session.workspace), serialized);
      setDraftStorageError(false);
    } catch {
      setDraftStorageError(true);
    }
  }, [accounts, products, holdings, effective, available, evidence, scope, step,
    dirty, recoveryEntry, receipt, session?.state, session?.workspace]);
  useEffect(() => { (stepHeading.current ?? heading.current)?.focus(); }, [step, preview, receipt, session?.enabled]);
  useEffect(() => { if (error) errorSummary.current?.focus(); }, [error]);
  useEffect(() => {
    if (location.hash !== "#setup-etoro") return;
    const target = document.getElementById("setup-etoro");
    if (target instanceof HTMLDetailsElement) {
      target.open = true;
      target.scrollIntoView();
    }
  }, [location.hash, session?.state]);
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
  const changed = (coverageToClear: string[] = []) => {
    setDirty(true); setPreview(null); setOpeningContextConfirmed(false); setError("");
    if (coverageToClear.length) setHoldingsCoverage(current => {
      const next = { ...current };
      coverageToClear.forEach(id => { delete next[id]; });
      return next;
    });
  };
  const changeAccount = (id: string, patch: Partial<Account>) => {
    const account = accounts.find(row => row.id === id);
    const invalidatedHoldings = account?.kind === "broker" && patch.kind === "deposit"
      ? holdings.filter(holding => holding.account === id) : [];
    if (invalidatedHoldings.length > 0 && !window.confirm(
      `Changing "${account?.name}" to a savings / deposit account will remove its ${invalidatedHoldings.length} holding ${invalidatedHoldings.length === 1 ? "row" : "rows"}. Continue?`,
    )) return;
    changed(account && patch.kind && account.kind !== patch.kind ? [id] : []);
    if (invalidatedHoldings.length > 0)
      setHoldings(rows => rows.filter(holding => holding.account !== id));
    setAccounts(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const changeProduct = (id: string, patch: Partial<Product>) => {
    changed(); setProducts(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const changeHolding = (id: string, patch: Partial<Holding>) => {
    const holding = holdings.find(row => row.id === id);
    const affectedAccounts = holding && (
      patch.account !== undefined && holding.account !== patch.account ||
      patch.product !== undefined && holding.product !== patch.product
    ) ? [holding.account, patch.account ?? holding.account].filter(Boolean) : [];
    changed(affectedAccounts); setHoldings(rows => rows.map(row => row.id === id ? { ...row, ...patch } : row));
  };
  const next = (event: FormEvent) => {
    event.preventDefault();
    if (!scope) { setError("Confirm that you have chosen the GBP accounts you want to track."); return; }
    if (step === 1) {
      const productIssues: Record<string, string> = {};
      products.forEach(product => {
        const incrementKey = `product-${product.id}-increment`;
        const minimumKey = `product-${product.id}-minimum`;
        const incrementIssue = amountIssue(product.increment, `${product.name || "Investment"} smallest quantity step`, true);
        const minimumIssue = amountIssue(product.minimum, `${product.name || "Investment"} minimum purchase`);
        if (incrementIssue) productIssues[incrementKey] = incrementIssue;
        if (minimumIssue) productIssues[minimumKey] = minimumIssue;
      });
      setFieldErrors(current => {
        const nextErrors = { ...current };
        products.forEach(product => {
          delete nextErrors[`product-${product.id}-increment`];
          delete nextErrors[`product-${product.id}-minimum`];
        });
        return { ...nextErrors, ...productIssues };
      });
      if (Object.keys(productIssues).length) {
        setError("Check the highlighted investment details before continuing.");
        return;
      }
    }
    setError("");
    setStep(value => value === 0 && !accounts.some(account => account.kind === "broker") ? 2 : value + 1);
  };
  const makePreview = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (session?.state === "recoverable" && (!recoveryEntry || !recovery?.requires_opening))
        throw new Error("The original setup details are unavailable. Keep all setup files unchanged and reload for recovery guidance.");
      const timestamp = recoveryEntry ? utc(effective, "Balances as of") : new Date().toISOString();
      const availableAt = recoveryEntry ? utc(available, "Source available from") : timestamp;
      if (availableAt < timestamp) throw new Error('"Source available from" cannot be earlier than "Balances as of". Check the dates against your source; do not guess.');
      if (!openingContextConfirmed)
        throw new Error(recoveryEntry
          ? "Confirm that the re-entered details match the original starting record before requesting a recovery preview."
          : "Confirm that all starting figures are current and reflect the same point in time before requesting a preview.");
      if (investmentAccounts.some(account => !holdingsCoverage[account.id]))
        throw new Error("Confirm that you have listed all holdings for each investment account, including accounts with none.");
      const amountIssues: Record<string, string> = {};
      accounts.forEach(account => {
        const key = `account-${account.id}-cash`;
        const issue = amountIssue(account.cash, `${account.name} cash`);
        if (issue) amountIssues[key] = issue;
      });
      holdings.forEach(holding => {
        const quantityKey = `holding-${holding.id}-quantity`;
        const quantityIssue = amountIssue(holding.quantity, "Holding quantity", true);
        if (quantityIssue) amountIssues[quantityKey] = quantityIssue;
        if (holding.price !== "") {
          const priceKey = `holding-${holding.id}-price`;
          const priceIssue = amountIssue(holding.price, "Unit price", true);
          if (priceIssue) amountIssues[priceKey] = priceIssue;
        }
      });
      setFieldErrors(current => {
        const nextErrors = { ...current };
        Object.keys(nextErrors).filter(key => key.startsWith("account-") ||
          key.startsWith("holding-")).forEach(key => { delete nextErrors[key]; });
        return { ...nextErrors, ...amountIssues };
      });
      if (Object.keys(amountIssues).length)
        throw new Error(`Check the highlighted amounts before requesting a preview. ${Object.values(amountIssues).join(" ")}`);
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
      setOpeningContextConfirmed(false);
      const result = await setupPreview(session?.csrf ?? "", {
        request_id: identifier("request"),
        profile: recoveryEntry && recovery ? recovery.profile : { schema_version: 2, portfolio_id: portfolioId.current, currency: "GBP", created_at: timestamp,
          accounts: wireAccounts, products: wireProducts, policy: null },
        opening,
      });
      setPreview(result); setNow(Date.now()); setStep(3);
    });
  };
  const confirm = () => {
    if (!preview || (!confirmAttempted && Date.parse(preview.expires_at) <= Date.now())) return;
    void run(async () => {
      setConfirmAttempted(true);
      try {
        const result = await setupConfirm(session?.csrf ?? "", preview.preview_id);
        setReceipt(result);
        if (result.status === "completed") {
          setDirty(false);
          try {
            if (session) window.localStorage.removeItem(setupDraftKey(session.workspace));
            setDraftStorageError(false);
            setDraftDiscardAvailable(false);
          } catch {
            setDraftStorageError(true);
            setDraftDiscardAvailable(true);
            setError("Setup completed, but this browser could not clear its saved draft. The journal was saved; remove the leftover draft from this browser's storage before starting another setup.");
          }
        }
      } catch {
        throw new Error("The save outcome is unknown or the request was refused. Records may already have been saved. Do not start another setup or delete files. Use Retry same confirmation to check this exact request, or reload for recovery guidance. Error details may be in the terminal window used to start the dashboard.");
      }
    });
  };
  const recover = () => {
    void run(async () => {
      try {
        const result = await setupRecoveryPreview(session?.csrf ?? "");
        setPreview(result); setNow(Date.now()); setStep(3); setDirty(true);
      } catch {
        throw new Error("The previous setup could not be verified for recovery. Keep all setup files unchanged. If starting figures were not retained, re-entering and explicitly reviewing the original figures is required. Do not guess balances or create a replacement. Check the terminal window used to start the dashboard for error details.");
      }
    });
  };
  const discardSetupDraft = () => {
    try {
      if (session) window.localStorage.removeItem(setupDraftKey(session.workspace));
      window.location.reload();
    } catch {
      setError("The saved browser draft could not be cleared. Keep this page open; no journal records were changed.");
    }
  };
  const reenterOpening = () => {
    if (!recovery?.requires_opening || confirmAttempted) return;
    if (!dirty) {
      setAccounts(recovery.profile.accounts.map(a => ({ ...a, cash: "" })));
      setProducts(recovery.profile.products.map(p => ({
        id: p.id, name: p.name, sleeve: p.sleeve, currency: p.currency,
        increment: p.quantity_increment, minimum: p.minimum_notional_gbp,
      })));
      setHoldings([]); setEffective(""); setAvailable(""); setEvidence("");
      setOpeningContextConfirmed(false); setHoldingsCoverage({}); setScope(true);
    }
    setPreview(null); setFieldErrors({}); setError("");
    setStep(2); setRecoveryEntry(true); setDirty(true);
  };
  const expired = preview !== null && Date.parse(preview.expires_at) <= now;
  const blocked = session && (!session.enabled || !["empty", "recoverable"].includes(session.state));
  const progressSteps = steps.map((name, index) => ({ name, index })).filter(({ index }) =>
    (index !== 1 || investmentAccounts.length > 0) &&
    (session?.state !== "recoverable" || index >= 2));

  return <section className="investment-setup" aria-label="Investment setup" aria-busy={busy}>
    <h1 ref={heading} tabIndex={-1}>Let's set up your portfolio</h1>
    {!receipt && session && !blocked && <p className="setup-draft-note">{session.state === "recoverable"
      ? "Re-entered recovery figures are not saved as a draft. Existing setup files may already contain saved records."
      : draftStorageError
        ? "This browser could not save the private draft. Keep this page open or finish before leaving."
        : "Your private setup draft is saved in this browser on this computer. Previews and confirmations are never resumed; check starting figures and holdings again after returning."}</p>}
    {!receipt && !preview && !blocked && !recoveryEntry && session?.state === "recoverable" && step === 0 && <p className="setup-intro">
      Check or exactly reconstruct the original setup. Do not start a replacement portfolio.</p>}
    {!receipt && !preview && !blocked && session?.state === "empty" && step === 0 && <>
      <p className="setup-intro">Add the GBP accounts you want to track, then enter their current cash and holdings.</p>
      <section className="setup-preflight" aria-labelledby="setup-preflight-title">
      <h2 id="setup-preflight-title">Have these ready</h2>
      <ul>
        <li>Cash balances and a complete list of holdings and quantities for the same date and time.</li>
        <li>For each investment, its GBP pricing, supported quantity step and minimum purchase, checked with your provider.</li>
        <li>The dated sources you used. Do not enter account numbers, passwords or full statements.</li>
      </ul>
      </section>
    </>}
    <details className="help-disclosure">
      <summary>Before you start</summary>
      <p>Use nicknames, not account numbers, passwords or credentials.</p>
      <p>{fixedWarning}</p>
      {session && <p className="setup-workspace">Saved on this computer in: <strong>{session.workspace}</strong>.</p>}
      <p>Keep this computer private and arrange separate backups. GoldScout has no sign-in.</p>
    </details>
    <details className="help-disclosure" id="setup-etoro">
      <summary>Optional: eToro connection</summary>
      <EToroPortfolioPanel revokeOnly={!session || Boolean(blocked) || Boolean(receipt) ||
        session.state !== "empty" || confirmAttempted || busy} />
    </details>
    {error && <div role="alert" tabIndex={-1} ref={errorSummary} className="investment-warnings">
      <p>{error}</p>
      {draftDiscardAvailable && <button type="button" onClick={discardSetupDraft}>Discard saved setup draft</button>}
      {Object.entries(fieldErrors).length > 0 && <ul aria-label="Fields to correct">
        {Object.entries(fieldErrors).map(([key, message]) => <li key={key}>
          <a href={`#${fieldTarget(key)}`} onClick={event => {
            event.preventDefault();
            const target = document.getElementById(fieldTarget(key));
            target?.focus();
            target?.scrollIntoView({ block: "center" });
          }}>{message}</a>
        </li>)}
      </ul>}
    </div>}
    {receipt ? <div role="status" className="recording-preview">
      <h2>{receipt.status === "completed" ? "Setup completed" : "Setup partially completed — preserve the workspace"}</h2>
      <p>{receipt.committed
        ? "Your starting records were saved."
        : "Saving your starting records has not been confirmed. Some setup files may remain; do not replace them."}</p>
      {receipt.message && <p>{receipt.message}</p>}
      <details className="help-disclosure"><summary>Save receipt and technical details</summary>
        <p>{receipt.revision === null ? "No committed journal revision." : `Journal revision: ${receipt.revision}.`}</p>
        <p>Journal committed: {receipt.committed ? "yes" : "no"}.</p>
      </details>
      {receipt.status === "completed" ? <>
        <p>Open your portfolio, then choose Add a record for future updates.</p>
        <button type="button" onClick={onComplete}>Open portfolio</button>
      </>       : <p>Setup did not finish completely. Keep the whole setup folder unchanged and reload this page for recovery guidance.
        Do not recreate, delete, overwrite or manually repair any files.</p>}
    </div> : blocked ? <div className="investment-warnings">
      <h2>{session.state === "ready" ? "Existing investment records" : "Setup cannot create this workspace"}</h2>
      {session.message && <details className="help-disclosure"><summary>Technical reason setup cannot continue</summary><p>{session.message}</p></details>}
      {session.state === "ready" ? <button type="button" onClick={onComplete}>Open portfolio</button>
        : <p>{session.state === "manual_unopened"
          ? "These records were already initialized but have no starting balances. The wizard cannot adopt or overwrite them. Keep the files unchanged and use the existing manual recording workflow for this journal."
          : session.state === "recoverable"
            ? "A previous setup was interrupted. Keep all files unchanged and use only the recovery options offered here. Nothing resumes automatically."
            : "Keep existing files unchanged. Check the terminal window used to start the dashboard for error details. No automatic repair, deletion or replacement is available here."}</p>}
    </div> : !session ? <p role="status">Waiting for setup status…</p>
      : <>
        <ol className="setup-steps" aria-label="Setup progress">{progressSteps.map(({ name, index }, progressIndex) =>
          <li key={name} aria-current={index === step ? "step" : undefined}>{progressIndex + 1}. {name}</li>)}</ol>
        <h2 ref={stepHeading} tabIndex={-1}>
          {session.state === "recoverable" && !preview && !recoveryEntry ? "Recovery" : steps[step]}
        </h2>
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
          <h3>Accounts, cash and holdings (GBP)</h3>
          <ul>{preview.profile.accounts.map(account => {
            const holdings = preview.opening.holdings.filter(holding => holding.account_id === account.id);
            const showCoverage = (session.state !== "recoverable" || recoveryEntry) && account.kind === "broker";
            return <li key={account.id}><strong>{account.name}</strong> — {account.kind === "deposit" ? "Savings / deposit" : "Investment / broker"};
              {" "}cash {preview.opening.balances_gbp[account.id] ?? "Unavailable"}
              {showCoverage && `; holdings ${holdings.length ? "confirmed complete" : "confirmed none"}`}
              {holdings.length > 0 && <ul>{holdings.map((holding, index) => <li key={index}>
                {preview.profile.products.find(product => product.id === holding.product_id)?.name ?? holding.product_id}:
                {" "}{holding.quantity} units; price {holding.price_gbp === null ? "unknown (not zero)" : `GBP ${holding.price_gbp}`}
              </li>)}</ul>}
            </li>;
          })}</ul>
          {preview.profile.products.length > 0 && <>
            <h3>Investment rules</h3>
            <ul>{preview.profile.products.map(p =>
              <li key={p.id}>{p.name} — {p.sleeve === "equity" ? "Shares / equity" : "Bonds"}, {p.currency};
                smallest quantity step {p.quantity_increment}; minimum purchase GBP {p.minimum_notional_gbp}</li>)}</ul>
          </>}
          <dl className="review-dates">
            <div><dt>{session.state === "recoverable" ? "Balances and prices as of" : "Starting figures recorded at"}</dt>
              <dd>{reviewTimestamp(preview.opening.timestamp)}</dd></div>
            {session.state === "recoverable" &&
              <div><dt>Source available from</dt><dd>{reviewTimestamp(preview.opening.available_at)}</dd></div>}
          </dl>
          <p>Where these figures came from: {preview.opening.evidence}</p>
          <p>Performance tracking starts from: {preview.report.baseline_as_of === null ? "Not available yet" : reviewTimestamp(preview.report.baseline_as_of)}.
            Profit or loss before this date is unknown.</p>
          <p>Starting funds are unassigned; you can add optional roles later.</p>
          <details className="recording-event">
            <summary>Technical details: exact profile and starting record</summary>
            <p>Portfolio: {preview.profile.portfolio_id}; profile inception (UTC): {preview.profile.created_at}.
              Opening record: {preview.opening.id}. Account IDs and bindings are included below.</p>
            <pre>{JSON.stringify({ profile: preview.profile, opening: preview.opening }, null, 2)}</pre>
          </details>
          <p>Check the accounts, holdings, amounts, dates and source before confirming. Starting records cannot be edited in place.</p>
          {expired && !confirmAttempted && <p role="alert">Preview expired. Edit and request a fresh preview before confirming.</p>}
          <div className="setup-actions">
            <button type="button" disabled={busy || confirmAttempted} onClick={() => { setPreview(null); setStep(2); }}>
              {session.state === "recoverable" && !recoveryEntry ? "Back to recovery guidance" : "Edit evidence"}</button>
            <button type="button" disabled={busy || (expired && !confirmAttempted)} onClick={confirm}>
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
              <p>Add the GBP savings and investment accounts you want to track. Use nicknames, never account numbers or passwords.</p>
              {accounts.map((a, index) => <div className="setup-row" key={a.id}>
                <label>Account {index + 1} name<input required maxLength={120} value={a.name}
                  placeholder="e.g. House savings"
                  onChange={event => changeAccount(a.id, { name: event.target.value })} /></label>
                <label>Account {index + 1} type<select required value={a.kind}
                  onChange={event => { const kind = event.target.value; if (kind === "broker" || kind === "deposit") changeAccount(a.id, { kind }); }}>
                  <option value="">Choose account type</option><option value="deposit">Savings / deposit account</option><option value="broker">Investment / broker account</option>
                </select></label>
                <button type="button" disabled={accounts.length === 1} onClick={() => {
                  const linkedHoldings = holdings.filter(holding => holding.account === a.id);
                  if (linkedHoldings.length > 0 && !window.confirm(
                    `Remove "${a.name}" and its ${linkedHoldings.length} linked holding ${linkedHoldings.length === 1 ? "row" : "rows"}? This cannot be undone in the draft.`,
                  )) return;
                  changed([a.id]); setAccounts(rows => rows.filter(row => row.id !== a.id));
                  setHoldings(rows => rows.filter(row => row.account !== a.id));
                }}>Remove account {index + 1}</button>
              </div>)}
              <button type="button" disabled={accounts.length >= 50} onClick={() => { changed(); setAccounts(rows => [...rows, newAccount()]); }}>Add account</button>
              <label className="setup-check"><input type="checkbox" required checked={scope}
                onChange={event => { changed(); setScope(event.target.checked); }} />
                These are the GBP accounts I want to track in this portfolio.</label>
              <details className="help-disclosure"><summary>Which account type should I choose?</summary>
                <p>Choose savings / deposit for bank savings or a Cash LISA. Choose investment / broker for an account that can hold shares or funds,
                  including its uninvested cash. A Stocks and Shares LISA is an investment / broker account, even if it currently holds only cash.</p>
                <p>This records the account type only; it does not verify LISA status, tax treatment, eligibility or deposit protection.</p>
              </details>
            </>}
            {step === 1 && <>
              <p>Add each share, fund or bond once. If you hold it in multiple accounts, enter it once per account on the next step.</p>
              <p>If your investment accounts only hold cash, continue without adding an investment.</p>
              <p className="position-note">Only GBP-priced shares, funds and bonds are supported. Verify details with your provider.</p>
              <details className="help-disclosure"><summary>Help with investment types and quantity rules</summary>
                <p>Equity means shares or share-based funds. Bonds are debt investments, not protected savings.
                  GBP pricing does not remove any underlying foreign-currency risk.</p>
                <p>The smallest quantity step is the unit increment your provider supports, such as 1 whole unit or 0.001 units.
                  The minimum purchase is the smallest supported purchase value in pounds. Enter 0 only if the provider confirms no minimum.
                  These details help validate recorded quantities; GoldScout cannot place orders. No provider lookup is performed.
                  If you cannot verify a required detail, pause setup rather than inventing it.</p>
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
                <label>Investment {index + 1} smallest quantity step<input required inputMode="decimal" maxLength={34}
                  id={fieldTarget(`product-${p.id}-increment`)} value={p.increment}
                  aria-invalid={Boolean(fieldErrors[`product-${p.id}-increment`])}
                  aria-describedby={`setup-increment-${p.id}${fieldErrors[`product-${p.id}-increment`] ? ` setup-error-product-${p.id}-increment` : ""}`}
                  onBlur={() => validateAmountField(`product-${p.id}-increment`, p.increment, `${p.name || "Investment"} smallest quantity step`, true)}
                  onChange={event => { clearFieldError(`product-${p.id}-increment`); changeProduct(p.id, { increment: event.target.value }); }}
                />
                  <small id={`setup-increment-${p.id}`} className="field-help">The provider's unit step, not how many you own. For example, 1 or 0.001; check your provider.</small></label>
                <label>Investment {index + 1} minimum purchase (GBP)<input required inputMode="decimal" maxLength={34}
                  id={fieldTarget(`product-${p.id}-minimum`)} value={p.minimum}
                  aria-invalid={Boolean(fieldErrors[`product-${p.id}-minimum`])}
                  aria-describedby={`setup-minimum-${p.id}${fieldErrors[`product-${p.id}-minimum`] ? ` setup-error-product-${p.id}-minimum` : ""}`}
                  onBlur={() => validateAmountField(`product-${p.id}-minimum`, p.minimum, `${p.name || "Investment"} minimum purchase`)}
                  onChange={event => { clearFieldError(`product-${p.id}-minimum`); changeProduct(p.id, { minimum: event.target.value }); }}
                />
                  <small id={`setup-minimum-${p.id}`} className="field-help">The provider's smallest purchase in pounds. For example, £10; if unknown, pause rather than guessing.</small></label>
                {fieldErrors[`product-${p.id}-increment`] && <small id={`setup-error-product-${p.id}-increment`} className="field-error">{fieldErrors[`product-${p.id}-increment`]}</small>}
                {fieldErrors[`product-${p.id}-minimum`] && <small id={`setup-error-product-${p.id}-minimum`} className="field-error">{fieldErrors[`product-${p.id}-minimum`]}</small>}
                {holdings.some(holding => holding.product === p.id) &&
                  <p className="field-help">Removing this investment will also remove its {holdings.filter(holding => holding.product === p.id).length} linked holding row(s).</p>}
                <button type="button" onClick={() => {
                  const linkedHoldings = holdings.filter(holding => holding.product === p.id);
                  if (linkedHoldings.length > 0 && !window.confirm(
                    `Remove "${p.name}" and its ${linkedHoldings.length} linked holding ${linkedHoldings.length === 1 ? "row" : "rows"}? This cannot be undone in the draft.`,
                  )) return;
                  changed(investmentAccounts.map(account => account.id));
                  setProducts(rows => rows.filter(row => row.id !== p.id));
                  setHoldings(rows => rows.filter(row => row.product !== p.id));
                }}>Remove investment {index + 1}</button>
              </div>)}
              <button type="button" disabled={products.length >= 100} onClick={() => { changed(investmentAccounts.map(account => account.id)); setProducts(rows => [...rows,
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
              <p>{recoveryEntry
                ? "Re-enter the original cash, holdings and dates exactly as they were recorded. Recovery will reject approximate matches."
                : "Enter current cash and holdings for the same point in time. Setup timestamps the preview and cannot be backdated."}</p>
              {recoveryEntry && <details className="help-disclosure" open>
                <summary>Re-enter the original opening time</summary>
                <p>Recovery must reproduce the original record exactly. Enter its original dates and source details; approximate values are refused.</p>
              </details>}
              {recoveryEntry && <>
                <label>Balances as of (local time)<input required type="datetime-local" value={effective}
                  onChange={event => { changed(); setEffective(event.target.value); }} /></label>
                <label>Source available from (local time)<input required type="datetime-local" value={available}
                  onChange={event => { changed(); setAvailable(event.target.value); }} /></label>
              </>}
              <h3>Cash in each account</h3>
              <p>Enter cash only, not investment value. Enter 0 only if your source confirms no cash.</p>
              {accounts.map(a => <label key={a.id}>Cash for {a.name} (GBP)<input required inputMode="decimal" maxLength={34}
                id={fieldTarget(`account-${a.id}-cash`)} value={a.cash}
                aria-invalid={Boolean(fieldErrors[`account-${a.id}-cash`])}
                aria-describedby={fieldErrors[`account-${a.id}-cash`] ? `setup-error-account-${a.id}-cash` : undefined}
                onBlur={() => validateAmountField(`account-${a.id}-cash`, a.cash, `${a.name} cash`)}
                onChange={event => { clearFieldError(`account-${a.id}-cash`); changeAccount(a.id, { cash: event.target.value }); }} />
                {fieldErrors[`account-${a.id}-cash`] && <small id={`setup-error-account-${a.id}-cash`} className="field-error">{fieldErrors[`account-${a.id}-cash`]}</small>}
              </label>)}
              {investmentAccounts.length === 0
                ? <p>This setup contains only savings / deposit accounts; no investment holdings are needed.</p>
                : <>
              <h3>Investments you own</h3>
              <p>List every holding. A unit price may be blank if unknown; it will not be treated as zero.</p>
              {holdings.map((h, index) => <div className="setup-row" key={h.id}>
                <label>Holding {index + 1} account<select required value={h.account} onChange={event => changeHolding(h.id, { account: event.target.value })}>
                  <option value="">Choose investment account</option>{investmentAccounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
                <label>Holding {index + 1} investment<select required value={h.product} onChange={event => changeHolding(h.id, { product: event.target.value })}>
                  <option value="">Choose investment</option>{products.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
                <label>Holding {index + 1} quantity<input required inputMode="decimal" maxLength={34}
                  id={fieldTarget(`holding-${h.id}-quantity`)} value={h.quantity}
                  aria-invalid={Boolean(fieldErrors[`holding-${h.id}-quantity`])}
                  aria-describedby={fieldErrors[`holding-${h.id}-quantity`] ? `setup-error-holding-${h.id}-quantity` : undefined}
                  onBlur={() => validateAmountField(`holding-${h.id}-quantity`, h.quantity, "Holding quantity", true)}
                  onChange={event => { clearFieldError(`holding-${h.id}-quantity`); changeHolding(h.id, { quantity: event.target.value }); }} />
                  {fieldErrors[`holding-${h.id}-quantity`] && <small id={`setup-error-holding-${h.id}-quantity`} className="field-error">{fieldErrors[`holding-${h.id}-quantity`]}</small>}
                </label>
                <label>Holding {index + 1} unit price (GBP, optional)<input inputMode="decimal" maxLength={34}
                  id={fieldTarget(`holding-${h.id}-price`)} value={h.price}
                  aria-invalid={Boolean(fieldErrors[`holding-${h.id}-price`])}
                  aria-describedby={fieldErrors[`holding-${h.id}-price`] ? `setup-error-holding-${h.id}-price` : undefined}
                  onBlur={() => validateAmountField(`holding-${h.id}-price`, h.price, "Unit price", true)}
                  onChange={event => { clearFieldError(`holding-${h.id}-price`); changeHolding(h.id, { price: event.target.value }); }} />
                  {fieldErrors[`holding-${h.id}-price`] && <small id={`setup-error-holding-${h.id}-price`} className="field-error">{fieldErrors[`holding-${h.id}-price`]}</small>}
                </label>
                <button type="button" onClick={() => { changed([h.account]); setHoldings(rows => rows.filter(row => row.id !== h.id)); }}>Remove holding {index + 1}</button>
              </div>)}
                {!products.length && <p id="setup-no-investments" className="field-help">To add a holding, go back and add its investment first. If your investment accounts only hold cash, leave this list empty and confirm that below.</p>}
                <button type="button" aria-describedby={!products.length ? "setup-no-investments" : undefined}
                  disabled={!products.length || !investmentAccounts.length || holdings.length >= 5000}
                  onClick={() => { changed(investmentAccounts.map(account => account.id)); setHoldings(rows => [...rows, { id: identifier("holding"), account: "", product: "", quantity: "", price: "" }]); }}>Add holding</button>
                </>}
              <h3>Your source</h3>
              <label>Where did these figures come from?<textarea required maxLength={200} value={evidence}
                placeholder="e.g. Savings statement and investment statement, 1 November"
                onChange={event => { changed(); setEvidence(event.target.value); }} /></label>
              <p>Name the dated source and accounts covered. Do not include account numbers, passwords or full statements.</p>
              <label className="setup-check"><input type="checkbox" required checked={openingContextConfirmed}
                onChange={event => { setOpeningContextConfirmed(event.target.checked); setDirty(true); setError(""); }} />
                {recoveryEntry
                  ? "I checked that the re-entered figures, source and dates match the original opening record exactly."
                  : "I checked that every starting figure is current and all accounts and holdings reflect the same point in time."}</label>
              {investmentAccounts.length > 0 && <section aria-label="Investment account holdings check">
                <h3>Confirm holdings</h3>
                <p>For each account, confirm every holding is listed, or that it has none.</p>
                {investmentAccounts.map(account => <label className="setup-check" key={account.id}>
                  <input type="checkbox" checked={Boolean(holdingsCoverage[account.id])}
                    onChange={event => setHoldingsCoverage(current => ({
                      ...current, [account.id]: event.target.checked,
                    }))} />
                  {account.name}: I have listed all holdings, or confirmed there are none.
                </label>)}
              </section>}
            </>}
          </fieldset>
          <div className="setup-actions">
            {recoveryEntry ? <button type="button" disabled={busy} onClick={() => { setError(""); setRecoveryEntry(false); }}>Back to recovery guidance</button>
              : step > 0 && <button type="button" disabled={busy} onClick={() => {
                setError("");
                setStep(value => value === 2 && investmentAccounts.length === 0 ? 0 : value - 1);
              }}>Back</button>}
            <button type="submit" disabled={busy}>{step === 2 ? "Preview setup" : "Continue"}</button>
          </div>
        </form>}
      </>}
  </section>;
}
