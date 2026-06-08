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
  // Optional because not every consumer of Track carries the hearted
  // state (search results, ad-hoc constructed rows). Present on rows
  // coming from /api/library/tracks and /api/library/albums/.../tracks.
  is_liked?: boolean;
};

export type Availability = "local" | "drive" | "remote" | "unavailable";

export type LibraryTrack = Track & {
  album_id: string | null;
  availability: Availability;
  // Server-side hearted state. Present on every row served from
  // /api/library/tracks; the heart toggle flips this through
  // setTrackLiked and the consumer expects the update to surface
  // optimistically.
  is_liked: boolean;
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

// 5 minutes: on first launch the app creates a venv and runs `pip install`
// before the Python server is ready. Give it enough time.
export async function waitForBackend(deadlineMs = 300_000): Promise<boolean> {
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

// Mirrors the server-side whitelist in src/smart_playlist.py. Keep
// these in sync; the dialog uses them to gate which ops are offered
// per field, and the server re-validates either way.
export type SmartField =
  | "artist"
  | "album"
  | "title"
  | "tag_tier"
  | "tag_score";

export type SmartOp =
  | "contains"
  | "equals"
  | "starts_with"
  | "ends_with"
  | "gt"
  | "lt";

export type SmartRule = {
  field: SmartField;
  op: SmartOp;
  value: string | number;
};

export type SmartRuleset = {
  match: "all" | "any";
  rules: SmartRule[];
};

export type Playlist = {
  playlist_id: string;
  name: string;
  track_count: number;
  updated_at: string;
  // Decoded by the GET endpoint; null means "static playlist". Static
  // and smart are distinguished only by truthiness of this field.
  smart_rules: SmartRuleset | null;
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

export type ArtistInfo = {
  summary: string;
  source_url: string | null;
  image_url: string | null;
  title: string;
};

export async function fetchArtistInfo(name: string): Promise<ArtistInfo | null> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/artists/${encodeURIComponent(name)}/info`,
  );
  if (!r.ok) return null;
  const data = await r.json();
  return data.success ? (data.info as ArtistInfo) : null;
}

// Album tracks now carry is_liked from the backend so the detail
// screen can render a heart column without a second per-row fetch.
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

export type TagTier = "green" | "yellow" | "red";

export type TagMeta = {
  artist?: string;
  album_artist?: string;
  album?: string;
  title?: string;
  date?: string;
  track_number?: string;
  disc_number?: string;
  mbid?: string;
  release_mbid?: string;
};

export type IdentifyCandidate = {
  score: number;
  tier: TagTier;
  meta: TagMeta;
  current: TagMeta;
};

// Separate kinds so the caller can distinguish "couldn't fingerprint"
// from "the API key is missing" from "no match found" — they all
// want different UIs (retry, open Settings, accept).
export type IdentifyResult =
  | { kind: "match"; candidate: IdentifyCandidate; localPath: string }
  | { kind: "no_match"; current: TagMeta }
  | { kind: "needs_config"; key: string; message: string }
  | { kind: "error"; message: string };

export async function identifyTrack(mbid: string): Promise<IdentifyResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tag/identify/${encodeURIComponent(mbid)}`,
    { method: "POST" },
  );
  const data = await r.json();
  if (!data.success) {
    const msg = String(data.message ?? "identify failed");
    if (msg.includes("acoustid_api_key")) {
      return { kind: "needs_config", key: "acoustid_api_key", message: msg };
    }
    return { kind: "error", message: msg };
  }
  if (!data.candidate) {
    return { kind: "no_match", current: data.current ?? {} };
  }
  return {
    kind: "match",
    candidate: data.candidate as IdentifyCandidate,
    localPath: String(data.local_path ?? ""),
  };
}

export async function applyTrackTags(
  mbid: string,
  meta: TagMeta,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tag/apply/${encodeURIComponent(mbid)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meta }),
    },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
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

export type OrphanTrack = {
  mbid: string;
  artist: string | null;
  title: string | null;
  album: string | null;
  deleted_at: string | null;
  local_path: string | null;
};

