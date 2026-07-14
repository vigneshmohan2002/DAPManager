import { describe, expect, it } from "vitest";
import type { LibraryTrack } from "./api";
import { createPlayableQueue, filterAndSortTracks } from "./songList";

function track(
  mbid: string,
  overrides: Partial<LibraryTrack> = {},
): LibraryTrack {
  return {
    mbid,
    title: mbid,
    artist: "Artist",
    album: "Album",
    album_id: `album-${mbid}`,
    track_number: 1,
    disc_number: 1,
    availability: "local",
    is_liked: false,
    ...overrides,
  };
}

describe("filterAndSortTracks", () => {
  it("matches trimmed searches case-insensitively across track metadata", () => {
    const tracks = [
      track("one", { title: "First Song", artist: "Alpha", album: null }),
      track("two", { title: "Second Song", artist: "Bravo", album: "Needle" }),
    ];

    expect(filterAndSortTracks(tracks, "  nEeDlE ", "artist", "asc")).toEqual([
      tracks[1],
    ]);
  });

  it("sorts the requested field without mutating the source rows", () => {
    const tracks = [
      track("one", { title: "Bravo", artist: "Zulu" }),
      track("two", { title: "alpha", artist: "Alpha" }),
    ];

    expect(
      filterAndSortTracks(tracks, "", "title", "desc").map(
        (item) => item.mbid,
      ),
    ).toEqual(["one", "two"]);
    expect(tracks.map((item) => item.mbid)).toEqual(["one", "two"]);
  });
});

describe("createPlayableQueue", () => {
  it("removes unavailable rows and remaps the selected visible index", () => {
    const tracks = [
      track("local"),
      track("missing", { availability: "unavailable" }),
      track("drive", { availability: "drive", album_id: "drive-album" }),
    ];

    const selection = createPlayableQueue(tracks, 2);

    expect(selection.queue.map((item) => item.mbid)).toEqual([
      "local",
      "drive",
    ]);
    expect(selection.queue.map((item) => item.albumId)).toEqual([
      "album-local",
      "drive-album",
    ]);
    expect(selection.startIndex).toBe(1);
  });

  it("returns an empty queue when every row is unavailable", () => {
    expect(
      createPlayableQueue(
        [track("missing", { availability: "unavailable" })],
        0,
      ),
    ).toEqual({ queue: [], startIndex: 0 });
  });
});
