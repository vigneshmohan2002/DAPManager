import { describe, expect, it } from "vitest";
import type { Album, Artist } from "../lib/api";
import {
  INITIAL_NAVIGATION_STATE,
  activeSidebarId,
  navigationReducer,
  routeFromSidebarId,
  selectAppSurface,
  type NavigationState,
} from "./model";

const album: Album = {
  id: "release-1",
  title: "Album",
  artist: "Artist",
  track_count: 10,
};

const artist: Artist = {
  name: "Artist",
  album_count: 1,
  track_count: 10,
};

describe("navigation model", () => {
  it("decodes only the playlist prefix and preserves the complete id", () => {
    expect(routeFromSidebarId("playlist:road/trip?year=2026")).toEqual({
      kind: "playlist",
      playlistId: "road/trip?year=2026",
    });
  });

  it("keeps an explicit unknown route for forward-compatible sidebar ids", () => {
    expect(routeFromSidebarId("future-screen")).toEqual({
      kind: "unknown",
      screen: "future-screen",
    });
  });

  it("returns an album to the exact route that opened it", () => {
    const artistRoute: NavigationState = { kind: "artist", artist };
    const albumRoute = navigationReducer(artistRoute, {
      type: "openAlbum",
      album,
    });

    expect(albumRoute).toEqual({
      kind: "album",
      album,
      returnTo: artistRoute,
    });
    expect(activeSidebarId(albumRoute)).toBe("artists");
    expect(navigationReducer(albumRoute, { type: "closeAlbum" })).toBe(
      artistRoute,
    );
  });

  it("replaces an open album without nesting its return route", () => {
    const first = navigationReducer(INITIAL_NAVIGATION_STATE, {
      type: "openAlbum",
      album,
    });
    const replacement = navigationReducer(first, {
      type: "openAlbum",
      album: { ...album, id: "release-2" },
    });

    expect(replacement).toMatchObject({
      kind: "album",
      album: { id: "release-2" },
      returnTo: INITIAL_NAVIGATION_STATE,
    });
  });

  it("clears a deleted playlist scope while preserving an open album", () => {
    const playlistRoute: NavigationState = {
      kind: "playlist",
      playlistId: "mix-1",
    };
    const albumRoute = navigationReducer(playlistRoute, {
      type: "openAlbum",
      album,
    });

    expect(
      navigationReducer(albumRoute, {
        type: "playlistDeleted",
        playlistId: "mix-1",
      }),
    ).toEqual({
      kind: "album",
      album,
      returnTo: { kind: "screen", screen: "songs" },
    });
  });

  it("stores and consumes settings focus only on a settings route", () => {
    const settings = navigationReducer(INITIAL_NAVIGATION_STATE, {
      type: "openSettings",
      focusKey: "acoustid_api_key",
    });
    expect(settings).toEqual({
      kind: "settings",
      focusKey: "acoustid_api_key",
    });
    expect(
      navigationReducer(settings, { type: "consumeSettingsFocus" }),
    ).toEqual({ kind: "settings", focusKey: null });
  });
});

describe("application surface selection", () => {
  it("gives setup precedence over booting and mini-player layouts", () => {
    expect(
      selectAppSurface({
        needsSetup: true,
        status: "booting",
        isMini: true,
        bootingSlowly: true,
      }),
    ).toEqual({ kind: "setup" });
  });

  it("keeps the dependency hint attached to the booting surface", () => {
    expect(
      selectAppSurface({
        needsSetup: false,
        status: "booting",
        isMini: true,
        bootingSlowly: true,
      }),
    ).toEqual({ kind: "booting", showDependencyHint: true });
  });

  it("selects mini-player only after startup gates are complete", () => {
    expect(
      selectAppSurface({
        needsSetup: false,
        status: "ready",
        isMini: true,
        bootingSlowly: false,
      }),
    ).toEqual({ kind: "miniPlayer" });
  });
});