export type OrphanPlaylist = {
  playlist_id: string;
  name: string;
  deleted_at: string | null;
  track_count: number;
};

// Fire-and-forget. Records one play event when a track has played long
// enough to count (the player owns that threshold). Failure is logged
// to the console, not surfaced — a missed scrobble is not user-visible
// and shouldn't toast on every transient backend hiccup.
//
// ``listenedMs`` is wall-clock ms the audio was unpaused on this load —
// the server caps it server-side, so over-reporting clients can't
// inflate listening-time stats.
export async function recordPlay(
  mbid: string,
  source: string = "desktop",
  listenedMs?: number,
): Promise<void> {
  try {
    const url = await backendUrl();
    const body: Record<string, unknown> = { mbid, source };
    if (typeof listenedMs === "number" && listenedMs >= 0) {
      body.listened_ms = Math.round(listenedMs);
    }
    await fetch(`${url}/api/library/plays`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    console.warn("recordPlay failed", e);
  }
}

export type PlayStatsTrack = {
  mbid: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  plays: number;
};

export type PlayStatsArtist = {
  artist: string;
  plays: number;
  distinct_tracks: number;
};

export type PlayStatsRecent = {
  id: number;
  mbid: string;
  played_at: string;
  source: string | null;
  title: string | null;
  artist: string | null;
  album: string | null;
  album_id: string | null;
};

export type PlayStats = {
  total: number;
  // Sum of listened ms across the same window the play count uses.
  // Legacy rows (Stage 12a migration boundary) have NULL listened_ms
  // server-side and contribute 0; tooltip copy on the Stats screen
  // notes the partial-history caveat.
  listening_time_ms: number;
  top_tracks: PlayStatsTrack[];
  top_artists: PlayStatsArtist[];
  recent: PlayStatsRecent[];
  // 24-element fixed-width array, index = UTC hour (0..23).
  // Backend pads zeros so the heatmap can render directly.
  hour_of_day: number[];
};

export type FetchPlayStatsOptions = {
  // ISO-8601 cutoff (inclusive); omit for all-time.
  since?: string;
  limit?: number;
};

export type WrappedTopTrack = {
  mbid: string;
  title: string | null;
  artist: string | null;
  album: string | null;
  plays: number;
};

export type WrappedTopArtist = {
  artist: string;
  plays: number;
  distinct_tracks: number;
};

export type WrappedTopAlbum = {
  album_id: string;
  album: string | null;
  artist: string | null;
  plays: number;
};

export type WrappedPayload = {
  year: number;
  total_plays: number;
  total_listening_time_ms: number;
  has_legacy_rows: boolean;
  top_track: WrappedTopTrack | null;
  top_artist: WrappedTopArtist | null;
  top_album: WrappedTopAlbum | null;
  busiest_day: { date: string; plays: number } | null;
  top_hour: number | null;
  first_play: { played_at: string; title: string | null; artist: string | null } | null;
  longest_streak_days: number;
};

export async function fetchWrapped(year?: number): Promise<WrappedPayload> {
  const url = await backendUrl();
  const params = new URLSearchParams();
  if (typeof year === "number") params.set("year", String(year));
  const qs = params.toString();
  const r = await fetch(
    `${url}/api/library/wrapped${qs ? `?${qs}` : ""}`,
  );
  if (!r.ok) throw new Error(`wrapped: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "wrapped failed");
  return data as WrappedPayload;
}

export type HomeLikedPreview = {
  mbid: string;
  title: string;
  artist: string;
  album: string | null;
  album_id: string | null;
};

export type HomeJumpBackIn = {
  album_id: string;
  title: string;
  artist: string;
};

export type HomeDailyMix = {
  playlist_id: string;
  name: string;
  tag: string;
  track_count: number;
};

export type HomePayload = {
  recent: PlayStatsRecent[];
  top_artists: PlayStatsArtist[];
  liked: { total: number; preview: HomeLikedPreview[] };
  jump_back_in: HomeJumpBackIn[];
  daily_mixes: HomeDailyMix[];
};

export async function fetchHome(): Promise<HomePayload> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/home`);
  if (!r.ok) throw new Error(`home: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "home failed");
  return {
    recent: (data.recent ?? []) as PlayStatsRecent[],
    top_artists: (data.top_artists ?? []) as PlayStatsArtist[],
    liked: {
      total: Number(data.liked?.total ?? 0),
      preview: (data.liked?.preview ?? []) as HomeLikedPreview[],
    },
    jump_back_in: (data.jump_back_in ?? []) as HomeJumpBackIn[],
    daily_mixes: (data.daily_mixes ?? []) as HomeDailyMix[],
  };
}

export async function regenerateDailyMixes(): Promise<{
  success: boolean;
  mixes: number;
  reason: string | null;
  message?: string;
}> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/daily-mixes/regenerate`, {
    method: "POST",
  });
  const data = await r.json();
  return {
    success: Boolean(data.success),
    mixes: Number(data.mixes ?? 0),
    reason: typeof data.reason === "string" ? data.reason : null,
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function fetchPlayStats(
  opts: FetchPlayStatsOptions = {},
): Promise<PlayStats> {
  const url = await backendUrl();
  const params = new URLSearchParams();
  if (opts.since) params.set("since", opts.since);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const r = await fetch(
    `${url}/api/library/play-stats${qs ? `?${qs}` : ""}`,
  );
  if (!r.ok) throw new Error(`play-stats: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "play-stats failed");
  // Coerce hour_of_day defensively: backend pads to 24 entries, but a
  // pre-Stage-12b server (or a flaky JSON parse) could deliver fewer.
  const rawHours = Array.isArray(data.hour_of_day) ? data.hour_of_day : [];
  const hours = Array.from(
    { length: 24 },
    (_, i) => Number(rawHours[i] ?? 0) || 0,
  );
  return {
    total: Number(data.total ?? 0),
    listening_time_ms: Number(data.listening_time_ms ?? 0),
    top_tracks: (data.top_tracks ?? []) as PlayStatsTrack[],
    top_artists: (data.top_artists ?? []) as PlayStatsArtist[],
    recent: (data.recent ?? []) as PlayStatsRecent[],
    hour_of_day: hours,
  };
}

export async function fetchOrphanTracks(): Promise<OrphanTrack[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/orphans/tracks`);
  if (!r.ok) throw new Error(`orphans/tracks: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "orphans/tracks failed");
  return (data.tracks ?? []) as OrphanTrack[];
}

export async function fetchOrphanPlaylists(): Promise<OrphanPlaylist[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/orphans/playlists`);
  if (!r.ok) throw new Error(`orphans/playlists: ${r.status}`);
  const data = await r.json();
  if (!data.success)
    throw new Error(data.message ?? "orphans/playlists failed");
  return (data.playlists ?? []) as OrphanPlaylist[];
}

export type ArtistRadio = {
  tracks: LibraryTrack[];
  top_tag: string | null;
  seed_count: number;
  related_count: number;
};

export async function fetchArtistRadio(
  name: string,
  limit = 50,
): Promise<ArtistRadio> {
  const url = await backendUrl();
  const params = new URLSearchParams({ limit: String(limit) });
  const r = await fetch(
    `${url}/api/library/artists/${encodeURIComponent(name)}/radio?${params.toString()}`,
  );
  if (!r.ok) throw new Error(`radio: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "radio failed");
  return {
    tracks: (data.tracks ?? []) as LibraryTrack[],
    top_tag: data.top_tag ?? null,
    seed_count: Number(data.seed_count ?? 0),
    related_count: Number(data.related_count ?? 0),
  };
}

export async function startTagBackfill(
  incremental = true,
): Promise<{ success: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/library/tags/backfill`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incremental }),
  });
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export type LyricsResponse = {
  lrc: string | null;
  synced: boolean;
  source: "lrclib" | "manual" | null;
  fetched_at: string | null;
  // True when the server returned a stale cached row because the live
  // LRCLIB fetch hit a transient network error. UI shows a quiet note;
  // contents are still rendered.
  stale?: boolean;
};

export async function fetchLyrics(mbid: string): Promise<LyricsResponse> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/lyrics`,
  );
  if (r.status === 404) return { lrc: null, synced: false, source: null, fetched_at: null };
  if (!r.ok) throw new Error(`lyrics: ${r.status}`);
  const data = await r.json();
  return {
    lrc: typeof data.lrc === "string" ? data.lrc : null,
    synced: Boolean(data.synced),
    source: data.source ?? null,
    fetched_at: data.fetched_at ?? null,
    stale: Boolean(data.stale),
  };
}

export async function saveLyrics(
  mbid: string,
  lrc: string,
  synced: boolean,
): Promise<LyricsResponse> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/lyrics`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lrc, synced }),
    },
  );
  if (!r.ok) throw new Error(`saveLyrics: ${r.status}`);
  const data = await r.json();
  return {
    lrc: typeof data.lrc === "string" ? data.lrc : null,
    synced: Boolean(data.synced),
    source: data.source ?? null,
    fetched_at: data.fetched_at ?? null,
  };
}

