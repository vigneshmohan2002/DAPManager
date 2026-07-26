import {
  apiFetch,
  arrayField,
  authenticatedMediaUrl,
  backendUrl,
  isJsonRecord,
  isNullableNumber,
  isNullableString,
  isNumber,
  isString,
  readJsonRecord,
  recordField,
} from "./client";
import type {
  Album,
  Artist,
  Track,
  LibraryTrack,
  SearchTrackResult,
  FetchTracksOptions,
  ArtistInfo,
  TagMeta,
  IdentifyCandidate,
  IdentifyResult,
  OrphanTrack,
  PlayStatsTrack,
  PlayStatsArtist,
  PlayStatsRecent,
  PlayStats,
  FetchPlayStatsOptions,
  WrappedTopTrack,
  WrappedTopArtist,
  WrappedTopAlbum,
  WrappedPayload,
  HomeLikedPreview,
  HomeJumpBackIn,
  HomeDailyMix,
  HomePayload,
  ArtistRadio,
  LyricsResponse,
  ActionResult,
} from "./types";

function isOptionalBoolean(value: unknown): value is boolean | undefined {
  return value === undefined || typeof value === "boolean";
}

function isOptionalStringArray(value: unknown): value is string[] | undefined {
  return (
    value === undefined ||
    (Array.isArray(value) && value.every((item) => isString(item)))
  );
}

function isOptionalNullableString(
  value: unknown,
): value is string | null | undefined {
  return value === undefined || isNullableString(value);
}

function isTrack(value: unknown): value is Track {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNullableString(value.album) &&
    isNullableNumber(value.track_number) &&
    isNullableNumber(value.disc_number) &&
    isOptionalBoolean(value.is_liked)
  );
}

function isAvailability(value: unknown): value is LibraryTrack["availability"] {
  return (
    value === "local" ||
    value === "drive" ||
    value === "remote" ||
    value === "unavailable"
  );
}

function isLibraryTrack(value: unknown): value is LibraryTrack {
  if (!isJsonRecord(value)) return false;
  const extraFields: Record<string, unknown> = value;
  if (!isTrack(value)) return false;
  return (
    isNullableString(extraFields.album_id) &&
    isAvailability(extraFields.availability) &&
    typeof extraFields.is_liked === "boolean" &&
    isOptionalBoolean(extraFields.orphan)
  );
}

function isAlbum(value: unknown): value is Album {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.id) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNumber(value.track_count) &&
    isOptionalNullableString(value.primary_artist) &&
    isOptionalStringArray(value.credited_artists)
  );
}

function isArtist(value: unknown): value is Artist {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.name) &&
    isNumber(value.album_count) &&
    isNumber(value.track_count)
  );
}

function isSearchTrackResult(value: unknown): value is SearchTrackResult {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNullableString(value.album) &&
    isNullableString(value.path)
  );
}

function isArtistInfo(value: unknown): value is ArtistInfo {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.summary) &&
    isNullableString(value.source_url) &&
    isNullableString(value.image_url) &&
    isString(value.title)
  );
}

function isTagMeta(value: unknown): value is TagMeta {
  if (!isJsonRecord(value)) return false;
  return (
    (value.artist === undefined || isString(value.artist)) &&
    (value.album_artist === undefined || isString(value.album_artist)) &&
    (value.album === undefined || isString(value.album)) &&
    (value.title === undefined || isString(value.title)) &&
    (value.date === undefined || isString(value.date)) &&
    (value.track_number === undefined || isString(value.track_number)) &&
    (value.disc_number === undefined || isString(value.disc_number)) &&
    (value.mbid === undefined || isString(value.mbid)) &&
    (value.release_mbid === undefined || isString(value.release_mbid))
  );
}

function decodeTagMeta(value: unknown): TagMeta {
  if (!isJsonRecord(value)) return {};
  const decoded: TagMeta = {};
  if (isString(value.artist)) decoded.artist = value.artist;
  if (isString(value.album_artist)) decoded.album_artist = value.album_artist;
  if (isString(value.album)) decoded.album = value.album;
  if (isString(value.title)) decoded.title = value.title;
  if (isString(value.date)) decoded.date = value.date;
  if (isString(value.track_number)) decoded.track_number = value.track_number;
  if (isString(value.disc_number)) decoded.disc_number = value.disc_number;
  if (isString(value.mbid)) decoded.mbid = value.mbid;
  if (isString(value.release_mbid)) decoded.release_mbid = value.release_mbid;
  return decoded;
}

