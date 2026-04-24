import { invoke } from "@tauri-apps/api/core";

export type Album = {
  id: string;
  title: string;
  artist: string;
  track_count: number;
};

export type Artist = {
  name: string;
  album_count: number;
  track_count: number;
};

export type Track = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  track_number: number | null;
  disc_number: number | null;
};

export type Availability = "local" | "drive" | "remote" | "unavailable";

export type LibraryTrack = Track & {
  album_id: string | null;
  availability: Availability;
  // Only present when the caller passed include_orphans=1; absent
  // rows are implicitly not orphans.
  orphan?: boolean;
};

let cachedBackend: string | null = null;

export async function backendUrl(): Promise<string> {
  if (cachedBackend) return cachedBackend;
  cachedBackend = await invoke<string>("backend_url");
  return cachedBackend;
}

export async function waitForBackend(deadlineMs = 30_000): Promise<boolean> {
  const url = await backendUrl();
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(`${url}/api/healthz`);
      if (r.ok) return true;
    } catch {
      // not up yet
    }
    await new Promise((res) => setTimeout(res, 500));
  }
  return false;
}

export async function fetchAlbums(): Promise<Album[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/albums`);
  if (!r.ok) throw new Error(`albums: ${r.status}`);
  const data = await r.json();
  return (data.albums ?? []) as Album[];
}

export type SearchTrackResult = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  path: string | null;
};

export async function searchTracks(query: string): Promise<SearchTrackResult[]> {
  const q = query.trim();
  if (!q) return [];
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/search?q=${encodeURIComponent(q)}`,
  );
  if (!r.ok) throw new Error(`search: ${r.status}`);
  const data = await r.json();
  return (data.results ?? []) as SearchTrackResult[];
}

export type FetchTracksOptions = {
  playlistId?: string;
  localOnly?: boolean;
  includeOrphans?: boolean;
};

export async function fetchAllTracks(
  opts: FetchTracksOptions = {},
): Promise<LibraryTrack[]> {
  const url = await backendUrl();
  const params = new URLSearchParams();
  if (opts.playlistId) params.set("playlist_id", opts.playlistId);
  if (opts.localOnly) params.set("local_only", "1");
  if (opts.includeOrphans) params.set("include_orphans", "1");
  const qs = params.toString();
  const r = await fetch(`${url}/api/library/tracks${qs ? `?${qs}` : ""}`);
  if (!r.ok) throw new Error(`tracks: ${r.status}`);
  const data = await r.json();
  return (data.tracks ?? []) as LibraryTrack[];
}

export type Playlist = {
  playlist_id: string;
  name: string;
  track_count: number;
  updated_at: string;
};

export async function fetchPlaylists(): Promise<Playlist[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/playlists`);
  if (!r.ok) throw new Error(`playlists: ${r.status}`);
  const data = await r.json();
  return (data.playlists ?? []) as Playlist[];
}

export async function fetchArtists(): Promise<Artist[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/artists`);
  if (!r.ok) throw new Error(`artists: ${r.status}`);
  const data = await r.json();
  return (data.artists ?? []) as Artist[];
}

export function albumCoverUrl(base: string, albumId: string): string {
  return `${base}/api/library/albums/${encodeURIComponent(albumId)}/cover`;
}

export async function fetchAlbumTracks(albumId: string): Promise<Track[]> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/albums/${encodeURIComponent(albumId)}/tracks`,
  );
  if (!r.ok) throw new Error(`tracks: ${r.status}`);
  const data = await r.json();
  return (data.tracks ?? []) as Track[];
}

export function streamUrl(base: string, mbid: string): string {
  return `${base}/api/stream/${encodeURIComponent(mbid)}`;
}

export type ConfigGroup = { label: string; keys: string[] };
export type ConfigValue = string | number | boolean | null;
export type ConfigPayload = {
  config: Record<string, ConfigValue>;
  editable_keys: string[];
  secret_keys: string[];
  bool_keys: string[];
  groups: ConfigGroup[];
};

export async function fetchConfig(): Promise<ConfigPayload> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/config`);
  if (!r.ok) throw new Error(`config: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "config failed");
  return {
    config: data.config ?? {},
    editable_keys: data.editable_keys ?? [],
    secret_keys: data.secret_keys ?? [],
    bool_keys: data.bool_keys ?? [],
    groups: data.groups ?? [],
  };
}

export type SaveConfigResult = {
  success: boolean;
  message: string;
  changed: string[];
};

export async function saveConfig(
  patch: Record<string, ConfigValue>,
): Promise<SaveConfigResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    changed: (data.changed ?? []) as string[],
  };
}

export type SyncState = {
  last_catalog_sync: string | null;
  last_playlist_sync: string | null;
  last_playlist_push: string | null;
  last_inventory_report: string | null;
};

export async function fetchSyncState(): Promise<SyncState> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/sync/state`);
  if (!r.ok) throw new Error(`sync/state: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "sync/state failed");
  return data.state as SyncState;
}

export type BackendStatus = {
  running: boolean;
  task: string | null;
  message: string | null;
  detail: string | null;
};

export async function fetchStatus(): Promise<BackendStatus> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/status`);
  if (!r.ok) throw new Error(`status: ${r.status}`);
  return (await r.json()) as BackendStatus;
}