export async function setTrackLiked(
  mbid: string,
  liked: boolean,
): Promise<{ success: boolean; liked?: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/like`,
    { method: liked ? "POST" : "DELETE" },
  );
  if (r.status === 404) {
    // The mbid no longer exists in the library (purged, never inserted,
    // or scrambled by a stale UI). Return a typed failure rather than
    // throwing — callers branch on this to drop the row from the table.
    return { success: false, message: "track not found" };
  }
  const data = await r.json();
  return {
    success: Boolean(data.success),
    liked: typeof data.liked === "boolean" ? data.liked : undefined,
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function restoreTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}/restore`,
    { method: "POST" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function purgeTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}?purge=true`,
    { method: "DELETE" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deleteTrackFile(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}/file`,
    { method: "DELETE" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function restorePlaylist(pid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/playlists/${encodeURIComponent(pid)}/restore`,
    { method: "POST" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function purgePlaylist(pid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/playlists/${encodeURIComponent(pid)}?purge=true`,
    { method: "DELETE" },
  );
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export type SuggestionItem =
  | { artist: string; title: string }
  | { search_query: string };

export type SuggestionResult = {
  success: boolean;
  message: string;
  received: number;
  queued: number;
  skipped: number;
};

// Mirrors desktop_app.py's parse_manual_suggestions: blank / "#"
// lines drop, "artist - title" splits, otherwise free-form query.
export function parseManualSuggestions(text: string): SuggestionItem[] {
  const items: SuggestionItem[] = [];
  for (const raw of (text ?? "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const dash = line.indexOf(" - ");
    if (dash > 0) {
      const artist = line.slice(0, dash).trim();
      const title = line.slice(dash + 3).trim();
      if (artist && title) {
        items.push({ artist, title });
        continue;
      }
    }
    items.push({ search_query: line });
  }
  return items;
}

// ── Setup wizard ────────────────────────────────────────────────────────────

export async function fetchSetupStatus(): Promise<{ needs_setup: boolean }> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/setup/status`);
  if (!r.ok) throw new Error(`setup/status: ${r.status}`);
  return r.json();
}

export type PublicUrlDetection = {
  source: "env" | "tailscale" | "none";
  url?: string;
};

export async function detectPublicUrl(): Promise<PublicUrlDetection> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/setup/detect-public-url`);
  if (!r.ok) return { source: "none" };
  return r.json();
}

export async function validatePath(
  path: string,
  kind: "directory" | "file" = "directory",
): Promise<{ ok: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/setup/validate-path`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, kind }),
  });
  if (!r.ok) return { ok: false, message: `${r.status}` };
  return r.json();
}

export type SetupPayload = {
  role: "master" | "satellite" | "standalone";
  music_library_path: string;
  downloads_path: string;
  dap_mount_point?: string;
  master_url?: string;
  public_master_url?: string;
  device_name?: string;
  slsk_username?: string;
  slsk_password?: string;
  jellyfin_url?: string;
  jellyfin_api_key?: string;
  jellyfin_user_id?: string;
  lidarr_url?: string;
  lidarr_api_key?: string;
  lidarr_enabled?: boolean;
  acoustid_api_key?: string;
  contact_email?: string;
  api_token?: string;
};

export async function saveSetupConfig(
  payload: SetupPayload,
): Promise<{ success: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/save_config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  return { success: Boolean(data.success), message: data.message };
}

export async function postSuggestions(
  host: string,
  items: SuggestionItem[],
): Promise<SuggestionResult> {
  const cleaned = host.replace(/\/+$/, "");
  const r = await fetch(`${cleaned}/api/suggestions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!r.ok) {
    return {
      success: false,
      message: `${r.status} ${r.statusText}`,
      received: 0,
      queued: 0,
      skipped: 0,
    };
  }
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    received: Number(data.received ?? 0),
    queued: Number(data.queued ?? 0),
    skipped: Number(data.skipped ?? 0),
  };
}

