import {
  apiFetch,
  arrayField,
  backendUrl,
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
} from "./types";

export async function queueCatalogDownload(
  mbids: string[],
): Promise<QueueDownloadResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/catalog/queue-download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mbids }),
  });
  return (await readJsonRecord(r)) as QueueDownloadResult;
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
    items: arrayField<WantedRelease>(data, "items"),
  };
}

export async function queueWantedRelease(
  release: Pick<WantedRelease, "mbid" | "artist" | "title">,
): Promise<ActionResult> {
  const url = await backendUrl();
  const search_query = `${release.artist} - ${release.title}`.trim();
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
  return arrayField<DownloadQueueItem>(data, "items");
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
