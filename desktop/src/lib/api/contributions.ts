import {
  apiFetch,
  arrayField,
  backendUrl,
  isJsonRecord,
  isNullableString,
  isNumber,
  isString,
  readJsonRecord,
} from "./client";
import { postAction } from "./sync";
import type {
  AudioQuality,
  Contribution,
  FleetDevice,
  FleetHolder,
  FleetSearchResult,
  ActionResult,
  ContributeTrackResult,
} from "./types";

function isOptionalNumber(value: unknown): value is number | undefined {
  return value === undefined || isNumber(value);
}

function isAudioQuality(value: unknown): value is AudioQuality {
  if (!isJsonRecord(value)) return false;
  return (
    (value.ext === undefined || isString(value.ext)) &&
    (value.lossless === undefined || typeof value.lossless === "boolean") &&
    isOptionalNumber(value.bits_per_sample) &&
    isOptionalNumber(value.sample_rate) &&
    isOptionalNumber(value.bitrate) &&
    isOptionalNumber(value.channels) &&
    isOptionalNumber(value.length_ms) &&
    isOptionalNumber(value.size_bytes)
  );
}

function isOptionalNullableNumber(
  value: unknown,
): value is number | null | undefined {
  return value === undefined || value === null || isNumber(value);
}

function isOptionalNullableString(
  value: unknown,
): value is string | null | undefined {
  return value === undefined || isNullableString(value);
}

function isContribution(value: unknown): value is Contribution {
  if (!isJsonRecord(value)) return false;
  return (
    isNumber(value.id) &&
    isOptionalNullableNumber(value.contribution_id) &&
    isNullableString(value.device_id) &&
    isNullableString(value.mbid) &&
    isOptionalNullableString(value.isrc) &&
    isNullableString(value.artist) &&
    isNullableString(value.title) &&
    isNullableString(value.album) &&
    (value.target_quality === null || isAudioQuality(value.target_quality)) &&
    (value.acquired_quality === null || isAudioQuality(value.acquired_quality)) &&
    isString(value.status) &&
    isOptionalNullableNumber(value.download_id) &&
    isOptionalNullableString(value.created_at) &&
    isNullableString(value.updated_at)
  );
}

function isFleetDevice(value: unknown): value is FleetDevice {
  return (
    isJsonRecord(value) &&
    isString(value.device_id) &&
    isNumber(value.track_count) &&
    isNullableString(value.last_reported_at)
  );
}

function isFleetHolder(value: unknown): value is FleetHolder {
  return (
    isJsonRecord(value) &&
    isString(value.device_id) &&
    isNullableString(value.local_path) &&
    isString(value.reported_at)
  );
}

function isFleetSearchResult(value: unknown): value is FleetSearchResult {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.artist) &&
    isString(value.title) &&
    isNullableString(value.album) &&
    isNumber(value.device_count) &&
    Array.isArray(value.holders) &&
    value.holders.every(isFleetHolder)
  );
}

export async function fetchContributions(limit = 200): Promise<Contribution[]> {
  const url = await backendUrl();
  const safeLimit = Math.max(1, Math.min(500, Math.trunc(limit)));
  const r = await apiFetch(`${url}/api/contributions?limit=${safeLimit}`);
  if (!r.ok) throw new Error(`contributions: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "contributions failed"));
  return arrayField(data, "contributions", isContribution);
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
  return arrayField(data, "contributions", isJsonRecord).map(
    (row): Contribution => {
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
  return arrayField(data, "devices", isFleetDevice);
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
  return arrayField(data, "results", isFleetSearchResult);
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
