import { useEffect, useState } from "react";
import Artwork from "../components/Artwork";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbumTracks,
  setTrackLiked,
  type Album,
  type Track,
} from "../lib/api";
import { albumDisplayArtist } from "../lib/album";
import { usePlayer } from "../player/PlayerContext";

type Props = {
  album: Album;
  onBack: () => void;
  // First like in a fresh library auto-creates the Liked Songs
  // playlist on the server — sidebar needs the nudge to re-fetch.
  // Optional so the search-overlay open path (which doesn't carry
  // playlists context yet) can route here without breaking.
  onPlaylistsChanged?: () => void;
};

export default function AlbumDetailScreen({
  album,
  onBack,
  onPlaylistsChanged,
}: Props) {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [base, setBase] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const {
    play,
    current,
    isPlaying,
    toggle,
    shuffle,
    toggleShuffle,
    setTrackLikedInQueue,
  } = usePlayer();
  const toast = useToast();
  const displayArtist = albumDisplayArtist(album);

  useEffect(() => {
    let cancelled = false;
    // Search can replace one open album with another without unmounting this
    // screen. Clear the previous album's rows/count before loading the next.
    setLoading(true);
    setError(null);
    setTracks([]);
    setBase("");
    setSearch("");
    (async () => {
      try {
        const [url, data] = await Promise.all([
          backendUrl(),
          fetchAlbumTracks(album.id),
        ]);
        if (cancelled) return;
        setBase(url);
        setTracks(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [album.id]);

  const filtered = search
    ? tracks.filter((t) =>
        t.title.toLowerCase().includes(search.trim().toLowerCase()),
      )
    : tracks;

  const trackSummary = loading
    ? "Loading tracks…"
    : error
      ? "Track count unavailable"
      : `${tracks.length} ${tracks.length === 1 ? "track" : "tracks"}`;

  const playFrom = (startIndex: number) => {
    const withAlbum = tracks.map((t) => ({ ...t, albumId: album.id }));
    play(withAlbum, startIndex);
  };

  const shuffleAlbum = () => {
    if (!shuffle) toggleShuffle();
    playFrom(0);
  };

  const handleLikeToggle = async (track: Track) => {
    const wasLiked = Boolean(track.is_liked);
    const next = !wasLiked;
    // Optimistic flip on local rows + on any in-queue copy so both
    // surfaces fill the heart immediately.
    setTracks((ts) =>
      ts.map((r) => (r.mbid === track.mbid ? { ...r, is_liked: next } : r)),
    );
    setTrackLikedInQueue(track.mbid, next);
    const result = await setTrackLiked(track.mbid, next);
    if (!result.success) {
      setTracks((ts) =>
        ts.map((r) => (r.mbid === track.mbid ? { ...r, is_liked: wasLiked } : r)),
      );
      setTrackLikedInQueue(track.mbid, wasLiked);
      toast.show(result.message ?? "Could not save like", "err");
      return;
    }
    if (next) onPlaylistsChanged?.();
  };

  const coverUrl = base ? albumCoverUrl(base, album.id) : null;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title={album.title}
        subtitle={displayArtist}
        search={search}
        onSearch={setSearch}
        onBack={onBack}
      />
      <div className="relative flex-1 overflow-y-auto">
        {coverUrl ? (
          <img
            key={coverUrl}
            src={coverUrl}
            alt=""
            aria-hidden="true"
            className="pointer-events-none absolute -right-16 -top-28 h-96 w-96 scale-125 object-cover opacity-[0.07] blur-3xl"
          />
        ) : null}
        <div className="relative px-6 py-7">
          <div className="mb-8 flex gap-7">
            <Artwork
              src={coverUrl}
              alt={`${album.title} cover`}
              loading="eager"
              className="h-52 w-52 shrink-0 rounded-[6px] shadow-[var(--shadow-artwork)]"
            />
            <div className="flex min-w-0 flex-col justify-end pb-1">
              <div className="text-[10px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
                {displayArtist}
              </div>
              <h2 className="mt-1 max-w-2xl truncate text-[30px] font-semibold leading-tight tracking-[-0.025em]">
                {album.title}
              </h2>
              <div className="mt-2 text-[11px] text-[var(--color-text-muted)]">
                {trackSummary}
              </div>
              <div className="mt-6 flex gap-2.5">
                <button
                  onClick={() => playFrom(0)}
                  disabled={tracks.length === 0}
                  className="doppler-control flex h-9 min-w-28 items-center justify-center gap-2 rounded-full bg-[var(--color-accent-soft)] px-5 text-[12px] font-medium text-[var(--color-accent)] disabled:opacity-40"
                >
                  <Icon name="play" size={13} />
                  Play
                </button>
                <button
                  onClick={shuffleAlbum}
                  disabled={tracks.length === 0}
                  className="doppler-control flex h-9 min-w-28 items-center justify-center gap-2 rounded-full bg-[var(--color-accent-soft)] px-5 text-[12px] font-medium text-[var(--color-accent)] disabled:opacity-40"
                >
                  <Icon name="shuffle" size={14} />
                  Shuffle
                </button>
              </div>
            </div>
          </div>

          {loading ? (
            <div className="text-[11px] text-[var(--color-text-muted)]">
              Loading…
            </div>
          ) : error ? (
            <div className="text-[11px] text-[var(--color-danger)]">{error}</div>
          ) : filtered.length === 0 ? (
            <div className="text-[11px] text-[var(--color-text-muted)]">
              No tracks available.
            </div>
          ) : (
            <ol className="border-y border-[var(--color-border)]">
              {filtered.map((t, idx) => {
                const absIdx = tracks.indexOf(t);
                const isCurrent = current?.mbid === t.mbid;
                const showTrackArtist =
                  t.artist.trim().toLocaleLowerCase() !==
                  displayArtist.trim().toLocaleLowerCase();
                return (
                  <li
                    key={t.mbid}
                    className={`group flex min-h-10 cursor-pointer items-center gap-3 border-b border-[var(--color-border)] px-3 last:border-b-0 ${
                      isCurrent
                        ? "bg-[var(--color-accent-soft)]"
                        : "hover:bg-[var(--color-surface)]/45"
                    }`}
                    onClick={() => {
                      if (isCurrent) toggle();
                      else playFrom(absIdx);
                    }}
                  >
                    <div className="w-6 text-right text-[10px] tabular-nums text-[var(--color-text-muted)]">
                      {isCurrent && isPlaying ? (
                        <Icon
                          name="songs"
                          size={12}
                          className="ml-auto text-[var(--color-accent)]"
                        />
                      ) : (
                        t.track_number ?? idx + 1
                      )}
                    </div>
                    <div className="min-w-0 flex-1 py-1.5">
                      <div
                        className={`truncate text-[11px] ${
                          isCurrent
                            ? "font-medium text-[var(--color-accent)]"
                            : ""
                        }`}
                      >
                        {t.title}
                      </div>
                      {showTrackArtist ? (
                        <div className="truncate text-[9px] text-[var(--color-text-muted)]">
                          {t.artist}
                        </div>
                      ) : null}
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleLikeToggle(t);
                      }}
                      aria-label={t.is_liked ? "Unlike" : "Like"}
                      aria-pressed={Boolean(t.is_liked)}
                      className={`doppler-control grid h-7 w-7 place-items-center rounded ${
                        t.is_liked
                          ? "text-[var(--color-liked)]"
                          : "opacity-0 group-hover:opacity-100"
                      }`}
                    >
                      <Icon
                        name="heart"
                        size={13}
                        className={t.is_liked ? "fill-current" : ""}
                      />
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
