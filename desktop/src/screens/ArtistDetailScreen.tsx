import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import AlbumCard from "../components/AlbumCard";
import Artwork from "../components/Artwork";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbumTracks,
  fetchAlbums,
  fetchArtistInfo,
  fetchArtistRadio,
  type Album,
  type Artist,
  type ArtistInfo,
  type Track,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type Props = {
  artist: Artist;
  onBack: () => void;
  onOpenAlbum: (album: Album) => void;
  embedded?: boolean;
  preloadedAlbums?: readonly Album[];
  preloadedBaseUrl?: string;
  preloadedLoading?: boolean;
  preloadedError?: string | null;
};

const ARTIST_TRACK_FETCH_CONCURRENCY = 4;

type ArtistQueueTrack = Track & { albumId: string };

type ArtistTrackLoadResult = {
  queue: ArtistQueueTrack[];
  failedAlbums: number;
};

async function loadArtistTracks(
  albums: readonly Album[],
  requestIsCurrent: () => boolean,
): Promise<ArtistTrackLoadResult> {
  const results: Array<ArtistQueueTrack[] | null | undefined> = new Array(
    albums.length,
  );
  let nextIndex = 0;

  const worker = async () => {
    while (requestIsCurrent()) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= albums.length) return;
      const album = albums[index];
      try {
        const tracks = await fetchAlbumTracks(album.id);
        if (!requestIsCurrent()) return;
        results[index] = tracks.map((track) => ({
          ...track,
          albumId: album.id,
        }));
      } catch {
        if (!requestIsCurrent()) return;
        results[index] = null;
      }
    }
  };

  const workerCount = Math.min(
    ARTIST_TRACK_FETCH_CONCURRENCY,
    albums.length,
  );
  await Promise.all(Array.from({ length: workerCount }, worker));

  return {
    queue: results.flatMap((tracks) => tracks ?? []),
    failedAlbums: results.filter((tracks) => tracks === null).length,
  };
}

