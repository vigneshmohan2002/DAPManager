import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import { albumCoverUrl, backendUrl, fetchAlbums, type Album } from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

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
  const { playAlbum } = usePlayer();
  const toast = useToast();

  const handlePlayAlbum = async (album: Album) => {
    try {
      const count = await playAlbum(album.id);
      if (count === 0) toast.show("No playable tracks in this album.", "err");
    } catch (e) {
      toast.show(`Could not play album: ${e}`, "err");
    }
  };

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
        subtitle={
          search
            ? `${filtered.length} of ${albums.length}`
            : `${albums.length} ${albums.length === 1 ? "album" : "albums"}`
        }
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto px-5 py-5">
        {!ready || loading ? (
          <div className="text-[11px] text-[var(--color-text-muted)]">
            Loading…
          </div>
        ) : error ? (
          <div className="text-[11px] text-[var(--color-danger)]">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="text-[11px] text-[var(--color-text-muted)]">
            No albums yet. Scan your library to populate.
          </div>
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-x-4 gap-y-5">
            {filtered.map((a) => (
              <AlbumCard
                key={a.id}
                album={a}
                coverUrl={albumCoverUrl(base, a.id)}
                onClick={() => onOpen(a)}
                onDoubleClick={() => void handlePlayAlbum(a)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
