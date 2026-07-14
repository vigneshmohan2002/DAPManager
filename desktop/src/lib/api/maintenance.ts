import {
  apiFetch,
  arrayField,
  backendUrl,
  readJsonRecord,
  recordField,
} from "./client";
import type {
  DuplicateGroup,
  ResolveDuplicateResult,
  IncompleteAlbum,
  ActionResult,
} from "./types";

export async function fetchDuplicates(): Promise<DuplicateGroup[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/duplicates`);
  if (!r.ok) throw new Error(`duplicates: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "duplicates failed"));
  return arrayField<DuplicateGroup>(data, "duplicates");
}

export async function resolveDuplicate(
  mbid: string,
  keepPath: string,
  deletePaths: string[],
): Promise<ResolveDuplicateResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/duplicates/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mbid,
      keep_path: keepPath,
      delete_paths: deletePaths,
    }),
  });
  const data = await readJsonRecord(r);
  const inner = recordField(data, "result");
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    deleted: arrayField<string>(inner, "deleted"),
    errors: arrayField<string>(inner, "errors"),
  };
}

export async function fetchAuditResults(): Promise<IncompleteAlbum[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/audit/results`);
  if (!r.ok) throw new Error(`audit/results: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "audit failed"));
  return arrayField<IncompleteAlbum>(data, "results");
}

export async function runCompleteAlbums(
  runDownloads: boolean,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/albums/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_downloads: runDownloads }),
  });
  if (!r.ok) return { success: false, message: `complete: ${r.status}` };
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}
