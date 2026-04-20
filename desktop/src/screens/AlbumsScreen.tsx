import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import TopBar from "../components/TopBar";
import { albumCoverUrl, backendUrl, fetchAlbums, type Album } from "../lib/api";

type Props = {
  ready: boolean;
  onOpen: (album: Album) => void;
};

export default function AlbumsScreen({ ready, onOpen }: Props) {
  const [albums, setAlbums] = useState<Album[]>([]);
  const [base, setBase] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const [url, data] = await Promise.all([backendUrl(), fetchAlbums()]);
        if (cancelled) return;
        setBase(url);
        setAlbums(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return albums;
    return albums.filter(
      (a) =>
        a.title.toLowerCase().includes(q) ||
        a.artist.toLowerCase().includes(q),
    );
  }, [albums, search]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Albums"
        subtitle={`${filtered.length} of ${albums.length}`}
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {!ready || loading ? (
          <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
        ) : error ? (
          <div className="text-[var(--color-accent)] text-sm">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="text-[var(--color-text-muted)] text-sm">
            No albums yet. Scan your library to populate.
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-6">
            {filtered.map((a) => (
              <AlbumCard
                key={a.id}
                album={a}
                coverUrl={albumCoverUrl(base, a.id)}
                onClick={() => onOpen(a)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
