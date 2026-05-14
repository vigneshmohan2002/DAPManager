import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbums,
  fetchArtistInfo,
  fetchArtistRadio,
  type Album,
  type Artist,
  type ArtistInfo,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

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
  const [radioLoading, setRadioLoading] = useState(false);
  const { play } = usePlayer();
  const toast = useToast();

  const handleRadio = async () => {
    setRadioLoading(true);
    try {
      const result = await fetchArtistRadio(artist.name);
      if (result.tracks.length === 0) {
        toast.show("No playable tracks found for this radio.", "err");
        return;
      }
      const queue = result.tracks.map((t) => ({
        ...t,
        albumId: t.album_id,
      }));
      play(queue, 0);
      // The breakdown tells users why the queue looks the way it does
      // — especially important when the related pool is empty because
      // the tag backfill hasn't run yet.
      if (result.related_count === 0) {
        toast.show(
          result.top_tag
            ? `Radio: ${artist.name} (no matching artists yet)`
            : `Radio: ${artist.name} (run a tag backfill in Settings for richer mixes)`,
        );
      } else {
        toast.show(
          `Radio: ${artist.name} · ${result.related_count} related (${result.top_tag})`,
        );
      }
    } catch (e) {
      toast.show(`Radio failed: ${e}`, "err");
    } finally {
      setRadioLoading(false);
    }
  };

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
        <div className="mb-4 flex items-center justify-between">
          <button
            onClick={onBack}
            className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            ← All artists
          </button>
          <button
            onClick={handleRadio}
            disabled={radioLoading || albums.length === 0}
            title="Start a Spotify-style radio seeded on this artist"
            className="px-4 py-1.5 rounded-full bg-[var(--color-accent)] text-white text-sm font-medium disabled:opacity-40"
          >
            {radioLoading ? "Loading…" : "📻 Start Radio"}
          </button>
        </div>
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
