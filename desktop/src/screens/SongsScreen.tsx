import { useEffect, useMemo, useState } from "react";
import TopBar from "../components/TopBar";
import {
  fetchAllTracks,
  fetchPlaylists,
  type LibraryTrack,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type SortKey = "title" | "artist" | "album";

type Props = {
  ready: boolean;
  // When set, Songs is scoped to this playlist's membership and the
  // TopBar title reflects the playlist name. null means "all tracks".
  playlistId?: string | null;
};

export default function SongsScreen({ ready, playlistId }: Props) {
  const [rows, setRows] = useState<LibraryTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("artist");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [playlistName, setPlaylistName] = useState<string | null>(null);
  const { play, current, isPlaying, toggle } = usePlayer();

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await fetchAllTracks({ playlistId: playlistId ?? undefined });
        if (!cancelled) setRows(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistId]);

  // Resolve the playlist name for the header. Cheap because
  // fetchPlaylists is already cached sidebar-side; re-fetching here
  // avoids prop-drilling the whole playlist list through App.tsx.
  useEffect(() => {
    if (!ready || !playlistId) {
      setPlaylistName(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const lists = await fetchPlaylists();
        if (cancelled) return;
        const match = lists.find((p) => p.playlist_id === playlistId);
        setPlaylistName(match?.name ?? null);
      } catch {
        if (!cancelled) setPlaylistName(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistId]);

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const filtered = q
      ? rows.filter(
          (r) =>
            r.title.toLowerCase().includes(q) ||
            r.artist.toLowerCase().includes(q) ||
            (r.album ?? "").toLowerCase().includes(q),
        )
      : rows;
    const mul = dir === "asc" ? 1 : -1;
    const pick = (r: LibraryTrack): string =>
      (sort === "title"
        ? r.title
        : sort === "album"
          ? r.album ?? ""
          : r.artist
      ).toLowerCase();
    return [...filtered].sort((a, b) => {
      const av = pick(a);
      const bv = pick(b);
      if (av < bv) return -1 * mul;
      if (av > bv) return 1 * mul;
      return 0;
    });
  }, [rows, search, sort, dir]);

  const playFrom = (startIndex: number) => {
    const queue = visible.map((t) => ({ ...t, albumId: t.album_id }));
    play(queue, startIndex);
  };

  const clickHeader = (key: SortKey) => {
    if (key === sort) setDir(dir === "asc" ? "desc" : "asc");
    else {
      setSort(key);
      setDir("asc");
    }
  };

  const arrow = (key: SortKey) =>
    sort === key ? (dir === "asc" ? " ↑" : " ↓") : "";

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title={playlistId ? playlistName ?? "Playlist" : "Songs"}
        subtitle={
          playlistId
            ? `${visible.length} of ${rows.length} · playlist`
            : `${visible.length} of ${rows.length}`
        }
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto">
        {!ready || loading ? (
          <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">
            Loading…
          </div>
        ) : error ? (
          <div className="px-6 py-6 text-[var(--color-accent)] text-sm">
            {error}
          </div>
        ) : visible.length === 0 ? (
          <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">
            No tracks.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--color-bg)] text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
              <tr>
                <th className="w-10"></th>
                <th
                  onClick={() => clickHeader("title")}
                  className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
                >
                  Title{arrow("title")}
                </th>
                <th
                  onClick={() => clickHeader("artist")}
                  className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
                >
                  Artist{arrow("artist")}
                </th>
                <th
                  onClick={() => clickHeader("album")}
                  className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
                >
                  Album{arrow("album")}
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((t, idx) => {
                const isCurrent = current?.mbid === t.mbid;
                return (
                  <tr
                    key={t.mbid}
                    onClick={() => {
                      if (isCurrent) toggle();
                      else playFrom(idx);
                    }}
                    className="border-b border-[var(--color-border)]/40 cursor-pointer hover:bg-[var(--color-surface)]/60"
                  >
                    <td className="w-10 px-3 py-1.5 text-center text-[var(--color-text-muted)]">
                      {isCurrent && isPlaying ? (
                        <span className="text-[var(--color-accent)]">♪</span>
                      ) : null}
                    </td>
                    <td
                      className={`px-3 py-1.5 truncate max-w-0 ${isCurrent ? "text-[var(--color-accent)]" : ""}`}
                    >
                      {t.title}
                    </td>
                    <td className="px-3 py-1.5 truncate max-w-0 text-[var(--color-text-muted)]">
                      {t.artist}
                    </td>
                    <td className="px-3 py-1.5 truncate max-w-0 text-[var(--color-text-muted)]">
                      {t.album ?? ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