function isTagTier(value: unknown): value is IdentifyCandidate["tier"] {
  return value === "green" || value === "yellow" || value === "red";
}

function isIdentifyCandidate(value: unknown): value is IdentifyCandidate {
  if (!isJsonRecord(value)) return false;
  return (
    isNumber(value.score) &&
    isTagTier(value.tier) &&
    isTagMeta(value.meta) &&
    isTagMeta(value.current)
  );
}

function isPlayStatsTrack(value: unknown): value is PlayStatsTrack {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isNullableString(value.title) &&
    isNullableString(value.artist) &&
    isNullableString(value.album) &&
    isNumber(value.plays)
  );
}

function isPlayStatsArtist(value: unknown): value is PlayStatsArtist {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.artist) &&
    isNumber(value.plays) &&
    isNumber(value.distinct_tracks)
  );
}

function isPlayStatsRecent(value: unknown): value is PlayStatsRecent {
  if (!isJsonRecord(value)) return false;
  return (
    isNumber(value.id) &&
    isString(value.mbid) &&
    isString(value.played_at) &&
    isNullableString(value.source) &&
    isNullableString(value.title) &&
    isNullableString(value.artist) &&
    isNullableString(value.album) &&
    isNullableString(value.album_id)
  );
}

function isWrappedTopTrack(value: unknown): value is WrappedTopTrack {
  return isPlayStatsTrack(value);
}

function isWrappedTopArtist(value: unknown): value is WrappedTopArtist {
  return isPlayStatsArtist(value);
}

function isWrappedTopAlbum(value: unknown): value is WrappedTopAlbum {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.album_id) &&
    isNullableString(value.album) &&
    isNullableString(value.artist) &&
    isNumber(value.plays)
  );
}

function isBusiestDay(
  value: unknown,
): value is NonNullable<WrappedPayload["busiest_day"]> {
  return (
    isJsonRecord(value) && isString(value.date) && isNumber(value.plays)
  );
}

function isFirstPlay(
  value: unknown,
): value is NonNullable<WrappedPayload["first_play"]> {
  return (
    isJsonRecord(value) &&
    isString(value.played_at) &&
    isNullableString(value.title) &&
    isNullableString(value.artist)
  );
}

function decodeWrappedPayload(data: Record<string, unknown>): WrappedPayload {
  return {
    year: isNumber(data.year) ? data.year : 0,
    total_plays: isNumber(data.total_plays) ? data.total_plays : 0,
    total_listening_time_ms: isNumber(data.total_listening_time_ms)
      ? data.total_listening_time_ms
      : 0,
    has_legacy_rows: Boolean(data.has_legacy_rows),
    top_track: isWrappedTopTrack(data.top_track) ? data.top_track : null,
    top_artist: isWrappedTopArtist(data.top_artist) ? data.top_artist : null,
    top_album: isWrappedTopAlbum(data.top_album) ? data.top_album : null,
    busiest_day: isBusiestDay(data.busiest_day) ? data.busiest_day : null,
    top_hour: isNumber(data.top_hour) ? data.top_hour : null,
    first_play: isFirstPlay(data.first_play) ? data.first_play : null,
    longest_streak_days: isNumber(data.longest_streak_days)
      ? data.longest_streak_days
      : 0,
  };
}

function isHomeLikedPreview(value: unknown): value is HomeLikedPreview {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isString(value.title) &&
    isString(value.artist) &&
    isNullableString(value.album) &&
    isNullableString(value.album_id)
  );
}

function isHomeJumpBackIn(value: unknown): value is HomeJumpBackIn {
  return (
    isJsonRecord(value) &&
    isString(value.album_id) &&
    isString(value.title) &&
    isString(value.artist)
  );
}

function isHomeDailyMix(value: unknown): value is HomeDailyMix {
  return (
    isJsonRecord(value) &&
    isString(value.playlist_id) &&
    isString(value.name) &&
    isString(value.tag) &&
    isNumber(value.track_count)
  );
}

