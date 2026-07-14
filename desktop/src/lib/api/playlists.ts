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
import type {
  SmartField,
  SmartOp,
  SmartRule,
  SmartRuleset,
  Playlist,
  OrphanPlaylist,
  ActionResult,
  CreatePlaylistResult,
  AddToPlaylistResult,
} from "./types";

function isSmartField(value: unknown): value is SmartField {
  return (
    value === "artist" ||
    value === "album" ||
    value === "title" ||
    value === "tag_tier" ||
    value === "tag_score"
  );
}

function isSmartOperator(value: unknown): value is SmartOp {
  return (
    value === "contains" ||
    value === "equals" ||
    value === "starts_with" ||
    value === "ends_with" ||
    value === "gt" ||
    value === "lt"
  );
}

function isSmartRule(value: unknown): value is SmartRule {
  if (!isJsonRecord(value)) return false;
  return (
    isSmartField(value.field) &&
    isSmartOperator(value.op) &&
    (isString(value.value) || isNumber(value.value))
  );
}

function isSmartRuleset(value: unknown): value is SmartRuleset {
  if (!isJsonRecord(value)) return false;
  return (
    (value.match === "all" || value.match === "any") &&
    Array.isArray(value.rules) &&
    value.rules.every(isSmartRule)
  );
}

function isPlaylist(value: unknown): value is Playlist {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.playlist_id) &&
    isString(value.name) &&
    isNumber(value.track_count) &&
    isString(value.updated_at) &&
    (value.smart_rules === null || isSmartRuleset(value.smart_rules))
  );
}

function isOrphanPlaylist(value: unknown): value is OrphanPlaylist {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.playlist_id) &&
    isString(value.name) &&
    isNullableString(value.deleted_at) &&
    isNumber(value.track_count)
  );
}

function isTrackReference(value: unknown): value is { mbid: string } {
  return isJsonRecord(value) && isString(value.mbid);
}

function decodeCreatePlaylistResult(
  data: Record<string, unknown>,
): CreatePlaylistResult {
  return {
    success: Boolean(data.success),
    message: isString(data.message) ? data.message : undefined,
    playlist_id: isString(data.playlist_id) ? data.playlist_id : undefined,
    name: isString(data.name) ? data.name : undefined,
  };
}

export async function fetchPlaylists(): Promise<Playlist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/playlists`);
  if (!r.ok) throw new Error(`playlists: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "playlists", isPlaylist);
}

export async function fetchOrphanPlaylists(): Promise<OrphanPlaylist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/orphans/playlists`);
  if (!r.ok) throw new Error(`orphans/playlists: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "orphans/playlists failed"));
  return arrayField(data, "playlists", isOrphanPlaylist);
}

export async function restorePlaylist(pid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/playlists/${encodeURIComponent(pid)}/restore`,
    { method: "POST" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function purgePlaylist(pid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/playlists/${encodeURIComponent(pid)}?purge=true`,
    { method: "DELETE" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function createPlaylist(
  name: string,
  smartRules?: SmartRuleset | null,
): Promise<CreatePlaylistResult> {
  const url = await backendUrl();
  const body: { name: string; smart_rules?: SmartRuleset | null } = { name };
  // Only include the key when caller passed something — the server
  // distinguishes "missing" from "explicit null" elsewhere, and
  // create has no use for null (a fresh playlist with null rules is
  // just a static playlist).
  if (smartRules !== undefined) body.smart_rules = smartRules;
  const r = await apiFetch(`${url}/api/library/playlists`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return decodeCreatePlaylistResult(await readJsonRecord(r));
}

export async function updatePlaylistSmartRules(
  playlistId: string,
  smartRules: SmartRuleset | null,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smart_rules: smartRules }),
    },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function renamePlaylist(
  playlistId: string,
  name: string,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deletePlaylist(
  playlistId: string,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    { method: "DELETE" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function addTrackToPlaylist(
  playlistId: string,
  mbid: string,
): Promise<AddToPlaylistResult> {
  const url = await backendUrl();
  const listResp = await apiFetch(
    `${url}/api/library/tracks?playlist_id=${encodeURIComponent(playlistId)}`,
  );
  const listData = await readJsonRecord(listResp);
  if (!listData.success) {
    return {
      success: false,
      message: String(listData.message ?? "lookup failed"),
      added: 0,
      missed: 0,
    };
  }
  const existing = arrayField(listData, "tracks", isTrackReference).map(
    (track) => track.mbid,
  );
  if (existing.includes(mbid)) {
    return { success: true, message: "already in playlist", added: 0, missed: 0 };
  }
  const merged = [...existing, mbid];
  const putResp = await apiFetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_mbids: merged }),
    },
  );
  const putData = await readJsonRecord(putResp);
  if (!putData.success) {
    return {
      success: false,
      message: String(putData.message ?? "update failed"),
      added: 0,
      missed: 0,
    };
  }
  const missed = Math.max(
    0,
    Number(putData.requested ?? merged.length) - Number(putData.landed ?? 0),
  );
  return { success: true, message: "", added: 1, missed };
}
