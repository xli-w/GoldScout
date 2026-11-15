import Decimal from "decimal.js";
import validResponse from "./response-validator.js";

export type Source = { observed_at: string | null; freshness: "fresh" | "stale" | "missing" | "invalid"; max_age_seconds: number };
export type Meta = {
  generated_at: string; environment: "paper" | "live" | "unknown";
  source_mode: "demo" | "recorded"; database_id: string | null;
  ledger_revision: number | null; telemetry_run_id: string | null;
  journal_head?: number;
  ledger: Source; telemetry: Source; broker: Source;
};
export type Overview = {
  currency: "USD"; accounting_day: string | null; recorded_equity: string | null;
  realized_pnl: string | null; unrealized_pnl: string | null;
  entries: "blocked" | "unknown" | "last_observed_allowed"; block_reasons: string[];
};
export type Position = {
  position_id: string; symbol: string; strategy: string; side: "long" | "short";
  quantity: string; entry_price: string; hard_stop: string; trailing_stop: string | null;
  broker_stop: string | null; protection: "last_verified" | "unverified" | "unknown";
  protection_observed_at: string | null; pending_exit_order_id: string | null; exit_pending: boolean;
};
export type Envelope<Value> = { api_version: "1"; resource: string; meta: Meta; data: Value };
export type Health = {
  runtime: "running" | "stopped" | "faulted" | "unknown";
  last_monitor_completed_at: string | null; persistence_fault: boolean | null;
  accounting_unresolved: boolean | null; daily_halted: boolean | null;
  unmanaged_symbols: string[]; task_failures?: Record<string, number>; warnings: string[];
  activity_sync: { checkpoint_at: string | null; status: string; coverage: string };
};

export class DashboardError extends Error {
  constructor(public code: string, message: string, public meta?: Meta) { super(message); }
}

export async function get<Value>(path: string, resource: string, signal: AbortSignal): Promise<Envelope<Value>> {
  let response: Response;
  try {
    response = await fetch(path, { signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), cache: "no-store" });
  } catch (error) {
    if (signal.aborted) throw error;
    throw new DashboardError("source_unavailable", error instanceof Error && error.name === "TimeoutError"
      ? "The data request timed out. Retry the snapshot." : "The data source could not be reached.");
  }
  let body: unknown;
  try { body = await response.json(); }
  catch (error) {
    if (signal.aborted) throw error;
    throw new DashboardError("source_unavailable", "The data response could not be read.");
  }
  if (!validResponse(body)) throw new DashboardError("ledger_invalid", "Account data failed validation.");
  const envelope = body as Envelope<Value>;
  if (envelope.resource === "error") {
    const detail = envelope.data as { code: string; message: string };
    throw new DashboardError(detail.code, detail.message, envelope.meta);
  }
  if (!response.ok || envelope.resource !== resource) {
    throw new DashboardError("source_unavailable", "Account data is unavailable.");
  }
  return envelope;
}

function sameSnapshot(left: Meta, right: Meta) {
  return left.database_id === right.database_id && left.ledger_revision === right.ledger_revision
    && left.environment === right.environment && left.source_mode === right.source_mode
    && left.telemetry_run_id === right.telemetry_run_id;
}

export async function loadDashboard(signal: AbortSignal) {
  const overview = await get<Overview>("/api/v1/overview", "overview", signal);
  const revision = overview.meta.ledger_revision;
  const parameters = new URLSearchParams();
  if (revision !== null) parameters.set("revision", String(revision));
  if (overview.meta.database_id !== null) parameters.set("database_id", overview.meta.database_id);
  if (overview.meta.telemetry_run_id !== null) parameters.set("run_id", overview.meta.telemetry_run_id);
  const positions = await get<Position[]>(`/api/v1/positions?${parameters}`, "positions", signal);
  const health = await get<Health>(`/api/v1/health?${parameters}`, "health", signal);
  if (!sameSnapshot(overview.meta, positions.meta) || !sameSnapshot(overview.meta, health.meta)) {
    throw new DashboardError("source_unavailable", "Account data changed during refresh.", overview.meta);
  }
  return { overview, positions, health };
}

export function money(value: string | null, exact = false) {
  if (value === null) return "Unavailable";
  const amount = new Decimal(value);
  const [whole, fraction] = amount.abs().toFixed(exact ? Math.max(2, amount.decimalPlaces()) : 2).split(".");
  return `${amount.isNegative() && !amount.isZero() ? "-" : ""}$${new Intl.NumberFormat("en-US").format(BigInt(whole))}.${fraction}`;
}

export function price(value: string | null) { return money(value, true); }

export function quantity(value: string | null) { return value === null ? "Unknown" : new Decimal(value).toFixed(); }
export function label(value: string) { return value.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" "); }
export function timestamp(value: string | null, missing = "Not observed") {
  return value === null ? missing : new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC", timeZoneName: "short",
  }).format(new Date(value));
}

export function sourceFreshness(source: Source, now: number) {
  if (source.freshness === "invalid") return "invalid";
  if (!source.observed_at) return "missing";
  const age = (now - Date.parse(source.observed_at)) / 1000;
  if (!Number.isFinite(age) || age < 0) return "invalid";
  return age > source.max_age_seconds ? "stale" : "fresh";
}

export function runtimeStatus(health: Health, meta: Meta, now: number) {
  if (sourceFreshness(meta.telemetry, now) !== "fresh") return "unknown";
  if (health.runtime === "running" && (!health.last_monitor_completed_at
      || now - Date.parse(health.last_monitor_completed_at) > 90000
      || now < Date.parse(health.last_monitor_completed_at))) return "unknown";
  return health.runtime;
}