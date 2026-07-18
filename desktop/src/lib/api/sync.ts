import { apiFetch, backendUrl, isJsonRecord, readJsonRecord } from "./client";
import type {
  SyncState,
  BackendStatus,
  ActionResult,
} from "./types";

function nullableText(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function decodeSyncState(value: unknown): SyncState {
  const state = isJsonRecord(value) ? value : {};
  return {
    catalog_pull: nullableText(state.catalog_pull),
    playlist_pull: nullableText(state.playlist_pull),
    playlist_push: nullableText(state.playlist_push),
    inventory_report: nullableText(state.inventory_report),
  };
}

function decodeBackendStatus(data: Record<string, unknown>): BackendStatus {
  return {
    running: Boolean(data.running),
    task: nullableText(data.task),
    message: nullableText(data.message),
    detail: nullableText(data.detail),
  };
}

export async function fetchSyncState(): Promise<SyncState> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/sync/state`);
  if (!r.ok) throw new Error(`sync/state: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "sync/state failed"));
  return decodeSyncState(data.state);
}

export async function fetchStatus(scope?: "downloads"): Promise<BackendStatus> {
  const url = await backendUrl();
  const suffix = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  const r = await apiFetch(`${url}/api/status${suffix}`);
  if (!r.ok) throw new Error(`status: ${r.status}`);
  return decodeBackendStatus(await readJsonRecord(r));
}

export async function postAction(path: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!r.ok) return { success: false, message: `${path}: ${r.status}` };
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}