export type DuplicateCandidate = {
  path: string;
  score: number;
  is_recommended?: boolean;
};

export type DuplicateGroup = {
  mbid: string;
  artist: string;
  title: string;
  candidates: DuplicateCandidate[];
};

export async function fetchDuplicates(): Promise<DuplicateGroup[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/duplicates`);
  if (!r.ok) throw new Error(`duplicates: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "duplicates failed");
  return (data.duplicates ?? []) as DuplicateGroup[];
}

export type ResolveDuplicateResult = {
  success: boolean;
  message: string;
  deleted: string[];
  errors: string[];
};

export async function resolveDuplicate(
  mbid: string,
  keepPath: string,
  deletePaths: string[],
): Promise<ResolveDuplicateResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/duplicates/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mbid,
      keep_path: keepPath,
      delete_paths: deletePaths,
    }),
  });
  const data = await r.json();
  const inner = (data.result ?? {}) as { deleted?: string[]; errors?: string[] };
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    deleted: inner.deleted ?? [],
    errors: inner.errors ?? [],
  };
}

export type IncompleteAlbum = {
  artist: string;
  album: string;
  mbid: string;
  have: number;
  total: number;
  missing: number;
  cover_art: string;
};

export async function fetchAuditResults(): Promise<IncompleteAlbum[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/audit/results`);
  if (!r.ok) throw new Error(`audit/results: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "audit failed");
  return (data.results ?? []) as IncompleteAlbum[];
}

