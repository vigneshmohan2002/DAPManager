import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import TopBar from "../components/TopBar";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbums,
  fetchArtistInfo,
  type Album,
  type Artist,
  type ArtistInfo,
} from "../lib/api";

type Props = {
  artist: Artist;
  onBack: () => void;
  onOpenAlbum: (album: Album) => void;
};

export default function ArtistDetailScreen({ artist, onBack, onOpenAlbum }: Props) {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [base, setBase] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [info, setInfo] = useState<ArtistInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [url, all] = await Promise.all([backendUrl(), fetchAlbums()]);
        if (cancelled) return;
        setBase(url);
        setAlbums(all.filter((a) => a.artist === artist.name));
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [artist.name]);

  useEffect(() => {
    let cancelled = false;
    setInfo(null);
    setInfoLoading(true);
    fetchArtistInfo(artist.name)
      .then((i) => {
        if (!cancelled) setInfo(i);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setInfoLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [artist.name]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return albums;
    return albums.filter((a) => a.title.toLowerCase().includes(q));
  }, [albums, search]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title={artist.name}
        subtitle={`${artist.album_count} albums · ${artist.track_count} tracks`}
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <button
          onClick={onBack}
          className="mb-4 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          ← All artists
        </button>
        {infoLoading ? (
          <div className="mb-6 h-24 rounded-md bg-[var(--color-surface)]/40 animate-pulse" />
        ) : info ? (
          <div className="mb-6 flex gap-4 p-4 rounded-md bg-[var(--color-surface)]/40">
            {info.image_url ? (
              <img
                src={info.image_url}
                alt=""
                className="w-20 h-20 rounded object-cover shrink-0"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
              />
            ) : null}
            <div className="flex-1 min-w-0">
              <p className="text-sm leading-snug text-[var(--color-text)] line-clamp-4">
                {info.summary}
              </p>
              {info.source_url ? (
                <a
                  href={info.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block text-xs text-[var(--color-accent)] hover:underline"
                >
                  Read on Wikipedia →
                </a>
              ) : null}
            </div>
          </div>
        ) : null}
        {loading ? (
          <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
        ) : error ? (
          <div className="text-[var(--color-accent)] text-sm">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="text-[var(--color-text-muted)] text-sm">
            No albums for this artist.
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-6">
            {filtered.map((a) => (
              <AlbumCard
                key={a.id}
                album={a}
                coverUrl={albumCoverUrl(base, a.id)}
                onClick={() => onOpenAlbum(a)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
