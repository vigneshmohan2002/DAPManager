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

export type LibraryTrack = Track & {
  album_id: string | null;
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
