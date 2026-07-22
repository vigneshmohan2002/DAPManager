import {
  apiFetch,
  arrayField,
  backendUrl,
  isJsonRecord,
  isNullableString,
  isNumber,
  isString,
  readJsonRecord,
  type JsonRecord,
} from "./client";
import type {
  ActionResult,
  QueueDownloadResult,
  WantedRelease,
  WantedReleasesResult,
  DownloadQueueItem,
  ClearCompletedResult,
  AlbumReleaseCandidate,
  AlbumDownloadRequest,
  AlbumDownloadRequestResult,
  AlbumReleaseSearchResult,
  AlbumDownloadStage,
} from "./types";

function numericValue(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

function decodeQueueDownloadResult(
  data: Record<string, unknown>,
): QueueDownloadResult {
  return {
    success: Boolean(data.success),
    message: isString(data.message) ? data.message : undefined,
    queued: numericValue(data.queued),
    skipped_linked: numericValue(data.skipped_linked),
    skipped_queued: numericValue(data.skipped_queued),
    not_found: numericValue(data.not_found),
  };
}

function isWantedRelease(value: unknown): value is WantedRelease {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.artist) &&
    isString(value.title) &&
    isNullableString(value.release_date) &&
    isString(value.cover_url) &&
    typeof value.queued === "boolean" &&
    typeof value.downloaded === "boolean"
  );
}

function isDownloadQueueItem(value: unknown): value is DownloadQueueItem {
  if (!isJsonRecord(value)) return false;
  return (
    isNumber(value.id) &&
    isString(value.query) &&
    isString(value.status) &&
    isNullableString(value.last_attempt) &&
    (value.attempt_count === undefined || isNumber(value.attempt_count)) &&
    (value.max_attempts === undefined || isNumber(value.max_attempts)) &&
    (value.next_attempt_at === undefined ||
      isNullableString(value.next_attempt_at)) &&
    (value.is_paused === undefined || typeof value.is_paused === "boolean") &&
    (value.is_quarantined === undefined ||
      typeof value.is_quarantined === "boolean") &&
    (value.last_error === undefined || isNullableString(value.last_error))
  );
}

const ALBUM_DOWNLOAD_STAGES = new Set<AlbumDownloadStage>([
  "queued",
  "downloading",
  "importing",
  "success",
  "failed",
]);
const RELEASE_MBID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function isAlbumDownloadStage(value: unknown): value is AlbumDownloadStage {
  return isString(value) && ALBUM_DOWNLOAD_STAGES.has(value as AlbumDownloadStage);
}

function stringValue(value: unknown): string {
  return isString(value) ? value : "";
}

function isAlbumReleaseCandidate(value: unknown): value is AlbumReleaseCandidate {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.release_mbid) &&
    RELEASE_MBID_PATTERN.test(value.release_mbid) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNumber(value.track_count)
  );
}

function decodeAlbumReleaseCandidate(
  value: AlbumReleaseCandidate,
): AlbumReleaseCandidate {
  return {
    release_mbid: value.release_mbid,
    title: value.title,
    artist: value.artist,
    track_count: numericValue(value.track_count),
    date: stringValue(value.date),
    country: stringValue(value.country),
    status: stringValue(value.status),
    disambiguation: stringValue(value.disambiguation),
    primary_type: stringValue(value.primary_type),
    format: stringValue(value.format),
    label: stringValue(value.label),
    catalog_number: stringValue(value.catalog_number),
    barcode: stringValue(value.barcode),
    cover_url: stringValue(value.cover_url),
    musicbrainz_url: stringValue(value.musicbrainz_url),
    ...(isNumber(value.score) ? { score: value.score } : {}),
  };
}

function isAlbumDownloadRequest(value: unknown): value is AlbumDownloadRequest {
  if (!isJsonRecord(value)) return false;
  return (
    isNumber(value.id) &&
    isString(value.release_mbid) &&
    RELEASE_MBID_PATTERN.test(value.release_mbid) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNumber(value.track_count) &&
    isAlbumDownloadStage(value.stage) &&
    isString(value.detail) &&
    isNumber(value.completed_tracks)
  );
}

function decodeAlbumDownloadRequest(
  value: AlbumDownloadRequest,
): AlbumDownloadRequest {
  return {
    id: value.id,
    release_mbid: value.release_mbid,
    title: value.title,
    artist: value.artist,
    track_count: Math.max(0, numericValue(value.track_count)),
    stage: value.stage,
    detail: value.detail,
    completed_tracks: Math.max(0, numericValue(value.completed_tracks)),
    queue_status: isNullableString(value.queue_status) ? value.queue_status : null,
    last_attempt: isNullableString(value.last_attempt) ? value.last_attempt : null,
    created_at: isNullableString(value.created_at) ? value.created_at : null,
    updated_at: isNullableString(value.updated_at) ? value.updated_at : null,
    cover_url: stringValue(value.cover_url),
  };
}

