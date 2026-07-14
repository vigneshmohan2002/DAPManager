import { describe, expect, it, vi } from "vitest";
import type { Playlist, SmartRuleset } from "../../lib/api";
import {
  playlistMenuEntries,
  playlistSidebarId,
  playlistSidebarItems,
  type PlaylistMenu,
} from "./model";

const rules: SmartRuleset = {
  match: "all",
  rules: [{ field: "artist", op: "contains", value: "Artist" }],
};

function playlist(
  playlistId: string,
  name: string,
  smartRules: SmartRuleset | null = null,
): Playlist {
  return {
    playlist_id: playlistId,
    name,
    track_count: 3,
    updated_at: "2026-07-14T12:00:00Z",
    smart_rules: smartRules,
  };
}

describe("sidebar model", () => {
  it("preserves playlist navigation encoding and pins Liked Songs first", () => {
    expect(playlistSidebarId("road/trip?2026")).toBe(
      "playlist:road/trip?2026",
    );
    expect(
      playlistSidebarItems([
        playlist("road-trip", "Road trip", rules),
        playlist("liked_songs", "Liked Songs", rules),
      ]),
    ).toEqual([
      expect.objectContaining({
        id: "playlist:liked_songs",
        label: "♥ Liked Songs",
      }),
      expect.objectContaining({
        id: "playlist:road-trip",
        label: "Road trip",
        smartRules: rules,
      }),
    ]);
  });

  it("gates system playlist actions and only offers rule editing for smart lists", () => {
    const actions = {
      editRules: vi.fn(),
      rename: vi.fn(),
      remove: vi.fn(),
    };
    const likedMenu: PlaylistMenu = {
      x: 1,
      y: 2,
      pid: "liked_songs",
      name: "♥ Liked Songs",
      smartRules: rules,
    };
    const likedEntries = playlistMenuEntries(likedMenu, actions);
    expect(likedEntries?.filter((entry) => entry.kind === "item")).toEqual([
      expect.objectContaining({ label: "Edit rules…", disabled: true }),
      expect.objectContaining({ label: "Rename…", disabled: true }),
      expect.objectContaining({ label: "Delete (soft)", disabled: true }),
    ]);

    const staticEntries = playlistMenuEntries(
      { ...likedMenu, pid: "road-trip", name: "Road trip", smartRules: null },
      actions,
    );
    expect(
      staticEntries
        ?.filter((entry) => entry.kind === "item")
        .map((entry) => entry.kind === "item" && entry.label),
    ).toEqual(["Rename…", "Delete (soft)"]);
  });
});
