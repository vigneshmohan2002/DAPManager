import { useEffect, useMemo, useState } from "react";
import ContextMenu, {
  type ContextMenuEntry,
} from "../components/ContextMenu";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  addTrackToPlaylist,
  fetchAllTracks,
  fetchPlaylists,
  queueCatalogDownload,
  softDeleteTrack,
  type Availability,
  type LibraryTrack,
  type Playlist,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type SortKey = "title" | "artist" | "album";

type Props = {
  ready: boolean;
  // When set, Songs is scoped to this playlist's membership and the
  // TopBar title reflects the playlist name. null means "all tracks".
  playlistId?: string | null;
  // Bumped by App when any playlist mutation happens; the Add-to-
  // Playlist submenu needs to re-fetch so new playlists show up.
  playlistsVersion: number;
  // SongsScreen itself mutates playlists (add track to playlist,
  // soft-delete a track that affects playlist counts). Call this to
  // tell the rest of the app to refresh.
  onPlaylistsChanged: () => void;
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

export default function SongsScreen({
  ready,
  playlistId,
  playlistsVersion,
  onPlaylistsChanged,
}: Props) {
  const [rows, setRows] = useState<LibraryTrack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("artist");
  const [dir, setDir] = useState<"asc" | "desc">("asc");
  const [playlistName, setPlaylistName] = useState<string | null>(null);
  const [allPlaylists, setAllPlaylists] = useState<Playlist[]>([]);
  const [menu, setMenu] = useState<{
    x: number;
    y: number;
    track: LibraryTrack;
  } | null>(null);
  const [tableVersion, setTableVersion] = useState(0);

  // Filters: match the web /library page's defaults + semantic.
  //   catalog-only OFF  -> local_only=1 (only rows with a file here)
  //   catalog-only ON   -> include rows whose only source is the
  //                        master (played via the stream proxy).
  //   show-orphans OFF  -> live rows only (default).
  const [catalogOnly, setCatalogOnly] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);

  const { play, current, isPlaying, toggle } = usePlayer();
  const toast = useToast();

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
  }, [ready, playlistId, catalogOnly, showOrphans, tableVersion]);

  // Full playlist list drives the "Add to playlist" submenu and the
  // scoped-title lookup. Re-fetched whenever the app signals a
  // playlist mutation happened anywhere.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const lists = await fetchPlaylists();
        if (!cancelled) setAllPlaylists(lists);
      } catch {
        if (!cancelled) setAllPlaylists([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, playlistsVersion]);

  useEffect(() => {
    if (!playlistId) {
      setPlaylistName(null);
      return;
    }
    const match = allPlaylists.find((p) => p.playlist_id === playlistId);
    setPlaylistName(match?.name ?? null);
  }, [playlistId, allPlaylists]);

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

  const reloadTable = () => setTableVersion((v) => v + 1);

  const handleQueueDownload = async (mbid: string) => {
    const result = await queueCatalogDownload([mbid]);
    if (!result.success) {
      toast.show(result.message || "Queue failed", "err");
      return;
    }
    if (result.queued > 0) {
      toast.show("Queued for download.");
    } else if (result.skipped_linked > 0) {
      toast.show("Already has a local file — nothing queued.");
    } else if (result.skipped_queued > 0) {
      toast.show("Already in the download queue.");
    } else if (result.not_found > 0) {
      toast.show("Track not found in catalog.", "err");
    } else {
      toast.show("Nothing queued.");
    }
  };

  const handleSoftDelete = async (track: LibraryTrack) => {
    const label = `${track.artist} — ${track.title}`;
    if (
      !window.confirm(
        `Soft-delete "${label}"? It becomes an orphan — restore from the web /orphans page if needed.`,
      )
    )
      return;
    const result = await softDeleteTrack(track.mbid);
    if (!result.success) {
      toast.show(result.message || "Delete failed", "err");
      return;
    }
    toast.show(`Deleted "${label}".`);
    onPlaylistsChanged();
    reloadTable();
  };

  const handleAddToPlaylist = async (pid: string, track: LibraryTrack) => {
    const pl = allPlaylists.find((p) => p.playlist_id === pid);
    const name = pl?.name ?? pid;
    const result = await addTrackToPlaylist(pid, track.mbid);
    if (!result.success) {
      toast.show(result.message || "Add failed", "err");
      return;
    }
    if (result.added === 0) {
      toast.show(`Already in "${name}".`);
      return;
    }
    toast.show(
      result.missed > 0
        ? `Added to "${name}" (${result.missed} dropped as unknown).`
        : `Added to "${name}".`,
    );
    onPlaylistsChanged();
    // If we're currently scoped to that playlist, refresh the table.
    if (playlistId === pid) reloadTable();
  };

  const menuEntries: ContextMenuEntry[] | null = menu
    ? [
        { kind: "label", text: `${menu.track.artist} — ${menu.track.title}` },
        { kind: "separator" },
        {
          kind: "list",
          heading: "Add to playlist",
          emptyText: "(no playlists — create one from the sidebar)",
          items: allPlaylists.map((p) => ({
            key: p.playlist_id,
            label: p.name,
            onSelect: () => handleAddToPlaylist(p.playlist_id, menu.track),
          })),
        },
        { kind: "separator" },
        {
          kind: "item",
          label: "Queue Download",
          onSelect: () => handleQueueDownload(menu.track.mbid),
        },
        {
          kind: "item",
          label: "Soft-Delete",
          danger: true,
          onSelect: () => handleSoftDelete(menu.track),
        },
      ]
    : null;

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
                    onContextMenu={(e) => {
                      e.preventDefault();
                      setMenu({ x: e.clientX, y: e.clientY, track: t });
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
      {menuEntries && menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          entries={menuEntries}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </div>
  );
}
