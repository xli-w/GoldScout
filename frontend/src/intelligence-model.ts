/** Pure display checks. No fetching, credentials, or portfolio inputs. */
export function publicSourceURL(value: string | undefined) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? value : undefined;
  } catch {
    return undefined;
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string");
}
function dated(value: unknown) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}
function cited(value: unknown): boolean {
  return object(value) && typeof value.text === "string" && strings(value.evidence_ids);
}

export function validPublicFoundation(value: unknown): boolean {
  if (!object(value) || typeof value.schema_capable !== "boolean"
    || !Array.isArray(value.source_health) || !("briefing" in value) || !("operation" in value)) return false;
  if (value.source_health.some(item => !object(item) || typeof item.source !== "string"
    || ![item.attempted_at, item.succeeded_at].every(stamp => stamp === null || dated(stamp))
    || !(item.error_code === null || typeof item.error_code === "string"))) return false;
  if (value.operation !== null && (!object(value.operation) || typeof value.operation.operation_id !== "string"
    || typeof value.operation.state !== "string" || !dated(value.operation.observed_at)
    || !(value.operation.error_code === null || typeof value.operation.error_code === "string"))) return false;
  const briefing = value.briefing;
  if (briefing === null) return true;
  if (!object(briefing) || typeof briefing.briefing_id !== "string" || !dated(briefing.generated_at)
    || typeof briefing.provider !== "string" || typeof briefing.model !== "string"
    || !object(briefing.manifest) || !object(briefing.output)) return false;
  const manifest = briefing.manifest, output = briefing.output;
  if (!Array.isArray(manifest.evidence) || !Array.isArray(manifest.gaps) || !Number.isInteger(manifest.omitted)
    || typeof manifest.input_hash !== "string" || !dated(manifest.cutoff)
    || typeof output.headline !== "string" || !Array.isArray(output.developments)) return false;
  if (manifest.evidence.some(item => !object(item) || typeof item.evidence_id !== "string"
    || typeof item.title !== "string" || typeof item.text !== "string"
    || !publicSourceURL(typeof item.source_url === "string" ? item.source_url : undefined)
    || !dated(item.received_at) || !(item.published_at === null || dated(item.published_at)))) return false;
  return output.developments.every(item => object(item) && typeof item.title === "string"
    && typeof item.topic === "string" && ["UK", "US"].includes(String(item.geography))
    && Array.isArray(item.claims) && item.claims.every(cited)
    && cited(item.interpretation) && strings(item.uncertainties));
}

export function calendarMatches(
  event: { source: string; category: string; scheduled_at: string | null },
  filters: { geography: string; category: string; time: string; cutoff: string },
) {
  return (filters.geography === "all" || (["ons", "boe_mpc"].includes(event.source) ? "UK" : "US") === filters.geography)
    && (filters.category === "all" || event.category === filters.category)
    && (filters.time === "all" || event.scheduled_at === null
      || Date.parse(event.scheduled_at) >= Date.parse(filters.cutoff));
}
