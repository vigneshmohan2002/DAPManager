import type { MouseEvent } from "react";
import type { Availability, LibraryTrack } from "../../lib/api";
import type {
  SongSortDirection,
  SongSortKey,
} from "../../lib/songList";

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

type Props = {
  ready: boolean;
  loading: boolean;
  error: string | null;
  tracks: readonly LibraryTrack[];
  sort: SongSortKey;
  direction: SongSortDirection;
  currentMbid: string | null;
  isPlaying: boolean;
  onSort: (key: SongSortKey) => void;
  onPlayFrom: (index: number) => void;
  onTogglePlayback: () => void;
  onLikeToggle: (track: LibraryTrack) => void;
  onContextMenu: (
    event: MouseEvent<HTMLTableRowElement>,
    track: LibraryTrack,
  ) => void;
};

export default function SongsTable({
  ready,
  loading,
  error,
  tracks,
  sort,
  direction,
  currentMbid,
  isPlaying,
  onSort,
  onPlayFrom,
  onTogglePlayback,
  onLikeToggle,
  onContextMenu,
}: Props) {
  const arrow = (key: SongSortKey) =>
    sort === key ? (direction === "asc" ? " ↑" : " ↓") : "";

  if (!ready || loading) {
    return (
      <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">
        Loading…
      </div>
    );
  }
  if (error) {
    return (
      <div className="px-6 py-6 text-[var(--color-accent)] text-sm">
        {error}
      </div>
    );
  }
  if (tracks.length === 0) {
    return (
      <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">
        No tracks.
      </div>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead className="sticky top-0 bg-[var(--color-bg)] text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
        <tr>
          <th className="w-10"></th>
          <th className="w-9"></th>
          <th
            onClick={() => onSort("title")}
            className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
          >
            Title{arrow("title")}
          </th>
          <th
            onClick={() => onSort("artist")}
            className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
          >
            Artist{arrow("artist")}
          </th>
          <th
            onClick={() => onSort("album")}
            className="text-left font-medium px-3 py-2 cursor-pointer select-none hover:text-[var(--color-text)]"
          >
            Album{arrow("album")}
          </th>
          <th className="w-36 text-left font-medium px-3 py-2">Status</th>
        </tr>
      </thead>
      <tbody>
        {tracks.map((track, index) => {
          const isCurrent = currentMbid === track.mbid;
          const playable = track.availability !== "unavailable";
          return (
            <tr
              key={track.mbid}
              onClick={() => {
                if (!playable) return;
                if (isCurrent) onTogglePlayback();
                else onPlayFrom(index);
              }}
              onContextMenu={(event) => onContextMenu(event, track)}
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
              <td className="w-9 px-1 py-1.5 text-center">
                <button
                  onClick={(event) => {
                    event.stopPropagation();
                    onLikeToggle(track);
                  }}
                  aria-label={track.is_liked ? "Unlike" : "Like"}
                  aria-pressed={track.is_liked}
                  className={`text-base transition-colors ${
                    track.is_liked
                      ? "text-rose-400 hover:text-rose-300"
                      : "text-[var(--color-text-muted)]/40 hover:text-rose-400"
                  }`}
                >
                  {track.is_liked ? "♥" : "♡"}
                </button>
              </td>
              <td
                className={`px-3 py-1.5 truncate max-w-0 ${isCurrent ? "text-[var(--color-accent)]" : ""}`}
              >
                {track.title}
              </td>
              <td className="px-3 py-1.5 truncate max-w-0 text-[var(--color-text-muted)]">
                {track.artist}
              </td>
              <td className="px-3 py-1.5 truncate max-w-0 text-[var(--color-text-muted)]">
                {track.album ?? ""}
              </td>
              <td className="px-3 py-1.5 whitespace-nowrap">
                <span
                  className={`inline-block rounded-full px-2 py-0.5 text-[11px] ${AVAILABILITY_CLASS[track.availability]}`}
                >
                  {AVAILABILITY_LABEL[track.availability]}
                </span>
                {track.orphan ? (
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
  );
}