export async function runCompleteAlbums(
  runDownloads: boolean,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/albums/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_downloads: runDownloads }),
  });
  if (!r.ok) return { success: false, message: `complete: ${r.status}` };
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

// Keys mirror /api/sync/state's response, which aliases the DB-side
// storage keys (last_catalog_sync, etc.) to friendlier step names so
// clients don't have to know the storage layout. The web dashboard
// reads the same shape — keep all three sides in lockstep.
export type SyncState = {
  catalog_pull: string | null;
  playlist_pull: string | null;
  playlist_push: string | null;
  inventory_report: string | null;
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
  smartRules?: SmartRuleset | null,
): Promise<CreatePlaylistResult> {
  const url = await backendUrl();
  const body: { name: string; smart_rules?: SmartRuleset | null } = { name };
  // Only include the key when caller passed something — the server
  // distinguishes "missing" from "explicit null" elsewhere, and
  // create has no use for null (a fresh playlist with null rules is
  // just a static playlist).
  if (smartRules !== undefined) body.smart_rules = smartRules;
  const r = await fetch(`${url}/api/library/playlists`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await r.json()) as CreatePlaylistResult;
}

// Replace the smart-rules ruleset on an existing playlist. Pass null
// to clear (turning a smart playlist back into a static one); the
// server is happy with either an object or explicit null.
export async function updatePlaylistSmartRules(
  playlistId: string,
  smartRules: SmartRuleset | null,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(
    `${url}/api/library/playlists/${encodeURIComponent(playlistId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ smart_rules: smartRules }),
    },
  );
  const data = await r.json();
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

// ---------- Stage 10b: New Releases / Lidarr wanted -------------------

export type WantedRelease = {
  mbid: string;
  artist: string;
  title: string;
  release_date: string | null;
  cover_url: string;
  queued: boolean;
  downloaded: boolean;
};

export type WantedReleasesResult =
  | { kind: "ok"; last_tick: string | null; items: WantedRelease[] }
  | { kind: "disabled" }
  | { kind: "unavailable" }
  | { kind: "error"; message: string };

export async function fetchWantedReleases(): Promise<WantedReleasesResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/releases/wanted`);
  if (r.status === 502) {
    const data = await r.json().catch(() => ({}));
    return { kind: "error", message: String(data.message ?? "Lidarr error") };
  }
  if (!r.ok) {
    return { kind: "error", message: `releases/wanted: ${r.status}` };
  }
  const data = await r.json();
  if (!data.success) {
    if (data.reason === "lidarr_disabled") return { kind: "disabled" };
    if (data.reason === "lidarr_unavailable") return { kind: "unavailable" };
    return { kind: "error", message: String(data.message ?? "unknown") };
  }
  return {
    kind: "ok",
    last_tick: data.last_tick ?? null,
    items: (data.items ?? []) as WantedRelease[],
  };
}

