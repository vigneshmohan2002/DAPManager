import {
  apiFetch,
  arrayField,
  backendUrl,
  isJsonRecord,
  readJsonRecord,
} from "./client";
import { postAction } from "./sync";
import type {
  Contribution,
  FleetDevice,
  FleetSearchResult,
  ActionResult,
  ContributeTrackResult,
} from "./types";

export async function fetchContributions(limit = 200): Promise<Contribution[]> {
  const url = await backendUrl();
  const safeLimit = Math.max(1, Math.min(500, Math.trunc(limit)));
  const r = await apiFetch(`${url}/api/contributions?limit=${safeLimit}`);
  if (!r.ok) throw new Error(`contributions: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "contributions failed"));
  return arrayField<Contribution>(data, "contributions");
}

export async function fetchOutgoingContributions(
  limit = 200,
): Promise<Contribution[]> {
  const url = await backendUrl();
  const safeLimit = Math.max(1, Math.min(500, Math.trunc(limit)));
  const r = await apiFetch(`${url}/api/contributed?limit=${safeLimit}`);
  if (!r.ok) throw new Error(`contributed: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "contributed failed"));
  return arrayField<unknown>(data, "contributions").map(
    (value): Contribution => {
      const row = isJsonRecord(value) ? value : {};
      return {
      id: Number(row.local_id ?? 0),
      contribution_id:
        row.contribution_id == null ? null : Number(row.contribution_id),
      device_id: null,
      mbid: row.mbid == null ? null : String(row.mbid),
      artist: row.artist == null ? null : String(row.artist),
      title: row.title == null ? null : String(row.title),
      album: row.album == null ? null : String(row.album),
      target_quality: null,
      acquired_quality: null,
      status: String(row.status ?? "unknown"),
      updated_at: row.updated_at == null ? null : String(row.updated_at),
      };
    },
  );
}

export async function fetchFleetSummary(): Promise<FleetDevice[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/fleet/summary`);
  if (!r.ok) throw new Error(`fleet/summary: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "fleet/summary failed"));
  return arrayField<FleetDevice>(data, "devices");
}

export async function searchFleet(q: string): Promise<FleetSearchResult[]> {
  const query = q.trim();
  if (!query) return [];
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/fleet/track?q=${encodeURIComponent(query)}`,
  );
  if (!r.ok) throw new Error(`fleet/track: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "fleet/track failed"));
  return arrayField<FleetSearchResult>(data, "results");
}

export async function contributeAllLocalTracks(): Promise<ActionResult> {
  return postAction("/api/contribute");
}

export async function contributeTrack(
  mbid: string,
): Promise<ContributeTrackResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/contribute/track`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mbid }),
  });
  let data: Record<string, unknown> = {};
  try {
    data = await readJsonRecord(r);
  } catch {
    // Keep the transport status useful even if an upstream proxy returns
    // HTML instead of the endpoint's normal JSON error object.
  }
  return {
    success: r.ok && Boolean(data.success),
    message:
      typeof data.message === "string"
        ? data.message
        : r.ok
          ? ""
          : `contribute/track: ${r.status}`,
    mbid: typeof data.mbid === "string" ? data.mbid : undefined,
    status: typeof data.status === "string" ? data.status : undefined,
  };
}
