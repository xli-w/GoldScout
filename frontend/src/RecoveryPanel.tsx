import { useEffect, useRef, useState, type ChangeEvent } from "react";

type RecoverySession = {
  enabled: boolean; csrf: string | null; archive_limit_bytes: number;
  database_location: string | null;
};
type RehearsalReceipt = {
  status: "verified";
  schema_version: number;
  portfolio_id: string;
  revision: number;
  copy_size_bytes: number;
  rehearsal_size_bytes: number;
  event_count: number;
  event_limit: number;
  evidence_bytes: number;
  evidence_limit_bytes: number;
  database_limit_bytes: number;
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSession(value: unknown): value is RecoverySession {
  return isObject(value) && typeof value.enabled === "boolean" &&
    (value.csrf === null || typeof value.csrf === "string") &&
    typeof value.archive_limit_bytes === "number" && Number.isInteger(value.archive_limit_bytes) &&
    (value.database_location === null || typeof value.database_location === "string");
}

function isReceipt(value: unknown): value is RehearsalReceipt {
  return isObject(value) && value.status === "verified" &&
    ["schema_version", "revision", "copy_size_bytes", "rehearsal_size_bytes",
      "event_count", "event_limit", "evidence_bytes", "evidence_limit_bytes",
      "database_limit_bytes"].every(key => typeof value[key] === "number" &&
      Number.isInteger(value[key]) && (value[key] as number) >= 0) &&
    typeof value.portfolio_id === "string";
}

async function responseError(response: Response, prefix: string) {
  let detail = "request rejected";
  try {
    const body: unknown = await response.json();
    if (isObject(body) && typeof body.error === "string")
      detail = body.error.replaceAll("_", " ");
  } catch { /* The server may not return a JSON error body. */ }
  return new Error(`${prefix}: ${detail}.`);
}

function RecoveryPanel() {
  const [session, setSession] = useState<RecoverySession | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<RehearsalReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement>(null);

  async function loadSession() {
    const response = await fetch("/api/investment/v1/backup/session", {
      credentials: "same-origin", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(10000),
    });
    const body: unknown = await response.json();
    if (!response.ok) throw await responseError(response, "Backup tools are unavailable");
    if (!isSession(body)) throw new Error("The backup session response failed validation.");
    setSession(body);
  }

  useEffect(() => {
    let active = true;
    loadSession().catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : "Backup tools are unavailable.");
    });
    return () => { active = false; };
  }, []);

  function authHeaders(contentType: string) {
    if (!session?.csrf) throw new Error("The local backup session expired. Reload and retry.");
    return { "Content-Type": contentType, "X-CSRF-Token": session.csrf };
  }

  async function createBackup() {
    if (busy) return;
    setBusy(true); setError(""); setMessage(""); setReceipt(null);
    try {
      const response = await fetch("/api/investment/v1/backup/download", {
        method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
        signal: AbortSignal.timeout(60000),
        headers: authHeaders("application/json"), body: "{}",
      });
      if (!response.ok) throw await responseError(response, "Verified backup could not be created");
      if (response.headers.get("content-type") !== "application/zip")
        throw new Error("The backup download response was not a ZIP archive.");
      const archive = await response.blob();
      if (!session || archive.size > session.archive_limit_bytes)
        throw new Error("The backup archive exceeded its supported size.");
      const name = response.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1]
        ?? "goldscout-verified-backup.zip";
      const url = URL.createObjectURL(archive);
      const link = document.createElement("a");
      link.href = url; link.download = name;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage(`Verified backup prepared; a download was requested as ${name}. Confirm it appears in your browser's download location, then keep it somewhere protected away from this device.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Verified backup could not be created.");
    } finally { setBusy(false); }
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setReceipt(null); setMessage(""); setError("");
  }

  async function rehearse() {
    if (!file || busy) return;
    setBusy(true); setError(""); setMessage(""); setReceipt(null);
    try {
      if (file.size > (session?.archive_limit_bytes ?? 0))
        throw new Error("The selected archive exceeds the supported size.");
      const response = await fetch("/api/investment/v1/backup/rehearsal", {
        method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
        signal: AbortSignal.timeout(60000),
        headers: authHeaders("application/zip"), body: file,
      });
      const body: unknown = await response.json();
      if (!response.ok) throw await responseError(response, "Restore rehearsal failed");
      if (!isReceipt(body)) throw new Error("The restore rehearsal response failed validation.");
      setReceipt(body);
      setMessage("Restore rehearsal passed. A temporary copy was verified and removed; the active journal was not changed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Restore rehearsal failed.");
    } finally { setBusy(false); }
  }

  return <section className="history-group recovery-panel" aria-label="Verified backup and recovery">
    <h2>Back up and practise recovery</h2>
    <p>A backup is created only when you request it. GoldScout makes a verified copy of the selected journal
      and downloads it with its receipt; it does not overwrite your journal. The ZIP is not encrypted.</p>
    {error && <p role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    {session?.enabled && <>
      <p className="position-note">Journal location selected for this dashboard: <code>{session.database_location ?? "Unavailable"}</code>.
        The backup is downloaded using your browser's download settings; GoldScout does not choose an external destination.</p>
      <button type="button" disabled={busy || !session.csrf} onClick={() => void createBackup()}>
        {busy ? "Working…" : "Create and download verified backup"}
      </button>
      <div className="recovery-rehearsal">
        <h3>Rehearse a backup without restoring over your records</h3>
        <p>Select a GoldScout verified backup ZIP. It is checked and copied to a new temporary location,
          then removed. This does not install or activate the backup.</p>
        <label>Verified backup ZIP
          <input ref={input} type="file" accept=".zip,application/zip"
            onChange={chooseFile} disabled={busy} />
        </label>
        <button type="button" disabled={busy || !file || !session.csrf} onClick={() => void rehearse()}>
          {busy ? "Checking backup…" : "Run restore rehearsal"}
        </button>
        {receipt && <dl className="health-facts">
          <div><dt>Verified journal revision</dt><dd>{receipt.revision}</dd></div>
          <div><dt>Event records</dt><dd>{receipt.event_count} of {receipt.event_limit}</dd></div>
          <div><dt>Evidence size</dt><dd>{receipt.evidence_bytes} of {receipt.evidence_limit_bytes} bytes</dd></div>
          <div><dt>Database copy size</dt><dd>{receipt.copy_size_bytes} of {receipt.database_limit_bytes} bytes</dd></div>
        </dl>}
      </div>
    </>}
    {session && !session.enabled && <p role="status">A recorded investment journal is not selected for this dashboard.</p>}
    {!session && !error && <p role="status">Loading local recovery tools…</p>}
    <p className="position-note">Store backups somewhere protected and separate from this computer. Keep setup receipts and original
      source documents separately. A rehearsal verifies a copy; it does not prove that the original source or account is correct.</p>
  </section>;
}

export default RecoveryPanel;
