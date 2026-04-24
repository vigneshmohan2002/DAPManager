import { useEffect, useMemo, useState } from "react";
import TopBar from "../components/TopBar";
import {
  fetchAllTracks,
  fetchPlaylists,
  type Availability,
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

const AVAILABILITY_LABEL: Record<Availability, string> = {
  local: "local",
  drive: "drive",
  remote: "catalog-only",
  unavailable: "missing",
};

const AVAILABILITY_CLASS: Record<Availability, string> = {
  local: "bg-emerald-900/40 text-emerald-300",
  drive: "bg-sky-900/40 text-sky-300",
  remote: "bg-amber-900/40 text-amber-300",
  unavailable: "bg-neutral-800 text-neutral-400",
};

export default function SongsScreen({ ready, playlistId }: Props) {
  const [rows, setRows] = useState<LibraryTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("artist");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [playlistName, setPlaylistName] = useState<string | null>(null);

  // Filters: match the web /library page's defaults + semantic.
  //   catalog-only OFF  -> local_only=1 (only rows with a file here)
  //   catalog-only ON   -> include rows whose only source is the
  //                        master (played via the stream proxy).
  //   show-orphans OFF  -> live rows only (default).
  const [catalogOnly, setCatalogOnly] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);

  const { play, current, isPlaying, toggle } = usePlayer();

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await fetchAllTracks({
          playlistId: playlistId ?? undefined,
          localOnly: !catalogOnly,
          includeOrphans: showOrphans,
        });
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
  }, [ready, playlistId, catalogOnly, showOrphans]);

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
    // Strip unavailable rows from the queue so next/prev can't land
    // on a dead end. startIndex is into `visible`; remap it to the
    // same track's position in the filtered queue.
    const queue: Array<LibraryTrack & { albumId: string | null }> = [];
    let adjusted = 0;
    visible.forEach((t, i) => {
      if (t.availability === "unavailable") return;
      if (i === startIndex) adjusted = queue.length;
      queue.push({ ...t, albumId: t.album_id });
    });
    if (queue.length) play(queue, adjusted);
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
      <div className="titlebar-nodrag flex items-center gap-4 px-6 py-2 border-b border-[var(--color-border)] text-sm text-[var(--color-text-muted)]">
        <label className="flex items-center gap-2 cursor-pointer select-none hover:text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={catalogOnly}
            onChange={(e) => setCatalogOnly(e.target.checked)}
          />
          Show catalog-only
        </label>
        <label className="flex items-center gap-2 cursor-pointer select-none hover:text-[var(--color-text)]">
          <input
            type="checkbox"
            checked={showOrphans}
            onChange={(e) => setShowOrphans(e.target.checked)}
          />
          Show orphans
        </label>
      </div>
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
                <th className="w-36 text-left font-medium px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((t, idx) => {
                const isCurrent = current?.mbid === t.mbid;
                const playable = t.availability !== "unavailable";
                return (
                  <tr
                    key={t.mbid}
                    onClick={() => {
                      if (!playable) return;
                      if (isCurrent) toggle();
                      else playFrom(idx);
                    }}
                    className={`border-b border-[var(--color-border)]/40 ${
                      playable
                        ? "cursor-pointer hover:bg-[var(--color-surface)]/60"
                        : "opacity-60 cursor-default"
                    }`}
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
                    <td className="px-3 py-1.5 whitespace-nowrap">
                      <span
                        className={`inline-block rounded-full px-2 py-0.5 text-[11px] ${AVAILABILITY_CLASS[t.availability]}`}
                      >
                        {AVAILABILITY_LABEL[t.availability]}
                      </span>
                      {t.orphan ? (
                        <span className="ml-1 inline-block rounded-full px-2 py-0.5 text-[11px] bg-rose-900/40 text-rose-300">
                          orphan
                        </span>
                      ) : null}
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