function isOrphanTrack(value: unknown): value is OrphanTrack {
  if (!isJsonRecord(value)) return false;
  return (
    isString(value.mbid) &&
    isNullableString(value.artist) &&
    isNullableString(value.title) &&
    isNullableString(value.album) &&
    isNullableString(value.deleted_at) &&
    isNullableString(value.local_path)
  );
}

export async function fetchAlbums(): Promise<Album[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/albums`);
  if (!r.ok) throw new Error(`albums: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "albums", isAlbum);
}

export async function searchTracks(query: string): Promise<SearchTrackResult[]> {
  const q = query.trim();
  if (!q) return [];
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/search?q=${encodeURIComponent(q)}`,
  );
  if (!r.ok) throw new Error(`search: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "results", isSearchTrackResult);
}

export async function fetchAllTracks(
  opts: FetchTracksOptions = {},
): Promise<LibraryTrack[]> {
  const url = await backendUrl();
  const params = new URLSearchParams();
  if (opts.playlistId) params.set("playlist_id", opts.playlistId);
  if (opts.localOnly) params.set("local_only", "1");
  if (opts.includeOrphans) params.set("include_orphans", "1");
  const qs = params.toString();
  const r = await apiFetch(`${url}/api/library/tracks${qs ? `?${qs}` : ""}`);
  if (!r.ok) throw new Error(`tracks: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "tracks", isLibraryTrack);
}

export async function fetchArtists(): Promise<Artist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/artists`);
  if (!r.ok) throw new Error(`artists: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "artists", isArtist);
}

export function albumCoverUrl(base: string, albumId: string): string {
  return authenticatedMediaUrl(
    `${base}/api/library/albums/${encodeURIComponent(albumId)}/cover`,
  );
}

export async function fetchArtistInfo(name: string): Promise<ArtistInfo | null> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/artists/${encodeURIComponent(name)}/info`,
  );
  if (!r.ok) return null;
  const data = await readJsonRecord(r);
  const info = recordField(data, "info");
  return data.success && isArtistInfo(info) ? info : null;
}

export async function fetchAlbumTracks(albumId: string): Promise<Track[]> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/albums/${encodeURIComponent(albumId)}/tracks`,
  );
  if (!r.ok) throw new Error(`tracks: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField(data, "tracks", isTrack);
}

export function streamUrl(base: string, mbid: string): string {
  return authenticatedMediaUrl(`${base}/api/stream/${encodeURIComponent(mbid)}`);
}

export async function identifyTrack(mbid: string): Promise<IdentifyResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tag/identify/${encodeURIComponent(mbid)}`,
    { method: "POST" },
  );
  const data = await readJsonRecord(r);
  if (!data.success) {
    const msg = String(data.message ?? "identify failed");
    if (msg.includes("acoustid_api_key")) {
      return { kind: "needs_config", key: "acoustid_api_key", message: msg };
    }
    return { kind: "error", message: msg };
  }
  if (!data.candidate) {
    return { kind: "no_match", current: decodeTagMeta(data.current) };
  }
  if (!isIdentifyCandidate(data.candidate)) {
    return { kind: "error", message: "identify returned an invalid candidate" };
  }
  return {
    kind: "match",
    candidate: data.candidate,
    localPath: String(data.local_path ?? ""),
  };
}

export async function applyTrackTags(
  mbid: string,
  meta: TagMeta,
): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tag/apply/${encodeURIComponent(mbid)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ meta }),
    },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

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
    await apiFetch(`${url}/api/library/plays`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    console.warn("recordPlay failed", e);
  }
}

export async function fetchWrapped(year?: number): Promise<WrappedPayload> {
  const url = await backendUrl();
  const params = new URLSearchParams();
  if (typeof year === "number") params.set("year", String(year));
  const qs = params.toString();
  const r = await apiFetch(
    `${url}/api/library/wrapped${qs ? `?${qs}` : ""}`,
  );
  if (!r.ok) throw new Error(`wrapped: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "wrapped failed"));
  return decodeWrappedPayload(data);
}

export async function fetchHome(): Promise<HomePayload> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/home`);
  if (!r.ok) throw new Error(`home: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "home failed"));
  return {
    recent: arrayField(data, "recent", isPlayStatsRecent),
    top_artists: arrayField(data, "top_artists", isPlayStatsArtist),
    liked: {
      total: Number(recordField(data, "liked").total ?? 0),
      preview: arrayField(
        recordField(data, "liked"),
        "preview",
        isHomeLikedPreview,
      ),
    },
    jump_back_in: arrayField(data, "jump_back_in", isHomeJumpBackIn),
    daily_mixes: arrayField(data, "daily_mixes", isHomeDailyMix),
  };
}

