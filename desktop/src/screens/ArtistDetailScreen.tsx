import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import TopBar from "../components/TopBar";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbums,
  type Album,
  type Artist,
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
