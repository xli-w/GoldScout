export type GoalInput = {
  name: string;
  target_amount_gbp: string | null;
  target_date: string | null;
  notes: string;
  purpose: "not_set" | "first_home" | "retirement" | "education" |
    "major_purchase" | "financial_resilience" | "other";
  access_date: string | null;
  liquidity: "unknown" | "immediate" | "within_1_year" | "within_5_years" | "flexible";
  loss_tolerance: "not_set" | "avoid_loss" | "small_fluctuations" |
    "substantial_fluctuations" | "not_sure";
  current_amount_gbp: string | null;
  monthly_contribution_gbp: string | null;
};

export type PlanningGoal = GoalInput & { id: string };
export type PlanningSession = {
  enabled: boolean;
  csrf: string | null;
  revision: number;
  profile_hash: string | null;
  goals: PlanningGoal[];
};
export type PlanningPreview = {
  preview_id: string;
  revision: number;
  expires_at: string;
  goals: PlanningGoal[];
};

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function goal(value: unknown): value is PlanningGoal {
  return object(value) && typeof value.id === "string" &&
    /^[0-9a-f]{32}$/.test(value.id) &&
    typeof value.name === "string" && value.name.length > 0 &&
    (value.target_amount_gbp === null || typeof value.target_amount_gbp === "string") &&
    (value.target_date === null || typeof value.target_date === "string") &&
    typeof value.notes === "string" &&
    ["not_set", "first_home", "retirement", "education", "major_purchase",
      "financial_resilience", "other"].includes(value.purpose as string) &&
    (value.access_date === null || typeof value.access_date === "string") &&
    ["unknown", "immediate", "within_1_year", "within_5_years", "flexible"]
      .includes(value.liquidity as string) &&
    ["not_set", "avoid_loss", "small_fluctuations", "substantial_fluctuations", "not_sure"]
      .includes(value.loss_tolerance as string) &&
    (value.current_amount_gbp === null || typeof value.current_amount_gbp === "string") &&
    (value.monthly_contribution_gbp === null || typeof value.monthly_contribution_gbp === "string");
}

function session(value: unknown): value is PlanningSession {
  return object(value) && typeof value.enabled === "boolean" &&
    (value.csrf === null || typeof value.csrf === "string") &&
    Number.isInteger(value.revision) && typeof value.revision === "number" &&
    (value.profile_hash === null || typeof value.profile_hash === "string") &&
    Array.isArray(value.goals) && value.goals.every(goal);
}

function preview(value: unknown): value is PlanningPreview {
  return object(value) && typeof value.preview_id === "string" &&
    typeof value.revision === "number" && Number.isInteger(value.revision) &&
    typeof value.expires_at === "string" && Array.isArray(value.goals) &&
    value.goals.every(goal);
}

async function request<T>(path: string, guard: (value: unknown) => value is T,
  csrf?: string, payload?: unknown): Promise<T> {
  if (payload !== undefined && !csrf) throw new Error("The local planning session expired. Reload and retry.");
  const response = await fetch(`/api/investment/v1/planning/${path}`, {
    method: payload === undefined ? "GET" : "POST",
    credentials: "same-origin", cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(15000),
    headers: payload === undefined ? {} : {
      "Content-Type": "application/json", "X-CSRF-Token": csrf!,
    },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const body: unknown = await response.json();
  if (!response.ok) {
    const code = object(body) && typeof body.error === "string"
      ? body.error.replaceAll("_", " ") : "Request rejected";
    throw new Error(`Local planning is unavailable: ${code}.`);
  }
  if (!guard(body)) throw new Error("The local planning response failed validation.");
  return body;
}

export const planningSession = () => request("session", session);
export const planningPreview = (csrf: string, payload: {
  request_id: string; expected_revision: number; expected_hash: string; goals: GoalInput[];
}) => request("preview", preview, csrf, payload);
export const planningConfirm = (csrf: string, preview_id: string) =>
  request("confirm", (value): value is { status: "saved"; revision: number } =>
    object(value) && value.status === "saved" && typeof value.revision === "number",
  csrf, { preview_id });
