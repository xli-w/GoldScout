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
