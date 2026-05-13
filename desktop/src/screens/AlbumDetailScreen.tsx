import { useEffect, useState } from "react";
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
  const { play, current, isPlaying, toggle, setTrackLikedInQueue } =
    usePlayer();
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
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

  const playFrom = (startIndex: number) => {
    const withAlbum = tracks.map((t) => ({ ...t, albumId: album.id }));
    play(withAlbum, startIndex);
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

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title={album.title} subtitle={album.artist} search={search} onSearch={setSearch} />
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="flex gap-6 mb-8">
          <div className="w-48 h-48 rounded-md overflow-hidden bg-[var(--color-surface)] shadow-lg shrink-0">
            {base ? (
              <img
                src={albumCoverUrl(base, album.id)}
                alt=""
                className="w-full h-full object-cover"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
              />
            ) : null}
          </div>
          <div className="flex flex-col justify-end min-w-0">
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
              Album
            </div>
            <h2 className="text-3xl font-bold truncate">{album.title}</h2>
            <div className="text-sm text-[var(--color-text-muted)] mt-1">
              {album.artist} · {album.track_count} tracks
            </div>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => playFrom(0)}
                disabled={tracks.length === 0}
                className="px-5 py-2 rounded-full bg-[var(--color-accent)] text-white text-sm font-medium disabled:opacity-40"
              >
                ▶ Play
              </button>
              <button
                onClick={onBack}
                className="px-4 py-2 rounded-full bg-[var(--color-surface)] text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                ← Back
              </button>
            </div>
          </div>
        </div>

        {loading ? (
          <div className="text-[var(--color-text-muted)] text-sm">Loading…</div>
        ) : error ? (
          <div className="text-[var(--color-accent)] text-sm">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="text-[var(--color-text-muted)] text-sm">
            No tracks available.
          </div>
        ) : (
          <ol className="divide-y divide-[var(--color-border)]">
            {filtered.map((t, idx) => {
              const absIdx = tracks.indexOf(t);
              const isCurrent = current?.mbid === t.mbid;
              return (
                <li
                  key={t.mbid}
                  className="flex items-center gap-4 py-2 px-2 rounded hover:bg-[var(--color-surface)]/60 group cursor-pointer"
                  onClick={() => {
                    if (isCurrent) toggle();
                    else playFrom(absIdx);
                  }}
                >
                  <div className="w-8 text-center text-[var(--color-text-muted)] text-sm tabular-nums">
                    {isCurrent && isPlaying ? (
                      <span className="text-[var(--color-accent)]">♪</span>
                    ) : (
                      t.track_number ?? idx + 1
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div
                      className={`text-sm truncate ${isCurrent ? "text-[var(--color-accent)]" : ""}`}
                    >
                      {t.title}
                    </div>
                    <div className="text-xs text-[var(--color-text-muted)] truncate">
                      {t.artist}
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      // Heart must not also fire the row's play handler.
                      e.stopPropagation();
                      handleLikeToggle(t);
                    }}
                    aria-label={t.is_liked ? "Unlike" : "Like"}
                    aria-pressed={Boolean(t.is_liked)}
                    className={`text-base px-2 transition-colors ${
                      t.is_liked
                        ? "text-rose-400 hover:text-rose-300"
                        : "text-[var(--color-text-muted)]/30 hover:text-rose-400 opacity-0 group-hover:opacity-100"
                    }`}
                  >
                    {t.is_liked ? "♥" : "♡"}
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
