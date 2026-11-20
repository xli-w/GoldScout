import Decimal from "decimal.js";

export function label(value: string) {
  return value.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function timestamp(value: string | null, missing = "Not observed") {
  return value === null ? missing : new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", timeZone: "UTC", timeZoneName: "short",
  }).format(new Date(value));
}

export function reviewTimestamp(value: string | null,
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone) {
  if (value === null) return "Not observed";
  const local = new Intl.DateTimeFormat("en-GB", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", hourCycle: "h23", timeZone,
  }).format(new Date(value));
  return `Local: ${local} (${timeZone}); UTC: ${value}`;
}

export function evidenceAge(value: string | null, now: number) {
  if (value === null) return "Age unavailable";
  const age = now - Date.parse(value);
  if (!Number.isFinite(age) || age < 0) return "Age unavailable (future or invalid clock)";
  const days = Math.floor(age / 86400000);
  if (days) return `${days} ${days === 1 ? "day" : "days"}`;
  const hours = Math.floor(age / 3600000);
  if (hours) return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  const minutes = Math.floor(age / 60000);
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

export function pounds(value: string | null, exact = false) {
  if (value === null) return "Unavailable";
  const amount = new Decimal(value);
  const [whole, fraction] = amount.abs().toFixed(exact ? Math.max(2, amount.decimalPlaces()) : 2).split(".");
  return `${amount.isNegative() && !amount.isZero() ? "-" : ""}\u00a3${new Intl.NumberFormat("en-GB").format(BigInt(whole))}.${fraction}`;
}
