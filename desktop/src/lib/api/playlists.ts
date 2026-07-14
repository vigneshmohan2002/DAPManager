import {
  apiFetch,
  arrayField,
  backendUrl,
  isJsonRecord,
  readJsonRecord,
} from "./client";
import type {
  SmartRuleset,
  Playlist,
  OrphanPlaylist,
  ActionResult,
  CreatePlaylistResult,
  AddToPlaylistResult,
} from "./types";

export async function fetchPlaylists(): Promise<Playlist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/playlists`);
  if (!r.ok) throw new Error(`playlists: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField<Playlist>(data, "playlists");
}

export async function fetchOrphanPlaylists(): Promise<OrphanPlaylist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/orphans/playlists`);
  if (!r.ok) throw new Error(`orphans/playlists: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "orphans/playlists failed"));
  return arrayField<OrphanPlaylist>(data, "playlists");
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
  return (await readJsonRecord(r)) as CreatePlaylistResult;
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
  const existing = arrayField<unknown>(listData, "tracks").flatMap((track) =>
    isJsonRecord(track) && typeof track.mbid === "string" ? [track.mbid] : [],
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
