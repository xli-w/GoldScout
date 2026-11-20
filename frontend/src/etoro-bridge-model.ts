import type { EToroSnapshot } from "./EToroPortfolio";
import type { InvestmentSnapshot } from "./InvestmentView";

export type SourceRow = {
  key: string; instrumentId: number; group: string; units: string; currency: string;
};
export type BridgePair = { sourceKey: string; productId: string };

export function bridgeRows(snapshot: EToroSnapshot): SourceRow[] {
  return [
    ...snapshot.direct_positions.map(item => ({
      key: `direct:${item.instrument_id}`, instrumentId: item.instrument_id,
      group: "Direct", units: item.netUnits, currency: item.asset_currency,
    })),
    ...snapshot.mirrors.flatMap(mirror => mirror.positions.map(item => ({
      key: `copy:${mirror.mirror_id}:${item.instrument_id}`, instrumentId: item.instrument_id,
      group: `Copy ${mirror.mirror_id}`, units: item.netUnits, currency: item.asset_currency,
    }))),
  ];
}

export function unambiguousBridgeRows(rows: SourceRow[]): SourceRow[] {
  const occurrences = new Map<number, number>();
  for (const row of rows) occurrences.set(row.instrumentId, (occurrences.get(row.instrumentId) ?? 0) + 1);
  return rows.filter(row => occurrences.get(row.instrumentId) === 1);
}

export function addBridgePair(rows: SourceRow[], holdings: InvestmentSnapshot["holdings"],
  pairs: BridgePair[], sourceKey: string, productId: string): BridgePair[] | null {
  if (!unambiguousBridgeRows(rows).some(row => row.key === sourceKey) ||
      !holdings.some(holding => holding.product_id === productId) ||
      pairs.some(pair => pair.sourceKey === sourceKey || pair.productId === productId)) return null;
  return [...pairs, { sourceKey, productId }];
}

type BridgeReadiness = {
  revokeOnly: boolean; preview: boolean; busy: boolean; revoking: boolean;
  statusError: string; reconciliationRequired: boolean;
  session: { enabled: boolean; state: string; last_status: string; snapshot_available: boolean;
    connection?: { environment: "real" | "demo"; account_id: number } | null } | null;
  portfolioStatus?: string; snapshot?: EToroSnapshot | null;
  journal?: InvestmentSnapshot; now?: number;
};

export function canReviewEToro({ revokeOnly, preview, busy, revoking, statusError,
  reconciliationRequired, session, portfolioStatus, snapshot, journal, now }: BridgeReadiness): boolean {
  const sourceClock = snapshot?.source_as_of.match(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,6}))?Z$/);
  if (revokeOnly || preview || busy || revoking || statusError || reconciliationRequired ||
      !session?.enabled || !["connected", "startup_refresh_enabled"].includes(session.state) ||
      session.last_status !== "success" || !session.snapshot_available ||
      portfolioStatus !== "success" || !snapshot || !session.connection ||
      snapshot.environment !== "real" || snapshot.environment !== session.connection.environment ||
      snapshot.account_id !== session.connection.account_id || snapshot.account_id <= 0 ||
      !sourceClock || !Number.isFinite(Date.parse(snapshot.source_as_of)) ||
      typeof now !== "number" || !Number.isSafeInteger(now) ||
      journal?.source_mode !== "recorded" ||
      !/^[A-Z]{3}$/.test(snapshot.account_currency)) return false;
  const fraction = sourceClock[1] ?? "";
  const sourceMicros = BigInt(Date.parse(snapshot.source_as_of)) * 1000n +
    BigInt(fraction.padEnd(6, "0").slice(3));
  if (sourceMicros > BigInt(now) * 1000n) return false;
  const cash = snapshot.account_totals.accountAvailableCash;
  if (typeof cash !== "string" || cash.length > 60 || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(cash))
    return false;
  const rows = bridgeRows(snapshot);
  return rows.every(row => Number.isSafeInteger(row.instrumentId) && row.instrumentId > 0 &&
      typeof row.units === "string" && row.units.length <= 60 &&
      /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/.test(row.units) &&
      typeof row.currency === "string" && /^[A-Z]{3}$/.test(row.currency)) &&
    new Set(rows.map(row => row.key)).size === rows.length &&
    snapshot.mirrors.every(mirror => Number.isSafeInteger(mirror.mirror_id) && mirror.mirror_id > 0) &&
    new Set(snapshot.mirrors.map(mirror => mirror.mirror_id)).size === snapshot.mirrors.length;
}
