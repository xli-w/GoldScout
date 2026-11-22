type ConnectionStatus = {
  state: string;
  connection?: { revoked_at: string | null } | null;
};

export function hasUnrevokedEToroConnection(status: ConnectionStatus | null | undefined): boolean {
  return Boolean(status?.connection && status.connection.revoked_at === null);
}

export function hasUncertainEToroRead(...statuses: (string | null | undefined)[]): boolean {
  return statuses.some(status => status === "connection_read_outcome_uncertain" ||
    status === "cancellation_unconfirmed");
}

export type UncertainOutcome = "refresh" | "connection" | "revocation" | "provider_read" | null;
export function recordEToroUncertainty(current: UncertainOutcome, next: Exclude<UncertainOutcome, null>): UncertainOutcome {
  return current === "revocation" ? current : next;
}

export function retainActivationReceipt(mounted: boolean, originalGeneration: number, currentGeneration: number): boolean {
  return !mounted || originalGeneration !== currentGeneration;
}

type StartupStatus = { state: string; last_status: string; startup_pending?: boolean | null };
export function waitingForEToroStartup(
  session: (StartupStatus & { enabled: boolean }) | null,
  portfolio: (StartupStatus & { snapshot: unknown }) | null,
): boolean {
  if (!session?.enabled || session.state !== "startup_refresh_enabled" ||
      !portfolio || portfolio.snapshot) return false;
  if (session.startup_pending === true || portfolio.startup_pending === true) return true;
  return (session.startup_pending == null || portfolio.startup_pending == null) &&
    (session.last_status === "not_collected" || portfolio.last_status === "not_collected");
}

export type ActivationReceipt = {
  committed: true; revision: number; environment: "real" | "demo"; account_id: number;
};

export function activationReceiptMessage(
  receipt: ActivationReceipt, session: { state: string } | null,
  statusUnavailable: boolean,
): string {
  const state = !statusUnavailable && session ? session.state : "unavailable";
  return `An earlier activation committed for ${receipt.environment} account ${receipt.account_id}, revision ${receipt.revision}. Current connection state: ${state}.`;
}

export function isActivationReceipt(value: unknown, environment: string): value is ActivationReceipt {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const receipt = value as Record<string, unknown>;
  return receipt.committed === true && receipt.environment === environment &&
    typeof receipt.account_id === "number" && Number.isSafeInteger(receipt.account_id) &&
    receipt.account_id > 0 && typeof receipt.revision === "number" &&
    Number.isSafeInteger(receipt.revision) && receipt.revision > 0;
}