// Queue an album via the same path the release-watcher uses: POST
// /api/download/request with a freeform "Artist - Title" search query
// and the release MBID as mbid_guess. Master-only; the New Releases
// screen only renders when Lidarr is enabled, which is master-only.
export async function queueWantedRelease(
  release: Pick<WantedRelease, "mbid" | "artist" | "title">,
): Promise<ActionResult> {
  const url = await backendUrl();
  const search_query = `${release.artist} - ${release.title}`.trim();
  const r = await fetch(`${url}/api/download/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ search_query, mbid_guess: release.mbid }),
  });
  if (!r.ok) {
    return { success: false, message: `download/request: ${r.status}` };
  }
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

// ---------- Stage 10a: Downloads queue --------------------------------

export type DownloadQueueItem = {
  id: number;
  query: string;
  // Backend status vocabulary is "pending" / "failed" / "success".
  // The UI labels "success" as "Completed" — keep names aligned with
  // the schema so logs / DB / API don't translate three different ways.
  status: "pending" | "failed" | "success" | string;
  last_attempt: string | null;
};

export async function fetchDownloads(): Promise<DownloadQueueItem[]> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/downloads/list`);
  if (!r.ok) throw new Error(`downloads/list: ${r.status}`);
  const data = await r.json();
  if (!data.success) throw new Error(data.message ?? "downloads/list failed");
  return (data.items ?? []) as DownloadQueueItem[];
}

export async function retryDownload(id: number): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/downloads/${id}/retry`, { method: "POST" });
  // The backend emits 404 when the row isn't in 'failed' state; surface
  // that as a typed message rather than a thrown error so the screen can
  // toast it and keep the table mounted.
  if (r.status === 404) {
    return { success: false, message: "row already finished or not failed" };
  }
  if (!r.ok) return { success: false, message: `retry: ${r.status}` };
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deleteDownload(id: number): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/downloads/${id}`, { method: "DELETE" });
  if (!r.ok) return { success: false, message: `delete: ${r.status}` };
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export type ClearCompletedResult = ActionResult & { removed: number };

export async function clearCompletedDownloads(): Promise<ClearCompletedResult> {
  const url = await backendUrl();
  const r = await fetch(`${url}/api/downloads/clear-completed`, {
    method: "POST",
  });
  if (!r.ok) return { success: false, message: `clear: ${r.status}`, removed: 0 };
  const data = await r.json();
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    removed: Number(data.removed ?? 0),
  };
}