export async function regenerateDailyMixes(): Promise<{
  success: boolean;
  mixes: number;
  reason: string | null;
  message?: string;
}> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/daily-mixes/regenerate`, {
    method: "POST",
  });
  const data = await readJsonRecord(r);
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
  const r = await apiFetch(
    `${url}/api/library/play-stats${qs ? `?${qs}` : ""}`,
  );
  if (!r.ok) throw new Error(`play-stats: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "play-stats failed"));
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
    top_tracks: arrayField(data, "top_tracks", isPlayStatsTrack),
    top_artists: arrayField(data, "top_artists", isPlayStatsArtist),
    recent: arrayField(data, "recent", isPlayStatsRecent),
    hour_of_day: hours,
  };
}

export async function fetchOrphanTracks(): Promise<OrphanTrack[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/orphans/tracks`);
  if (!r.ok) throw new Error(`orphans/tracks: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success)
    throw new Error(String(data.message ?? "orphans/tracks failed"));
  return arrayField(data, "tracks", isOrphanTrack);
}

export async function fetchArtistRadio(
  name: string,
  limit = 50,
): Promise<ArtistRadio> {
  const url = await backendUrl();
  const params = new URLSearchParams({ limit: String(limit) });
  const r = await apiFetch(
    `${url}/api/library/artists/${encodeURIComponent(name)}/radio?${params.toString()}`,
  );
  if (!r.ok) throw new Error(`radio: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "radio failed"));
  return {
    tracks: arrayField(data, "tracks", isLibraryTrack),
    top_tag: typeof data.top_tag === "string" ? data.top_tag : null,
    seed_count: Number(data.seed_count ?? 0),
    related_count: Number(data.related_count ?? 0),
  };
}

export async function startTagBackfill(
  incremental = true,
): Promise<{ success: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/tags/backfill`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ incremental }),
  });
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function fetchLyrics(mbid: string): Promise<LyricsResponse> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/lyrics`,
  );
  if (r.status === 404) return { lrc: null, synced: false, source: null, fetched_at: null };
  if (!r.ok) throw new Error(`lyrics: ${r.status}`);
  const data = await readJsonRecord(r);
  const source = data.source;
  return {
    lrc: typeof data.lrc === "string" ? data.lrc : null,
    synced: Boolean(data.synced),
    source: source === "lrclib" || source === "manual" ? source : null,
    fetched_at: typeof data.fetched_at === "string" ? data.fetched_at : null,
    stale: Boolean(data.stale),
  };
}

export async function saveLyrics(
  mbid: string,
  lrc: string,
  synced: boolean,
): Promise<LyricsResponse> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/lyrics`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lrc, synced }),
    },
  );
  if (!r.ok) throw new Error(`saveLyrics: ${r.status}`);
  const data = await readJsonRecord(r);
  const source = data.source;
  return {
    lrc: typeof data.lrc === "string" ? data.lrc : null,
    synced: Boolean(data.synced),
    source: source === "lrclib" || source === "manual" ? source : null,
    fetched_at: typeof data.fetched_at === "string" ? data.fetched_at : null,
  };
}

export async function setTrackLiked(
  mbid: string,
  liked: boolean,
): Promise<{ success: boolean; liked?: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/tracks/${encodeURIComponent(mbid)}/like`,
    { method: liked ? "POST" : "DELETE" },
  );
  if (r.status === 404) {
    // The mbid no longer exists in the library (purged, never inserted,
    // or scrambled by a stale UI). Return a typed failure rather than
    // throwing — callers branch on this to drop the row from the table.
    return { success: false, message: "track not found" };
  }
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    liked: typeof data.liked === "boolean" ? data.liked : undefined,
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function restoreTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}/restore`,
    { method: "POST" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function purgeTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}?purge=true`,
    { method: "DELETE" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function deleteTrackFile(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}/file`,
    { method: "DELETE" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}

export async function softDeleteTrack(mbid: string): Promise<ActionResult> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/tracks/${encodeURIComponent(mbid)}`,
    { method: "DELETE" },
  );
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
  };
}
