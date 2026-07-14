import { apiFetch, backendUrl, readJsonRecord, recordField } from "./client";
import type {
  SyncState,
  BackendStatus,
  ActionResult,
} from "./types";

export async function fetchSyncState(): Promise<SyncState> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/sync/state`);
  if (!r.ok) throw new Error(`sync/state: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "sync/state failed"));
  return recordField(data, "state") as SyncState;
}

export async function fetchStatus(): Promise<BackendStatus> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/status`);
  if (!r.ok) throw new Error(`status: ${r.status}`);
  return (await readJsonRecord(r)) as BackendStatus;
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
