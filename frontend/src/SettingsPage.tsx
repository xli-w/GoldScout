import { useEffect, useState } from "react";

export const SETTINGS_QUERY_KEY = ["settings-session"];

export type SettingsSession = {
  enabled: boolean;
  csrf: string | null;
  revision: number | null;
  preferences: {
    compact_mode: boolean;
    review_interval_days: number;
    savings_terms_interval_days: number;
  } | null;
  connection_revision: number | null;
  etoro_credential_revision: string | null;
  connection_settings_enabled: boolean;
  connection_overrides: Record<string, boolean> | null;
};

const connectionNames = [
  ["GOLDSCOUT_ETORO_API_KEY", "eToro API key", "password"],
  ["GOLDSCOUT_ETORO_USER_KEY", "eToro user key", "password"],
  ["TRADER_AI_OPENAI_KEY", "OpenAI API key", "password"],
  ["TRADER_AI_ANTHROPIC_KEY", "Anthropic API key", "password"],
  ["TRADER_AI_GEMINI_KEY", "Gemini API key", "password"],
  ["TRADER_PROXY_URL", "HTTP proxy URL", "text"],
  ["TRADER_DNS_IPV4", "IPv4 DNS server", "text"],
] as const;
const intervals = [7, 14, 30, 90, 180, 365];

