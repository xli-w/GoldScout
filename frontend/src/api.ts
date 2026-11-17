export function label(value: string) {
  return value.split("_").map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

export function timestamp(value: string | null, missing = "Not observed") {
  return value === null ? missing : new Intl.DateTimeFormat("en-US", {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    second: "2-digit", timeZone: "UTC", timeZoneName: "short",
  }).format(new Date(value));
}