async function responseMessage(response: Response, fallback: string): Promise<string> {
  try {
    const data = await readJsonRecord(response);
    return isString(data.message) ? data.message : fallback;
  } catch {
    return fallback;
  }
}

export async function searchAlbumReleases(
  query: string,
  signal?: AbortSignal,
): Promise<AlbumReleaseSearchResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/download/albums/search?q=${encodeURIComponent(query.trim())}`,
    { signal, cache: "no-store" },
  );
  if (!r.ok) {
    throw new Error(await responseMessage(r, `album search: ${r.status}`));
  }
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(stringValue(data.message) || "Album search failed");
  return {
    query: stringValue(data.query),
    ambiguous: Boolean(data.ambiguous),
    candidates: arrayField(data, "candidates", isAlbumReleaseCandidate).map(
      decodeAlbumReleaseCandidate,
    ),
  };
}

export async function requestAlbumDownload(
  releaseMbid: string,
): Promise<AlbumDownloadRequestResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/download/albums/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ release_mbid: releaseMbid }),
    cache: "no-store",
  });
  const data = await readJsonRecord(r).catch((): JsonRecord => ({}));
  if (!r.ok || !data.success) {
    return {
      success: false,
      queued: false,
      message: stringValue(data.message) || `album request: ${r.status}`,
    };
  }
  const request = isAlbumDownloadRequest(data.request)
    ? decodeAlbumDownloadRequest(data.request)
    : undefined;
  return {
    success: true,
    queued: Boolean(data.queued),
    message: stringValue(data.message),
    ...(request ? { request } : {}),
  };
}

export async function fetchAlbumDownloadRequests(
  signal?: AbortSignal,
): Promise<AlbumDownloadRequest[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/download/albums/requests`, {
    signal,
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`album requests: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(stringValue(data.message) || "Album requests failed");
  return arrayField(data, "requests", isAlbumDownloadRequest).map(
    decodeAlbumDownloadRequest,
  );
}

export async function fetchAlbumDownloadRequest(
  requestId: number,
  signal?: AbortSignal,
): Promise<AlbumDownloadRequest | null> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/download/albums/requests/${requestId}`, {
    signal,
    cache: "no-store",
  });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`album request ${requestId}: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success || !isAlbumDownloadRequest(data.request)) {
    throw new TypeError("Invalid album request progress response");
  }
  return decodeAlbumDownloadRequest(data.request);
}

export async function queueCatalogDownload(
  mbids: string[],
): Promise<QueueDownloadResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/catalog/queue-download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mbids }),
  });
  return decodeQueueDownloadResult(await readJsonRecord(r));
}

export async function fetchWantedReleases(): Promise<WantedReleasesResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/releases/wanted`);
  if (r.status === 502) {
    const data = await readJsonRecord(r).catch((): JsonRecord => ({}));
    return { kind: "error", message: String(data.message ?? "Lidarr error") };
  }
  if (!r.ok) {
    return { kind: "error", message: `releases/wanted: ${r.status}` };
  }
  const data = await readJsonRecord(r);
  if (!data.success) {
    if (data.reason === "lidarr_disabled") return { kind: "disabled" };
    if (data.reason === "lidarr_unavailable") return { kind: "unavailable" };
    return { kind: "error", message: String(data.message ?? "unknown") };
  }
  return {
    kind: "ok",
    last_tick: typeof data.last_tick === "string" ? data.last_tick : null,
    items: arrayField(data, "items", isWantedRelease),
  };
}

export async function queueWantedRelease(
  release: Pick<WantedRelease, "mbid" | "artist" | "title">,
): Promise<ActionResult> {
  const url = await backendUrl();
  const search_query = `::ALBUM:: ${release.artist} - ${release.title}`.trim();
  const r = await apiFetch(`${url}/api/download/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ search_query, mbid_guess: release.mbid }),
  });
  if (!r.ok) {
    return { success: false, message: `download/request: ${r.status}` };
  }
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function fetchDownloads(): Promise<DownloadQueueItem[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/downloads/list`);
  if (!r.ok) throw new Error(`downloads/list: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "downloads/list failed"));
  return arrayField(data, "items", isDownloadQueueItem);
}

export async function retryDownload(id: number): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/downloads/${id}/retry`, { method: "POST" });
  // The backend emits 404 when the row isn't in 'failed' state; surface
  // that as a typed message rather than a thrown error so the screen can
  // toast it and keep the table mounted.
  if (r.status === 404) {
    return { success: false, message: "row already finished or not failed" };
  }
  if (!r.ok) return { success: false, message: `retry: ${r.status}` };
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deleteDownload(id: number): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/downloads/${id}`, { method: "DELETE" });
  if (!r.ok) return { success: false, message: `delete: ${r.status}` };
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function clearCompletedDownloads(): Promise<ClearCompletedResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/downloads/clear-completed`, {
    method: "POST",
  });
  if (!r.ok) return { success: false, message: `clear: ${r.status}`, removed: 0 };
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    removed: Number(data.removed ?? 0),
  };
}
