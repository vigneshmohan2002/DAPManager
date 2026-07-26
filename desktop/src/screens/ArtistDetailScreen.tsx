import { useEffect, useMemo, useState } from "react";
import AlbumCard from "../components/AlbumCard";
import Artwork from "../components/Artwork";
import Icon from "../components/Icon";
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
  const { play, playAlbum } = usePlayer();
  const toast = useToast();

  const handlePlayAlbum = async (album: Album) => {
    try {
      const count = await playAlbum(album.id);
      if (count === 0) toast.show("No playable tracks in this album.", "err");
    } catch (e) {
      toast.show(`Could not play album: ${e}`, "err");
    }
  };

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
        onBack={onBack}
      />
      <div className="flex-1 overflow-y-auto px-5 pb-10">
        <div className="mx-auto w-full max-w-[1180px]">
          <header className="flex min-h-40 items-end gap-5 border-b border-[var(--color-border)] py-6">
            {infoLoading ? (
              <div className="h-24 w-24 shrink-0 animate-pulse rounded-full bg-[var(--color-surface)] shadow-[var(--shadow-artwork)]" />
            ) : (
              <Artwork
                src={info?.image_url}
                alt={artist.name}
                fallbackLabel={(artist.name[0] ?? "?").toUpperCase()}
                loading="eager"
                className="h-24 w-24 shrink-0 rounded-full text-lg font-semibold shadow-[var(--shadow-artwork)]"
              />
            )}
            <div className="min-w-0 flex-1 pb-0.5">
              <p className="text-[9px] font-semibold uppercase tracking-[0.13em] text-[var(--color-text-muted)]">
                Artist
              </p>
              <h1 className="mt-1 truncate text-[28px] font-semibold leading-none tracking-[-0.035em]">
                {artist.name}
              </h1>
              <p className="mt-2 text-[10px] text-[var(--color-text-muted)]">
                {artist.album_count}{" "}
                {artist.album_count === 1 ? "album" : "albums"} ·{" "}
                {artist.track_count}{" "}
                {artist.track_count === 1 ? "track" : "tracks"}
              </p>
            </div>
            <button
              type="button"
              onClick={handleRadio}
              disabled={radioLoading || albums.length === 0}
              title="Start a Spotify-style radio seeded on this artist"
              aria-label={`Start ${artist.name} radio`}
              className="mb-0.5 inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full bg-[var(--color-text)] px-4 text-[11px] font-semibold text-[var(--color-content)] shadow-sm transition-transform hover:scale-[1.015] active:scale-[0.98] disabled:opacity-35"
            >
              <Icon name="play" size={11} />
              {radioLoading ? "Loading…" : "Start Radio"}
            </button>
          </header>

          {!infoLoading && info ? (
            <section className="border-b border-[var(--color-border)] py-4">
              <p className="max-w-3xl text-[11px] leading-[1.55] text-[var(--color-text-muted)] line-clamp-4">
                {info.summary}
              </p>
              {info.source_url ? (
                <a
                  href={info.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-block text-[10px] font-medium text-[var(--color-accent)] hover:underline"
                >
                  Read on Wikipedia →
                </a>
              ) : null}
            </section>
          ) : null}

          <section className="pt-5">
            <div className="mb-3 flex items-baseline justify-between">
              <h2 className="text-[13px] font-semibold">Albums</h2>
              {!loading && !error ? (
                <span className="text-[10px] tabular-nums text-[var(--color-text-muted)]">
                  {filtered.length}
                </span>
              ) : null}
            </div>
            {loading ? (
              <div className="text-[11px] text-[var(--color-text-muted)]">
                Loading…
              </div>
            ) : error ? (
              <div className="text-[11px] text-[var(--color-danger)]">{error}</div>
            ) : filtered.length === 0 ? (
              <div className="text-[11px] text-[var(--color-text-muted)]">
                {search.trim()
                  ? "No matching albums."
                  : "No albums for this artist."}
              </div>
            ) : (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(132px,1fr))] gap-x-4 gap-y-5">
                {filtered.map((a) => (
                  <AlbumCard
                    key={a.id}
                    album={a}
                    coverUrl={albumCoverUrl(base, a.id)}
                    onClick={() => onOpenAlbum(a)}
                    onDoubleClick={() => void handlePlayAlbum(a)}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
