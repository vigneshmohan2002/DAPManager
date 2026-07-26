import type { KeyboardEvent, MouseEvent, ReactNode } from "react";
import Icon from "../../components/Icon";
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
  local: "bg-emerald-500",
  drive: "bg-sky-500",
  remote: "bg-amber-500",
  unavailable: "bg-[var(--color-text-muted)]/45",
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
    sort === key ? (direction === "asc" ? "↑" : "↓") : null;

  if (!ready || loading) {
    return (
      <TableState>
        <span role="status">Loading…</span>
      </TableState>
    );
  }
  if (error) {
    return (
      <TableState>
        <span role="alert" className="text-[var(--color-danger)]">
          {error}
        </span>
      </TableState>
    );
  }
  if (tracks.length === 0) {
    return (
      <TableState>
        <Icon name="songs" size={26} className="mb-2 opacity-40" />
        <span>No tracks.</span>
      </TableState>
    );
  }

  return (
    <table className="mt-2 w-full table-fixed text-[11px]">
      <thead className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-content)]/95 text-[9px] uppercase tracking-[0.08em] text-[var(--color-text-muted)] backdrop-blur-xl">
        <tr>
          <th className="w-8" aria-label="Playback" />
          <th className="w-9" aria-label="Liked" />
          <SortableHeader
            column="title"
            label="Title"
            sort={sort}
            direction={direction}
            arrow={arrow("title")}
            onSort={onSort}
          />
          <SortableHeader
            column="artist"
            label="Artist"
            sort={sort}
            direction={direction}
            arrow={arrow("artist")}
            onSort={onSort}
          />
          <SortableHeader
            column="album"
            label="Album"
            sort={sort}
            direction={direction}
            arrow={arrow("album")}
            onSort={onSort}
          />
          <th className="w-40 px-3 py-2 text-left font-medium">Availability</th>
        </tr>
      </thead>
      <tbody>
        {tracks.map((track, index) => {
          const isCurrent = currentMbid === track.mbid;
          const playable = track.availability !== "unavailable";
          const activateRow = () => {
            if (!playable) return;
            if (isCurrent) {
              onTogglePlayback();
              return;
            }
            onPlayFrom(index);
          };
          const handleKeyDown = (
            event: KeyboardEvent<HTMLTableRowElement>,
          ) => {
            if (event.target !== event.currentTarget) return;
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            activateRow();
          };
          return (
            <tr
              key={track.mbid}
              onClick={activateRow}
              onKeyDown={handleKeyDown}
              onContextMenu={(event) => onContextMenu(event, track)}
              tabIndex={playable ? 0 : undefined}
              aria-current={isCurrent ? "true" : undefined}
              aria-disabled={!playable || undefined}
              aria-label={`${track.title} by ${track.artist}`}
              className={`group h-10 border-b border-[var(--color-border)]/75 outline-none last:border-b-0 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] ${
                isCurrent ? "bg-[var(--color-accent-soft)]" : ""
              } ${
                playable
                  ? "cursor-pointer hover:bg-[var(--color-surface)]/45"
                  : "cursor-default text-[var(--color-text-muted)]"
              }`}
            >
              <td className="w-8 pl-2 text-center text-[var(--color-text-muted)]">
                {isCurrent && isPlaying ? (
                  <Icon
                    name="songs"
                    size={12}
                    className="mx-auto text-[var(--color-accent)]"
                  />
                ) : playable ? (
                  <Icon
                    name="play"
                    size={10}
                    className="mx-auto opacity-0 transition-opacity group-hover:opacity-55 group-focus-visible:opacity-55"
                  />
                ) : (
                  <span
                    aria-hidden="true"
                    className="mx-auto block h-px w-2 bg-[var(--color-text-muted)]/40"
                  />
                )}
              </td>
              <td className="w-9 px-1 text-center">
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onLikeToggle(track);
                  }}
                  aria-label={track.is_liked ? "Unlike" : "Like"}
                  aria-pressed={track.is_liked}
                  className={`doppler-control mx-auto grid h-7 w-7 place-items-center rounded transition-opacity ${
                    track.is_liked
                      ? "text-[var(--color-liked)]"
                      : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                  }`}
                >
                  <Icon
                    name="heart"
                    size={13}
                    className={track.is_liked ? "fill-current" : ""}
                  />
                </button>
              </td>
              <td
                className={`truncate px-3 font-medium ${
                  isCurrent ? "text-[var(--color-accent)]" : ""
                }`}
              >
                {track.title}
              </td>
              <td className="truncate px-3 text-[var(--color-text-muted)]">
                {track.artist}
              </td>
              <td className="truncate px-3 text-[var(--color-text-muted)]">
                {track.album ?? ""}
              </td>
              <td className="whitespace-nowrap px-3 text-[10px] text-[var(--color-text-muted)]">
                <span
                  aria-hidden="true"
                  className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${AVAILABILITY_CLASS[track.availability]}`}
                />
                {AVAILABILITY_LABEL[track.availability]}
                {track.orphan ? (
                  <span className="ml-2 text-[var(--color-danger)]">
                    <span
                      aria-hidden="true"
                      className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-danger)]"
                    />
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

function SortableHeader({
  column,
  label,
  sort,
  direction,
  arrow,
  onSort,
}: {
  column: SongSortKey;
  label: string;
  sort: SongSortKey;
  direction: SongSortDirection;
  arrow: string | null;
  onSort: (key: SongSortKey) => void;
}) {
  return (
    <th
      scope="col"
      aria-sort={
        sort === column
          ? direction === "asc"
            ? "ascending"
            : "descending"
          : "none"
      }
      className="px-1 text-left font-medium"
    >
      <button
        type="button"
        onClick={() => onSort(column)}
        className="doppler-control flex h-8 w-full min-w-0 items-center gap-1 rounded px-2 text-left font-medium"
      >
        <span className="truncate">{label}</span>
        {arrow ? (
          <span
            aria-hidden="true"
            className="text-[10px] text-[var(--color-accent)]"
          >
            {arrow}
          </span>
        ) : null}
      </button>
    </th>
  );
}

function TableState({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-48 place-items-center text-center text-[11px] text-[var(--color-text-muted)]">
      <div className="flex flex-col items-center">{children}</div>
    </div>
  );
}