export default function ArtistDetailScreen({
  artist,
  onBack,
  onOpenAlbum,
  embedded = false,
  preloadedAlbums,
  preloadedBaseUrl,
  preloadedLoading,
  preloadedError,
}: Props) {
  const [loadedAlbums, setLoadedAlbums] = useState<Album[]>([]);
  const [loadedBase, setLoadedBase] = useState("");
  const [loadedAlbumsLoading, setLoadedAlbumsLoading] = useState(true);
  const [loadedAlbumsError, setLoadedAlbumsError] = useState<string | null>(
    null,
  );
  const [search, setSearch] = useState("");
  const [info, setInfo] = useState<ArtistInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(true);
  const [radioLoading, setRadioLoading] = useState(false);
  const [artistPlaybackLoading, setArtistPlaybackLoading] = useState(false);
  const { play, playAlbum, shuffle, toggleShuffle } = usePlayer();
  const toast = useToast();
  const activeArtistRef = useRef(artist.name);
  const artistRequestEpochRef = useRef(0);
  const shuffleRef = useRef(shuffle);
  shuffleRef.current = shuffle;

  const hasPreloadedAlbums = preloadedAlbums !== undefined;
  const albums = hasPreloadedAlbums ? preloadedAlbums : loadedAlbums;
  const base = hasPreloadedAlbums
    ? (preloadedBaseUrl ?? "")
    : loadedBase;
  const loading = hasPreloadedAlbums
    ? Boolean(preloadedLoading)
    : loadedAlbumsLoading;
  const error = hasPreloadedAlbums
    ? (preloadedError ?? null)
    : loadedAlbumsError;

  useLayoutEffect(() => {
    activeArtistRef.current = artist.name;
    artistRequestEpochRef.current += 1;
    setRadioLoading(false);
    setArtistPlaybackLoading(false);
    return () => {
      activeArtistRef.current = "";
      artistRequestEpochRef.current += 1;
    };
  }, [artist.name]);

  const handlePlayAlbum = async (album: Album) => {
    try {
      const count = await playAlbum(album.id);
      if (count === 0) toast.show("No playable tracks in this album.", "err");
    } catch (e) {
      toast.show(`Could not play album: ${e}`, "err");
    }
  };

  const handleRadio = async () => {
    const requestArtist = artist.name;
    const requestEpoch = artistRequestEpochRef.current;
    const requestIsCurrent = () =>
      activeArtistRef.current === requestArtist &&
      artistRequestEpochRef.current === requestEpoch;
    setRadioLoading(true);
    try {
      const result = await fetchArtistRadio(requestArtist);
      if (!requestIsCurrent()) return;
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
            ? `Radio: ${requestArtist} (no matching artists yet)`
            : `Radio: ${requestArtist} (run a tag backfill in Settings for richer mixes)`,
        );
      } else {
        toast.show(
          `Radio: ${requestArtist} · ${result.related_count} related (${result.top_tag})`,
        );
      }
    } catch (e) {
      if (requestIsCurrent()) toast.show(`Radio failed: ${e}`, "err");
    } finally {
      if (requestIsCurrent()) setRadioLoading(false);
    }
  };

  const handlePlayArtist = async (shouldShuffle: boolean) => {
    if (albums.length === 0 || artistPlaybackLoading) return;
    const requestArtist = artist.name;
    const requestEpoch = artistRequestEpochRef.current;
    const requestIsCurrent = () =>
      activeArtistRef.current === requestArtist &&
      artistRequestEpochRef.current === requestEpoch;
    const requestedAlbums = [...albums];
    setArtistPlaybackLoading(true);
    try {
      const { queue, failedAlbums } = await loadArtistTracks(
        requestedAlbums,
        requestIsCurrent,
      );
      if (!requestIsCurrent()) return;
      if (queue.length === 0) {
        if (failedAlbums > 0) {
          toast.show(
            `Could not play artist: ${failedAlbums} ${
              failedAlbums === 1 ? "album failed" : "albums failed"
            } to load.`,
            "err",
          );
        } else {
          toast.show("No playable tracks found for this artist.", "err");
        }
        return;
      }
      if (shouldShuffle !== shuffleRef.current) toggleShuffle();
      play(queue, 0);
      if (failedAlbums > 0) {
        toast.show(
          `Playing available tracks; skipped ${failedAlbums} ${
            failedAlbums === 1 ? "album" : "albums"
          } that could not be loaded.`,
        );
      }
    } catch (error) {
      if (requestIsCurrent()) {
        toast.show(`Could not play artist: ${String(error)}`, "err");
      }
    } finally {
      if (requestIsCurrent()) setArtistPlaybackLoading(false);
    }
  };

  useEffect(() => {
    setSearch("");
  }, [artist.name]);

  useEffect(() => {
    if (hasPreloadedAlbums) return;
    let cancelled = false;
    setLoadedAlbumsLoading(true);
    setLoadedAlbumsError(null);
    setLoadedAlbums([]);
    (async () => {
      try {
        const [url, all] = await Promise.all([backendUrl(), fetchAlbums()]);
        if (cancelled) return;
        setLoadedBase(url);
        setLoadedAlbums(all.filter((a) => a.artist === artist.name));
      } catch (e) {
        if (!cancelled) setLoadedAlbumsError(String(e));
      } finally {
        if (!cancelled) setLoadedAlbumsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [artist.name, hasPreloadedAlbums]);

  useEffect(() => {
    let cancelled = false;
    if (embedded) {
      setInfo(null);
      setInfoLoading(false);
      return () => {
        cancelled = true;
      };
    }
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
  }, [artist.name, embedded]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return albums;
    return albums.filter((a) => a.title.toLowerCase().includes(q));
  }, [albums, search]);

  if (embedded) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <header className="flex h-[62px] shrink-0 items-center gap-4 border-b border-[var(--color-border)] px-5">
          <h1 className="min-w-0 flex-1 truncate text-[27px] font-semibold tracking-[-0.035em]">
            {artist.name}
          </h1>
          <button
            type="button"
            onClick={() => void handlePlayArtist(false)}
            disabled={loading || albums.length === 0 || artistPlaybackLoading}
            aria-label={`Play all ${artist.name}`}
            title="Play all"
            className="doppler-control grid h-7 min-w-11 place-items-center rounded-full bg-[var(--color-surface)] px-3 disabled:opacity-35"
          >
            <Icon name="play" size={14} />
          </button>
          <button
            type="button"
            onClick={() => void handlePlayArtist(true)}
            disabled={loading || albums.length === 0 || artistPlaybackLoading}
            aria-label={`Shuffle all ${artist.name}`}
            title="Shuffle all"
            className="doppler-control grid h-7 min-w-11 place-items-center rounded-full bg-[var(--color-surface)] px-3 disabled:opacity-35"
          >
            <Icon name="shuffle" size={15} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-10 pt-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-[13px] font-semibold">Albums</h2>
            <div className="flex items-center gap-3">
              {!loading && !error ? (
                <span className="text-[10px] tabular-nums text-[var(--color-text-muted)]">
                  {filtered.length}
                </span>
              ) : null}
              <label className="relative block">
                <Icon
                  name="search"
                  size={12}
                  className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
                />
                <input
                  type="search"
                  aria-label={`Search ${artist.name} albums`}
                  placeholder="Filter albums"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="h-7 w-40 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] py-1 pl-7 pr-2 text-[10px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
                />
              </label>
              <button
                type="button"
                onClick={handleRadio}
                disabled={radioLoading || albums.length === 0}
                aria-label={`Start ${artist.name} radio`}
                className="doppler-control h-7 rounded-full px-3 text-[10px] font-medium disabled:opacity-35"
              >
                {radioLoading ? "Loading…" : "Radio"}
              </button>
            </div>
          </div>
          {loading ? (
            <div role="status" className="text-[11px] text-[var(--color-text-muted)]">
              Loading…
            </div>
          ) : error ? (
            <div role="alert" className="text-[11px] text-[var(--color-danger)]">
              {error}
            </div>
          ) : filtered.length === 0 ? (
            <div className="grid min-h-40 place-items-center text-center text-[11px] text-[var(--color-text-muted)]">
              {search.trim()
                ? "No matching albums."
                : "No albums for this artist."}
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(156px,194px))] gap-x-5 gap-y-6">
              {filtered.map((album) => (
                <AlbumCard
                  key={album.id}
                  album={album}
                  coverUrl={albumCoverUrl(base, album.id)}
                  onClick={() => onOpenAlbum(album)}
                  onDoubleClick={() => void handlePlayAlbum(album)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  const detail = (
    <div className="relative min-h-0 flex-1 overflow-y-auto">
        {!infoLoading && info?.image_url ? (
          <img
            src={info.image_url}
            alt=""
            aria-hidden="true"
            className="pointer-events-none absolute -right-20 -top-32 h-[28rem] w-[28rem] scale-125 rounded-full object-cover opacity-[0.055] blur-3xl"
          />
        ) : null}
        <div className="relative mx-auto w-full max-w-[1180px] px-5 pb-10">
          <header className="flex min-h-44 items-end gap-6 border-b border-[var(--color-border)] py-7">
            {infoLoading ? (
              <div className="h-28 w-28 shrink-0 animate-pulse rounded-full bg-[var(--color-surface)] shadow-[var(--shadow-artwork)]" />
            ) : (
              <Artwork
                src={info?.image_url}
                alt={artist.name}
                fallbackLabel={(artist.name[0] ?? "?").toUpperCase()}
                loading="eager"
                className="h-28 w-28 shrink-0 rounded-full text-lg font-semibold shadow-[var(--shadow-artwork)]"
              />
            )}
            <div className="min-w-0 flex-1 pb-0.5">
              <p className="text-[9px] font-semibold uppercase tracking-[0.13em] text-[var(--color-text-muted)]">
                Artist
              </p>
              <h1 className="mt-1 truncate text-[32px] font-semibold leading-none tracking-[-0.04em]">
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
              className="doppler-control mb-0.5 inline-flex h-9 shrink-0 items-center gap-2 rounded-full bg-[var(--color-accent-soft)] px-5 text-[11px] font-medium text-[var(--color-accent)] disabled:opacity-35"
            >
              <Icon name="play" size={12} />
              {radioLoading ? "Loading…" : "Start Radio"}
            </button>
          </header>

          <div
            className={`grid gap-8 pt-6 ${
              !infoLoading && info
                ? "lg:grid-cols-[minmax(0,1fr)_240px]"
                : ""
            }`}
          >
            <section className="min-w-0">
              <div className="mb-3 flex items-baseline justify-between">
                <h2 className="text-[13px] font-semibold">Albums</h2>
                <div className="flex items-center gap-3">
                  {!loading && !error ? (
                    <span className="text-[10px] tabular-nums text-[var(--color-text-muted)]">
                      {filtered.length}
                    </span>
                  ) : null}
                  {embedded ? (
                    <label className="relative block">
                      <Icon
                        name="search"
                        size={12}
                        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)]"
                      />
                      <input
                        type="search"
                        aria-label={`Search ${artist.name} albums`}
                        placeholder="Filter albums"
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        className="h-7 w-40 rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] py-1 pl-7 pr-2 text-[10px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]"
                      />
                    </label>
                  ) : null}
                </div>
              </div>
              {loading ? (
                <div
                  role="status"
                  className="text-[11px] text-[var(--color-text-muted)]"
                >
                  Loading…
                </div>
              ) : error ? (
                <div
                  role="alert"
                  className="text-[11px] text-[var(--color-danger)]"
                >
                  {error}
                </div>
              ) : filtered.length === 0 ? (
                <div className="grid min-h-40 place-items-center text-center text-[11px] text-[var(--color-text-muted)]">
                  <div>
                    <Icon
                      name="albums"
                      size={26}
                      className="mx-auto mb-2 opacity-40"
                    />
                    {search.trim()
                      ? "No matching albums."
                      : "No albums for this artist."}
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(136px,1fr))] gap-x-4 gap-y-5">
                  {filtered.map((album) => (
                    <AlbumCard
                      key={album.id}
                      album={album}
                      coverUrl={albumCoverUrl(base, album.id)}
                      onClick={() => onOpenAlbum(album)}
                      onDoubleClick={() => void handlePlayAlbum(album)}
                    />
                  ))}
                </div>
              )}
            </section>

            {!infoLoading && info ? (
              <aside
                aria-label={`About ${artist.name}`}
                className="h-fit rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] p-4 lg:sticky lg:top-5"
              >
                <h2 className="text-[11px] font-semibold">About</h2>
                <p className="mt-2 text-[10px] leading-[1.55] text-[var(--color-text-muted)]">
                  {info.summary}
                </p>
                {info.source_url ? (
                  <a
                    href={info.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 inline-block text-[10px] font-medium text-[var(--color-accent)] hover:underline"
                  >
                    Read on Wikipedia →
                  </a>
                ) : null}
              </aside>
            ) : null}
          </div>
        </div>
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <TopBar
        title={artist.name}
        subtitle={`${artist.album_count} albums · ${artist.track_count} tracks`}
        search={search}
        onSearch={setSearch}
        onBack={onBack}
      />
      {detail}
    </div>
  );
}
