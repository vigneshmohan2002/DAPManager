import {
  apiFetch,
  arrayField,
  backendUrl,
  isJsonRecord,
  isNumber,
  isString,
  readJsonRecord,
  recordField,
} from "./client";
import type {
  DuplicateCandidate,
  DuplicateGroup,
  ResolveDuplicateResult,
  IncompleteAlbum,
  ActionResult,
} from "./types";

function isDuplicateCandidate(value: unknown): value is DuplicateCandidate {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.path) &&
    isNumber(value.score) &&
    (value.is_recommended === undefined ||
      typeof value.is_recommended === "boolean") &&
    (value.exists === undefined || typeof value.exists === "boolean") &&
    (value.is_safe_file === undefined ||
      typeof value.is_safe_file === "boolean") &&
    (value.identity_status === undefined ||
      value.identity_status === "match" ||
      value.identity_status === "mismatch" ||
      value.identity_status === "unknown") &&
    (value.release_mbid === undefined || isString(value.release_mbid))
  );
}

function isDuplicateGroup(value: unknown): value is DuplicateGroup {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.artist) &&
    isString(value.title) &&
    (value.release_conflict === undefined ||
      typeof value.release_conflict === "boolean") &&
    Array.isArray(value.candidates) &&
    value.candidates.every(isDuplicateCandidate)
  );
}

function isIncompleteAlbum(value: unknown): value is IncompleteAlbum {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.artist) &&
    isString(value.album) &&
    isString(value.mbid) &&
    isNumber(value.have) &&
    isNumber(value.total) &&
    isNumber(value.missing) &&
    isString(value.cover_art)
  );
}

export async function fetchDuplicates(): Promise<DuplicateGroup[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/duplicates`);
  if (!r.ok) throw new Error(`duplicates: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "duplicates failed"));
  return arrayField(data, "duplicates", isDuplicateGroup);
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
    success: Boolean(data.success) && Boolean(inner.resolved),
    message: String(data.message ?? ""),
    deleted: arrayField(inner, "deleted", isString),
    errors: arrayField(inner, "errors", isString),
    missing: arrayField(inner, "missing", isString),
    remaining: arrayField(inner, "remaining", isString),
    resolved: Boolean(inner.resolved),
  };
}

export async function fetchAuditResults(): Promise<IncompleteAlbum[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/audit/results`);
  if (!r.ok) throw new Error(`audit/results: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "audit failed"));
  return arrayField(data, "results", isIncompleteAlbum);
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
