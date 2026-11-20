type RecordFields = {
  accountId: string;
  destinationAccountId: string;
  productId: string;
  role: "core" | "dynamic" | "unassigned";
  roleEvidence: string;
  amount: string;
  quantity: string;
  price: string;
  fee: string;
  ratio: string;
};

export function localClock(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  const date = new Date(value);
  if (!match || !Number.isFinite(date.getTime()) || date.getFullYear() !== Number(match[1]) ||
      date.getMonth() + 1 !== Number(match[2]) || date.getDate() !== Number(match[3]) ||
      date.getHours() !== Number(match[4]) || date.getMinutes() !== Number(match[5]))
    throw new Error("Enter a valid local date and time for each required date.");
  for (let hour = -36; hour <= 36; hour++) {
    const offset = new Date(date.getTime() + hour * 3600000).getTimezoneOffset();
    const alternative = new Date(date.getTime() + (offset - date.getTimezoneOffset()) * 60000);
    if (alternative.getTime() !== date.getTime() && alternative.getFullYear() === date.getFullYear() &&
        alternative.getMonth() === date.getMonth() && alternative.getDate() === date.getDate() &&
        alternative.getHours() === date.getHours() && alternative.getMinutes() === date.getMinutes())
      throw new Error("This local time occurs twice when the clocks change. Pause and use an unambiguous time supported by your source; do not guess.");
  }
  return date.toISOString();
}

export const PENDING_RECORDING_KEY = "goldscout-pending-recording";
const recordingVersionKey = "goldscout-recording-version";
export type PendingRecording = { previewId: string; eventId: string; expectedRevision: number | null };
export class RecordingRequestError extends Error {
  constructor(public readonly code: string) { super(code); }
}
export class RecordingPreviewExpiredError extends Error {
  constructor() { super("The preview expired before this confirmation was saved. Request a new preview."); }
}
export class RecordingPreviewChangedError extends Error {
  constructor() { super("Another save started while this preview was open. Check your records and request a new preview."); }
}
export class RecordingSavedStorageError extends Error {
  constructor() {
    super("The record was saved, but the browser could not clear its pending-save reference.");
  }
}

export function pendingRecording(storage: Storage): PendingRecording | null {
  const raw = storage.getItem(PENDING_RECORDING_KEY);
  if (raw === null) return null;
  const value: unknown = JSON.parse(raw);
  if (typeof value !== "object" || value === null ||
      !("previewId" in value) || typeof value.previewId !== "string" || !value.previewId ||
      !("eventId" in value) || typeof value.eventId !== "string" || !value.eventId)
    throw new Error("The pending recording reference is invalid.");
  if (!("expectedRevision" in value))
    return { previewId: value.previewId, eventId: value.eventId, expectedRevision: null };
  if (typeof value.expectedRevision !== "number" ||
      !Number.isSafeInteger(value.expectedRevision) || value.expectedRevision < 1)
    throw new Error("The pending recording revision is invalid.");
  return { previewId: value.previewId, eventId: value.eventId, expectedRevision: value.expectedRevision };
}

export function recordingVersion(storage: Storage) { return storage.getItem(recordingVersionKey); }

export async function confirmRecording(storage: Storage, pending: PendingRecording,
  send: (previewId: string) => Promise<unknown>) {
  if (pending.expectedRevision === null)
    throw new Error("The pending save has no expected revision. Check the journal before taking further action.");
  const existing = pendingRecording(storage);
  if (existing && (existing.previewId !== pending.previewId || existing.eventId !== pending.eventId ||
      existing.expectedRevision !== pending.expectedRevision))
    throw new Error("Another recording has an unfinished save. Resolve it before recording anything else.");
  if (!existing) {
    storage.setItem(PENDING_RECORDING_KEY, JSON.stringify(pending));
  }
  if (recordingVersion(storage) !== pending.previewId)
    storage.setItem(recordingVersionKey, pending.previewId);
  let receipt: unknown;
  try { receipt = await send(pending.previewId); }
  catch (error) {
    if (!existing && error instanceof RecordingRequestError && error.code === "preview_expired") {
      storage.removeItem(PENDING_RECORDING_KEY);
      throw new RecordingPreviewExpiredError();
    }
    throw error;
  }
  if (typeof receipt !== "object" || receipt === null || !("id" in receipt) ||
      receipt.id !== pending.eventId || !("revision" in receipt) ||
      typeof receipt.revision !== "number" || !Number.isSafeInteger(receipt.revision) ||
      receipt.revision !== pending.expectedRevision)
    throw new Error("The save receipt could not be verified against this record.");
  try { storage.removeItem(PENDING_RECORDING_KEY); }
  catch { throw new RecordingSavedStorageError(); }
}

export function recordingClocks(effective: string, available: string, sameClock: boolean) {
  const timestamp = localClock(effective);
  const availableAt = sameClock ? timestamp : localClock(available);
  if (availableAt < timestamp)
    throw new Error("Source available from cannot be earlier than the date this record applies. For a correction, use when the correction evidence became available; describe an earlier source date in the source description.");
  return { timestamp, available_at: availableAt };
}

export function standardRecord(kind: string, common: {
  id: string; timestamp: string; available_at: string; evidence: string;
}, input: RecordFields): Record<string, unknown> {
  const value: Record<string, unknown> = {
    ...common, type: kind, account_id: input.accountId, currency: "GBP",
  };
  if (input.role !== "unassigned" &&
      ["contribution", "withdrawal", "transfer", "interest", "dividend", "buy", "sell", "account_fee"].includes(kind))
    value.role = input.role;
  if (kind === "interest" || kind === "dividend") value.role_evidence = input.roleEvidence;
  if (["contribution", "withdrawal", "interest", "dividend", "transfer", "account_fee"].includes(kind))
    value.amount_gbp = input.amount;
  if (kind === "transfer") value.destination_account_id = input.destinationAccountId;
  if (["dividend", "buy", "sell", "mark", "split"].includes(kind)) {
    if (!input.productId) throw new Error("Choose the investment for this record.");
    value.product_id = input.productId;
  }
  if (kind === "buy" || kind === "sell") {
    if (!input.fee.trim()) throw new Error("Enter the recorded fee. Enter 0 only if the source confirms no fee.");
    value.quantity = input.quantity;
    value.price_gbp = input.price;
    value.fee_gbp = input.fee;
  }
  if (kind === "mark") value.price_gbp = input.price;
  if (kind === "split") value.ratio = input.ratio;
  return value;
}
