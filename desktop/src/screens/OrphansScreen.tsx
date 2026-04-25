import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  deleteTrackFile,
  fetchOrphanPlaylists,
  fetchOrphanTracks,
  purgePlaylist,
  purgeTrack,
  restorePlaylist,
  restoreTrack,
  type OrphanPlaylist,
  type OrphanTrack,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = {
  ready: boolean;
  onPlaylistsChanged: () => void;
};

type Tab = "tracks" | "playlists";

export default function OrphansScreen({ ready, onPlaylistsChanged }: Props) {
  const [tab, setTab] = useState<Tab>("tracks");
  const [tracks, setTracks] = useState<OrphanTrack[] | null>(null);
  const [playlists, setPlaylists] = useState<OrphanPlaylist[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const loadTracks = async () => {
    try {
      setTracks(await fetchOrphanTracks());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };
  const loadPlaylists = async () => {
    try {
      setPlaylists(await fetchOrphanPlaylists());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    if (!ready) return;
    loadTracks();
    loadPlaylists();
  }, [ready]);

  const onTrackAction = async (
    action: "restore" | "purge" | "delete-file",
    row: OrphanTrack,
  ) => {
    if (action === "purge") {
      if (
        !window.confirm(
          "Permanently remove this track row? This cannot be undone.",
        )
      )
        return;
    } else if (action === "delete-file") {
      if (
        !window.confirm(
          "Delete the file on disk? The orphan row stays until you Purge it.",
        )
      )
        return;
    }
    const fn =
      action === "restore"
        ? restoreTrack
        : action === "purge"
        ? purgeTrack
        : deleteTrackFile;
    const result = await fn(row.mbid);
    if (!result.success) {
      toast.show(result.message || `${action} failed`, "err");
      return;
    }
    toast.show(
      action === "restore"
        ? "Restored."
        : action === "purge"
        ? "Purged."
        : "File deleted.",
    );
    loadTracks();
  };

  const onPlaylistAction = async (
    action: "restore" | "purge",
    row: OrphanPlaylist,
  ) => {
    if (action === "purge") {
      const extra =
        row.track_count > 0
          ? ` (removes ${row.track_count} membership row${
              row.track_count === 1 ? "" : "s"
            })`
          : "";
      if (
        !window.confirm(
          `Permanently remove "${row.name}"${extra}? This cannot be undone.`,
        )
      )
        return;
    }
    const fn = action === "restore" ? restorePlaylist : purgePlaylist;
    const result = await fn(row.playlist_id);
    if (!result.success) {
      toast.show(result.message || `${action} failed`, "err");
      return;
    }
    toast.show(action === "restore" ? "Restored." : "Purged.");
    loadPlaylists();
    // Restoring brings the playlist back into the live sidebar list;
    // purging is no-op for the sidebar (it was already hidden) but
    // bumping covers both cases cheaply.
    onPlaylistsChanged();
  };

  const trackCount = tracks?.length ?? 0;
  const playlistCount = playlists?.length ?? 0;
  const subtitle =
    error !== null
      ? "Failed to load"
      : tracks === null || playlists === null
      ? "Loading…"
      : trackCount + playlistCount === 0
      ? "No orphans"
      : `${trackCount} track${trackCount === 1 ? "" : "s"}, ${playlistCount} playlist${
          playlistCount === 1 ? "" : "s"
        }`;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Orphans" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="flex border-b border-[var(--color-border)] mb-4">
          <TabButton
            label="Tracks"
            count={trackCount}
            active={tab === "tracks"}
            onClick={() => setTab("tracks")}
          />
          <TabButton
            label="Playlists"
            count={playlistCount}
            active={tab === "playlists"}
            onClick={() => setTab("playlists")}
          />
        </div>

        {error ? (
          <div className="text-sm text-[var(--color-accent)]">{error}</div>
        ) : tab === "tracks" ? (
          <TracksTable rows={tracks} onAction={onTrackAction} />
        ) : (
          <PlaylistsTable rows={playlists} onAction={onPlaylistAction} />
        )}
      </div>
    </div>
  );
}

function TabButton({
  label,
  count,
  active,
  onClick,
}: {
  label: string;
  count: number;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm border-b-2 -mb-px transition-colors ${
        active
          ? "border-[var(--color-accent)] text-[var(--color-text)]"
          : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
      }`}
    >
      {label}
      <span className="ml-2 text-xs text-[var(--color-text-muted)]">
        {count}
      </span>
    </button>
  );
}

function TracksTable({
  rows,
  onAction,
}: {
  rows: OrphanTrack[] | null;
  onAction: (
    action: "restore" | "purge" | "delete-file",
    row: OrphanTrack,
  ) => void;
}) {
  if (rows === null) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Loading…
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        No orphan tracks.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
            <th className="px-3 py-2">Artist</th>
            <th className="px-3 py-2">Title</th>
            <th className="px-3 py-2">Album</th>
            <th className="px-3 py-2 whitespace-nowrap">Deleted</th>
            <th className="px-3 py-2">On disk</th>
            <th className="px-3 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const hasFile = !!(r.local_path && r.local_path.trim());
            return (
              <tr
                key={r.mbid}
                className="border-b border-[var(--color-border)]/60 hover:bg-[var(--color-surface)]/40"
              >
                <td className="px-3 py-2 truncate max-w-[200px]">
                  {r.artist || ""}
                </td>
                <td className="px-3 py-2 truncate max-w-[260px]">
                  {r.title || ""}
                </td>
                <td className="px-3 py-2 truncate max-w-[200px] text-[var(--color-text-muted)]">
                  {r.album || ""}
                </td>
                <td className="px-3 py-2 whitespace-nowrap text-[var(--color-text-muted)]">
                  {relativeTime(r.deleted_at)}
                </td>
                <td
                  className="px-3 py-2 text-[var(--color-text-muted)]"
                  title={r.local_path ?? ""}
                >
                  {hasFile ? "yes" : "—"}
                </td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <RowButton onClick={() => onAction("restore", r)}>
                    Restore
                  </RowButton>
                  {hasFile ? (
                    <RowButton danger onClick={() => onAction("delete-file", r)}>
                      Delete file
                    </RowButton>
                  ) : null}
                  <RowButton danger onClick={() => onAction("purge", r)}>
                    Purge
                  </RowButton>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PlaylistsTable({
  rows,
  onAction,
}: {
  rows: OrphanPlaylist[] | null;
  onAction: (action: "restore" | "purge", row: OrphanPlaylist) => void;
}) {
  if (rows === null) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Loading…
      </div>
    );
  }
  if (rows.length === 0) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        No orphan playlists.
      </div>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
            <th className="px-3 py-2">Name</th>
            <th className="px-3 py-2">Tracks</th>
            <th className="px-3 py-2 whitespace-nowrap">Deleted</th>
            <th className="px-3 py-2 text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.playlist_id}
              className="border-b border-[var(--color-border)]/60 hover:bg-[var(--color-surface)]/40"
            >
              <td className="px-3 py-2 truncate max-w-[280px]">{r.name}</td>
              <td className="px-3 py-2 text-[var(--color-text-muted)]">
                {r.track_count}
              </td>
              <td className="px-3 py-2 whitespace-nowrap text-[var(--color-text-muted)]">
                {relativeTime(r.deleted_at)}
              </td>
              <td className="px-3 py-2 text-right whitespace-nowrap">
                <RowButton onClick={() => onAction("restore", r)}>
                  Restore
                </RowButton>
                <RowButton danger onClick={() => onAction("purge", r)}>
                  Purge
                </RowButton>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RowButton({
  children,
  onClick,
  danger,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`ml-2 px-2.5 py-1 rounded-md text-xs border transition-colors ${
        danger
          ? "border-[var(--color-accent)]/40 text-[var(--color-accent)] hover:bg-[var(--color-accent)]/10"
          : "border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface)]"
      }`}
    >
      {children}
    </button>
  );
}
