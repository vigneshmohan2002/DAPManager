import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbumTracks,
  type Album,
  type Track,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type Props = {
  album: Album;
  onBack: () => void;
};

export default function AlbumDetailScreen({ album, onBack }: Props) {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [base, setBase] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const { play, current, isPlaying, toggle } = usePlayer();

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
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