export async function fetchSettingsSession(): Promise<SettingsSession> {
  const response = await fetch("/api/v1/settings/session", {
    credentials: "same-origin", cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Settings could not be loaded.");
  const value: unknown = await response.json();
  if (typeof value !== "object" || value === null || !("enabled" in value) ||
      typeof value.enabled !== "boolean" ||
      value.enabled && (!("csrf" in value) || typeof value.csrf !== "string" ||
        !("revision" in value) || typeof value.revision !== "number" ||
        !("connection_revision" in value) || typeof value.connection_revision !== "number" ||
        !("etoro_credential_revision" in value) ||
        typeof value.etoro_credential_revision !== "string" ||
        !("connection_settings_enabled" in value) ||
        typeof value.connection_settings_enabled !== "boolean" ||
        !("preferences" in value) || typeof value.preferences !== "object" ||
        value.preferences === null || !("connection_overrides" in value) ||
        typeof value.connection_overrides !== "object" || value.connection_overrides === null))
    throw new Error("Settings response was invalid.");
  return value as SettingsSession;
}

function intervalName(days: number) {
  return days === 7 ? "Every week" : days === 14 ? "Every two weeks"
    : days === 30 ? "Every month" : days === 90 ? "Every three months"
      : days === 180 ? "Every six months" : "Every year";
}

export function SettingsPage({ session, onSaved, loadError }: {
  session: SettingsSession | undefined; onSaved: () => void; loadError?: boolean;
}) {
  const [compact, setCompact] = useState(false);
  const [reviewInterval, setReviewInterval] = useState(30);
  const [termsInterval, setTermsInterval] = useState(90);
  const [credentialInputs, setCredentialInputs] = useState<Record<string, string>>({});
  const [clearOverrides, setClearOverrides] = useState<Record<string, boolean>>({});
  const [disableProxy, setDisableProxy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!session?.preferences) return;
    setCompact(session.preferences.compact_mode);
    setReviewInterval(session.preferences.review_interval_days);
    setTermsInterval(session.preferences.savings_terms_interval_days);
  }, [session?.revision]);

  async function savePreferences(event: React.FormEvent) {
    event.preventDefault();
    if (!session?.csrf || session.revision === null) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch("/api/v1/settings/preferences", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf },
        body: JSON.stringify({
          expected_revision: session.revision,
          preferences: {
            compact_mode: compact, review_interval_days: reviewInterval,
            savings_terms_interval_days: termsInterval,
          },
        }),
      });
      if (!response.ok) throw new Error("Preferences were not saved. Reload settings and try again.");
      setMessage("Preferences saved.");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Preferences could not be saved.");
    } finally { setBusy(false); }
  }

  async function saveConnections(event: React.FormEvent) {
    event.preventDefault();
    if (!session?.csrf || !session.connection_settings_enabled || session.connection_revision === null) return;
    const updates: Record<string, string> = {};
    for (const [name] of connectionNames) {
      const value = credentialInputs[name] ?? "";
      if (value !== "") updates[name] = value;
    }
    if (disableProxy) updates.TRADER_PROXY_URL = "";
    const clearNames = Object.entries(clearOverrides)
      .filter(([, checked]) => checked).map(([name]) => name);
    if (Object.keys(updates).length === 0 && clearNames.length === 0) {
      setError("Enter a new value or select an override to clear.");
      return;
    }
    if (clearNames.some(name => name in updates)) {
      setError("Enter a new value or clear the saved override, not both.");
      return;
    }
    if (updates.TRADER_PROXY_URL === "" && !disableProxy) {
      setError("The proxy URL is empty. Select explicit proxy disable only if you intend direct connections.");
      return;
    }
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch("/api/v1/settings/connections", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf },
        body: JSON.stringify({
          expected_revision: session.connection_revision, updates, clear_names: clearNames,
        }),
      });
      if (!response.ok) throw new Error("Connection settings were not saved. Reload settings and try again.");
      const result: unknown = await response.json();
      const reauthorization = typeof result === "object" && result !== null &&
        "reauthorization_required" in result && result.reauthorization_required === true;
      setCredentialInputs({});
      setClearOverrides({});
      setDisableProxy(false);
      setMessage(reauthorization
        ? "Connection settings saved. Re-enable eToro before another read; the previous activation no longer applies."
        : "Connection settings saved. Changes apply to the next provider request; an in-flight request may finish.");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Connection settings could not be saved.");
    } finally { setBusy(false); }
  }

  if (!session && loadError)
    return <p role="alert">Settings are unavailable. <button type="button" onClick={onSaved}>Retry</button></p>;
  if (!session) return <p role="status">Loading settings…</p>;
  if (!session.enabled || !session.preferences || !session.connection_overrides)
    return <p role="status">Settings are available for a selected recorded workspace only.</p>;
  const connectionOverrides = session.connection_overrides;

  return <section className="settings-page" aria-label="Settings">
    <p className="page-description">Settings stay on this computer and do not change journal records, collect data, or enable trades.</p>
    {(error || message) && <p role={error ? "alert" : "status"}>{error || message}</p>}
    <form className="history-group settings-form" onSubmit={savePreferences}>
      <h2>Display and review defaults</h2>
      <label className="settings-toggle">
        <input type="checkbox" checked={compact} onChange={event => setCompact(event.target.checked)} />
        Compact display
      </label>
      <p className="position-note">Review defaults apply only where an account has no saved interval. Existing account-specific choices are kept.</p>
      <label>Default account review interval
        <select value={reviewInterval} onChange={event => setReviewInterval(Number(event.target.value))}>
          {intervals.map(days => <option key={days} value={days}>{intervalName(days)}</option>)}
        </select>
      </label>
      <label>Default savings-terms recheck interval
        <select value={termsInterval} onChange={event => setTermsInterval(Number(event.target.value))}>
          {intervals.map(days => <option key={days} value={days}>{intervalName(days)}</option>)}
        </select>
      </label>
      <button type="submit" disabled={busy}>Save preferences</button>
    </form>

    {session.connection_settings_enabled ? <form className="history-group settings-form" onSubmit={saveConnections}>
      <h2>Private connections</h2>
      <p className="position-note">New values are write-only here. Saved values are never sent back to the browser. Leave a field blank to keep its current setting. Clearing a saved override falls back to the existing private file or environment setting. Changes are plaintext in the project folder; restrict access to this computer.</p>
      {connectionNames.map(([name, label, type]) => <div className="settings-secret" key={name}>
        <label>{label}
          <input type={type} autoComplete="new-password" spellCheck={false}
            value={credentialInputs[name] ?? ""}
            onChange={event => {
              setCredentialInputs({ ...credentialInputs, [name]: event.target.value });
              setClearOverrides({ ...clearOverrides, [name]: false });
              if (name === "TRADER_PROXY_URL") setDisableProxy(false);
            }} />
        </label>
        <span role="status">{connectionOverrides[name]
          ? "A project setting is saved" : "No project override; existing configuration may still apply"}</span>
        {name === "TRADER_PROXY_URL" && <label className="settings-toggle">
          <input type="checkbox" checked={disableProxy} onChange={event => {
            setDisableProxy(event.target.checked);
            if (event.target.checked) setClearOverrides({ ...clearOverrides, [name]: false });
          }} />
          Explicitly disable the proxy (may allow direct connections)
        </label>}
        {connectionOverrides[name] && <label className="settings-toggle">
          <input type="checkbox" checked={clearOverrides[name] ?? false}
            onChange={event => {
              setClearOverrides({ ...clearOverrides, [name]: event.target.checked });
              if (name === "TRADER_PROXY_URL" && event.target.checked) setDisableProxy(false);
            }} />
          Clear this project override
        </label>}
      </div>)}
      <button type="submit" disabled={busy}>Save connection settings</button>
      <p className="position-note">Changing either eToro key requires enabling the eToro connection again. Saving credentials does not make a provider request.</p>
      <p className="position-note">Current eToro credential revision: {session.etoro_credential_revision}. A separate CLI watch pins its revision and must be restarted after a key change.</p>
      <p className="position-note">Close older GoldScout processes before changing connections. A request already dispatched cannot be recalled. The private connections file is ignored when PYTHON_DOTENV_DISABLED=1.</p>
    </form> : <p role="status">Connection settings are disabled because PYTHON_DOTENV_DISABLED=1. Existing project values were not read or changed.</p>}
  </section>;
}
