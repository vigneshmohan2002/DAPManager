import type { LibraryTrack } from "./api";

export type SongSortKey = "title" | "artist" | "album";
export type SongSortDirection = "asc" | "desc";

export type PlayableLibraryTrack = LibraryTrack & {
  albumId: string | null;
};

export type PlayableQueueSelection = {
  queue: PlayableLibraryTrack[];
  startIndex: number;
};

function matchesSearch(track: LibraryTrack, query: string): boolean {
  if (track.title.toLowerCase().includes(query)) return true;
  if (track.artist.toLowerCase().includes(query)) return true;
  return (track.album ?? "").toLowerCase().includes(query);
}

function sortValue(track: LibraryTrack, sort: SongSortKey): string {
  if (sort === "title") return track.title.toLowerCase();
  if (sort === "album") return (track.album ?? "").toLowerCase();
  return track.artist.toLowerCase();
}

export function filterAndSortTracks(
  tracks: readonly LibraryTrack[],
  search: string,
  sort: SongSortKey,
  direction: SongSortDirection,
): LibraryTrack[] {
  const query = search.trim().toLowerCase();
  const filtered = query
    ? tracks.filter((track) => matchesSearch(track, query))
    : tracks;
  const multiplier = direction === "asc" ? 1 : -1;

  return [...filtered].sort((left, right) => {
    const leftValue = sortValue(left, sort);
    const rightValue = sortValue(right, sort);
    if (leftValue < rightValue) return -1 * multiplier;
    if (leftValue > rightValue) return 1 * multiplier;
    return 0;
  });
}

export function createPlayableQueue(
  tracks: readonly LibraryTrack[],
  selectedIndex: number,
): PlayableQueueSelection {
  const queue: PlayableLibraryTrack[] = [];
  let startIndex = 0;

  tracks.forEach((track, index) => {
    if (track.availability === "unavailable") return;
    if (index === selectedIndex) startIndex = queue.length;
    queue.push({ ...track, albumId: track.album_id });
  });

  return { queue, startIndex };
}
