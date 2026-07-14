import {
  apiFetch,
  arrayField,
  authenticatedMediaUrl,
  backendUrl,
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
  WrappedPayload,
  HomeLikedPreview,
  HomeJumpBackIn,
  HomeDailyMix,
  HomePayload,
  ArtistRadio,
  LyricsResponse,
  ActionResult,
} from "./types";

export async function fetchAlbums(): Promise<Album[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/albums`);
  if (!r.ok) throw new Error(`albums: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField<Album>(data, "albums");
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
  return arrayField<SearchTrackResult>(data, "results");
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
  return arrayField<LibraryTrack>(data, "tracks");
}

export async function fetchArtists(): Promise<Artist[]> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/artists`);
  if (!r.ok) throw new Error(`artists: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField<Artist>(data, "artists");
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
  return data.success ? (recordField(data, "info") as ArtistInfo) : null;
}

export async function fetchAlbumTracks(albumId: string): Promise<Track[]> {
  const url = await backendUrl();
  const r = await apiFetch(
    `${url}/api/library/albums/${encodeURIComponent(albumId)}/tracks`,
  );
  if (!r.ok) throw new Error(`tracks: ${r.status}`);
  const data = await readJsonRecord(r);
  return arrayField<Track>(data, "tracks");
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
    return { kind: "no_match", current: recordField(data, "current") as TagMeta };
  }
  return {
    kind: "match",
    candidate: recordField(data, "candidate") as IdentifyCandidate,
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
  return data as WrappedPayload;
}

export async function fetchHome(): Promise<HomePayload> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/library/home`);
  if (!r.ok) throw new Error(`home: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "home failed"));
  return {
    recent: arrayField<PlayStatsRecent>(data, "recent"),
    top_artists: arrayField<PlayStatsArtist>(data, "top_artists"),
    liked: {
      total: Number(recordField(data, "liked").total ?? 0),
      preview: arrayField<HomeLikedPreview>(recordField(data, "liked"), "preview"),
    },
    jump_back_in: arrayField<HomeJumpBackIn>(data, "jump_back_in"),
    daily_mixes: arrayField<HomeDailyMix>(data, "daily_mixes"),
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
    top_tracks: arrayField<PlayStatsTrack>(data, "top_tracks"),
    top_artists: arrayField<PlayStatsArtist>(data, "top_artists"),
    recent: arrayField<PlayStatsRecent>(data, "recent"),
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
  return arrayField<OrphanTrack>(data, "tracks");
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
    tracks: arrayField<LibraryTrack>(data, "tracks"),
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
