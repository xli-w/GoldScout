import validateOpeningSnapshot from "./investment-validator-v2.js";
import validateSetup from "./setup-validator.js";

export type SetupSession = {
  enabled: boolean; csrf?: string | null;
  state: "empty" | "ready" | "manual_unopened" | "blocked" | "recoverable";
  message?: string; workspace: string; recovery?: SetupRecovery;
};
export type SetupRecovery = {
  can_preview: boolean; requires_opening: boolean; profile: SetupProfile; opening_id: string;
};
export type SetupProfile = {
  schema_version: 2; portfolio_id: string; currency: "GBP"; created_at: string;
  accounts: { id: string; name: string; kind: "broker" | "deposit"; binding: string; currency: "GBP" }[];
  products: { id: string; name: string; sleeve: "equity" | "bond"; currency: "GBP";
    quantity_increment: string; minimum_notional_gbp: string }[];
  policy: null;
};
export type SetupOpening = {
  id: string; type: "opening"; timestamp: string; available_at: string; evidence: string;
  balances_gbp: Record<string, string>;
  holdings: { account_id: string; product_id: string; quantity: string; price_gbp: string | null }[];
};
export type SetupPreview = {
  preview_id: string; expires_at: string; profile: SetupProfile; opening: SetupOpening;
  report: { complete: boolean; equity_gbp: string | null; baseline_as_of: string | null;
    warnings: string[]; limitations: string[] };
};
export type SetupConfirmation = (
  { status: "completed"; committed: true; revision: 1 } |
  { status: "partial"; committed: true; revision: 1 } |
  { status: "partial"; committed: false; revision: null }
) & { message?: string };

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
const text = (value: unknown): value is string =>
  typeof value === "string" && value.length > 0 && value.length <= 2000;
const decimal = (value: unknown): value is string =>
  typeof value === "string" && value.length <= 100 && /^\d+(?:\.\d+)?$/.test(value);
const clock = (value: unknown): value is string =>
  text(value) && /Z$/.test(value) && Number.isFinite(Date.parse(value));
const optionalText = (value: unknown) => value === undefined || text(value);

function session(value: unknown): value is SetupSession {
  return object(value) && typeof value.enabled === "boolean" &&
    (value.csrf === null || optionalText(value.csrf)) &&
    optionalText(value.message) && text(value.workspace) &&
    (value.recovery === undefined || (value.state === "recoverable" &&
      object(value.recovery) && typeof value.recovery.can_preview === "boolean" &&
      typeof value.recovery.requires_opening === "boolean" && profile(value.recovery.profile) &&
      text(value.recovery.opening_id))) &&
    ["empty", "ready", "manual_unopened", "blocked", "recoverable"].includes(String(value.state));
}
function profile(value: unknown): value is SetupProfile {
  return object(value) && value.schema_version === 2 && value.currency === "GBP" &&
    value.policy === null && text(value.portfolio_id) && clock(value.created_at) &&
    Array.isArray(value.accounts) && value.accounts.length > 0 && value.accounts.length <= 50 &&
    value.accounts.every(a => object(a) && text(a.id) && text(a.name) && text(a.binding) &&
      a.currency === "GBP" && (a.kind === "broker" || a.kind === "deposit")) &&
    Array.isArray(value.products) && value.products.length <= 100 &&
    value.products.every(p => object(p) && text(p.id) && text(p.name) &&
      p.currency === "GBP" && (p.sleeve === "equity" || p.sleeve === "bond") &&
      decimal(p.quantity_increment) && decimal(p.minimum_notional_gbp));
}
function opening(value: unknown): value is SetupOpening {
  return object(value) && value.type === "opening" && text(value.id) &&
    clock(value.timestamp) && clock(value.available_at) && text(value.evidence) &&
    object(value.balances_gbp) && Object.values(value.balances_gbp).every(decimal) &&
    Array.isArray(value.holdings) && value.holdings.length <= 5000 &&
    value.holdings.every(h => object(h) && text(h.account_id) && text(h.product_id) &&
      decimal(h.quantity) && (h.price_gbp === null || decimal(h.price_gbp)));
}
function preview(value: unknown): value is SetupPreview {
  if (!object(value) || !text(value.preview_id) || !clock(value.expires_at) ||
      !profile(value.profile) || !opening(value.opening) ||
      !validateOpeningSnapshot(value.report) || !object(value.report)) return false;
  const report = value.report;
  return typeof report.complete === "boolean" &&
    (report.equity_gbp === null || decimal(report.equity_gbp)) &&
    (report.baseline_as_of === null || clock(report.baseline_as_of)) &&
    Array.isArray(report.warnings) && report.warnings.every(text) &&
    Array.isArray(report.limitations) && report.limitations.every(text);
}
function confirmation(value: unknown): value is SetupConfirmation {
  return object(value) && (value.status === "completed" || value.status === "partial") &&
    ((value.committed === true && value.revision === 1) ||
      (value.status === "partial" && value.committed === false && value.revision === null)) &&
    optionalText(value.message);
}

/** Response boundary, also available to the generated-schema integration. */
export function validateSetupResponse(value: unknown): boolean {
  return session(value) || preview(value) || confirmation(value);
}

async function request<T>(path: string, guard: (value: unknown) => value is T,
  csrf?: string, payload?: unknown): Promise<T> {
  if (payload !== undefined && !csrf) throw new Error("Setup request is unavailable. Reload the local setup page and retry.");
  const response = await fetch(`/api/investment/v1/setup/${path}`, {
    method: payload === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store",
    redirect: "error", signal: AbortSignal.timeout(15000),
    headers: payload === undefined ? {} : { "Content-Type": "application/json", "X-CSRF-Token": csrf! },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("Setup request was refused or is unavailable. Check the terminal and reload if the local session expired.");
  const raw = await response.text();
  if (raw.length > 2_000_000) throw new Error("Setup response exceeded the supported bound.");
  const value: unknown = JSON.parse(raw);
  if (!validateSetup(value) || !guard(value)) throw new Error("Setup response failed validation. Nothing further will be submitted.");
  return value;
}

export const setupSession = () => request("session", session);
export const setupPreview = (csrf: string, payload: {
  request_id: string; profile: SetupProfile; opening: SetupOpening;
}) => request("preview", preview, csrf, payload);
export const setupConfirm = (csrf: string, preview_id: string) =>
  request("confirm", confirmation, csrf, { preview_id });
export const setupRecoveryPreview = (csrf: string) =>
  request("recovery-preview", preview, csrf, {});
