import { invoke } from "@tauri-apps/api/core";

export type Album = {
  id: string;
  title: string;
  artist: string;
  track_count: number;
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

export function albumCoverUrl(base: string, albumId: string): string {
  return `${base}/api/library/albums/${encodeURIComponent(albumId)}/cover`;
}