export type FleetDevice = {
  device_id: string;
  track_count: number;
  last_reported_at: string | null;
};

export type FleetHolder = {
  device_id: string;
  local_path: string | null;
  reported_at: string;
};

export type FleetSearchResult = {
  mbid: string;
  artist: string;
  title: string;
  album: string | null;
  device_count: number;
  holders: FleetHolder[];
};

export async function fetchFleetSummary(): Promise<FleetDevice[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/fleet/summary`);
  if (!r.ok) throw new Error(`fleet/summary: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "fleet/summary failed");
  return (data.devices ?? []) as FleetDevice[];
}

export async function searchFleet(q: string): Promise<FleetSearchResult[]> {
  const query = q.trim();
  if (!query) return [];
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/fleet/track?q=${encodeURIComponent(query)}`,
  );
  if (!r.ok) throw new Error(`fleet/track: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "fleet/track failed");
  return (data.results ?? []) as FleetSearchResult[];
}

export type ActionResult = { success: boolean; message: string };

// POST an empty-body action endpoint (sync triggers, link-local, etc).
// The backend's TaskManager returns {success, message} uniformly;
// callers surface `message` so config-gated failures (e.g.
// report_inventory_to_host disabled) land in the UI verbatim.
export async function postAction(path: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!r.ok) return { success: false, message: `${path}: ${r.status}` };
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export type QueueDownloadResult = {
  success: boolean;
  message?: string;
  queued: number;
  skipped_linked: number;
  skipped_queued: number;
  not_found: number;
};

export async function queueCatalogDownload(
  mbids: string[],
): Promise<QueueDownloadResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/catalog/queue-download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mbids }),
  });
  const data = await r.json();
  return data as QueueDownloadResult;
}

export async function softDeleteTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}`,
    { method: "DELETE" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export type CreatePlaylistResult = {
  success: boolean;
  message?: string;
  playlist_id?: string;
  name?: string;
};

export async function createPlaylist(
  name: string,
): Promise<CreatePlaylistResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/playlists`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return (await r.json()) as CreatePlaylistResult;
}

export async function renamePlaylist(
  playlistId: string,
  name: string,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deletePlaylist(
  playlistId: string,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    { method: "DELETE" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

// Add one track to a playlist by merging into existing membership.
// The PUT endpoint replaces the full list, so the caller must
// compose it — mirrors the web /library page's addSelectedToPlaylist.
export type AddToPlaylistResult = {
  success: boolean;
  message: string;
  added: number;
  missed: number;
};

export async function addTrackToPlaylist(
  playlistId: string,
  mbid: string,
): Promise<AddToPlaylistResult> {
  const url = await backendUrl();
  const listResp = await fetch(
    `${url}/api/library/tracks?playlist_id=${encodeURIComponent(playlistId)}`,
  );
  const listData = await listResp.json();
  if (!listData.success) {
    return {
      success: false,
      message: String(listData.message ?? "lookup failed"),
      added: 0,
      missed: 0,
    };
  }
  const existing: string[] = (listData.tracks ?? []).map(
    (t: { mbid: string }) => t.mbid,
  );
  if (existing.includes(mbid)) {
    return { success: true, message: "already in playlist", added: 0, missed: 0 };
  }
  const merged = [...existing, mbid];
  const putResp = await fetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ track_mbids: merged }),
    },
  );
  const putData = await putResp.json();
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
