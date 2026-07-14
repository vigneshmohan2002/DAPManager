import { describe, expect, it, vi } from "vitest";
import type { LibraryTrack, Playlist } from "../../lib/api";
import { buildSongContextMenu, type SongMenuActions } from "./menu";

function track(overrides: Partial<LibraryTrack> = {}): LibraryTrack {
  return {
    mbid: "track-1",
    title: "Song",
    artist: "Artist",
    album: "Album",
    album_id: "album-1",
    track_number: 1,
    disc_number: 1,
    availability: "local",
    is_liked: false,
    ...overrides,
  };
}

function actions(): SongMenuActions {
  return {
    onLikeToggle: vi.fn(),
    onPlayNext: vi.fn(),
    onAddToQueue: vi.fn(),
    onAddToPlaylist: vi.fn(),
    onQueueDownload: vi.fn(),
    onSuggest: vi.fn(),
    onContribute: vi.fn(),
    onIdentify: vi.fn(),
    onSoftDelete: vi.fn(),
  };
}

const playlists: Playlist[] = [
  {
    playlist_id: "manual",
    name: "Road trip",
    track_count: 2,
    updated_at: "2026-07-14T00:00:00Z",
    smart_rules: null,
  },
  {
    playlist_id: "smart",
    name: "Liked Songs",
    track_count: 1,
    updated_at: "2026-07-14T00:00:00Z",
    smart_rules: {
      match: "all",
      rules: [{ field: "artist", op: "equals", value: "Artist" }],
    },
  },
];

describe("buildSongContextMenu", () => {
  it("keeps labels, manual-playlist filtering, and callbacks stable", () => {
    const selected = track();
    const handlers = actions();
    const entries = buildSongContextMenu({
      track: selected,
      playlists,
      canContributeToMaster: true,
      contributingMbid: null,
      suggestingMbid: null,
      identifying: false,
      actions: handlers,
    });

    const labels = entries
      .filter((entry) => entry.kind === "item")
      .map((entry) => entry.label);
    expect(labels).toEqual([
      "Add to Liked Songs",
      "Play next",
      "Add to queue",
      "Queue Download",
      "Suggest to Jellyfin",
      "Contribute to master",
      "Identify & Tag",
      "Soft-Delete",
    ]);

    const playlistEntry = entries.find((entry) => entry.kind === "list");
    expect(playlistEntry?.kind).toBe("list");
    if (playlistEntry?.kind !== "list") return;
    expect(playlistEntry.items.map((item) => item.label)).toEqual([
      "Road trip",
    ]);
    playlistEntry.items[0]?.onSelect();
    expect(handlers.onAddToPlaylist).toHaveBeenCalledWith("manual", selected);
  });

  it("preserves local-only action gating and in-flight labels", () => {
    const unavailable = track({
      availability: "unavailable",
      is_liked: true,
    });
    const entries = buildSongContextMenu({
      track: unavailable,
      playlists: [],
      canContributeToMaster: true,
      contributingMbid: unavailable.mbid,
      suggestingMbid: unavailable.mbid,
      identifying: true,
      actions: actions(),
    });

    const item = (label: string) =>
      entries.find(
        (entry) => entry.kind === "item" && entry.label === label,
      );
    expect(item("Remove from Liked Songs")).toMatchObject({ disabled: true });
    expect(item("Play next")).toMatchObject({ disabled: true });
    expect(item("Add to queue")).toMatchObject({ disabled: true });
    expect(item("Suggesting…")).toMatchObject({ disabled: true });
    expect(item("Contributing…")).toMatchObject({ disabled: true });
    expect(item("Identifying…")).toMatchObject({ disabled: true });
  });
});
