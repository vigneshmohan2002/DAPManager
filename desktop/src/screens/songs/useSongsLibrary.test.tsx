import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LibraryTrack, Playlist } from "../../lib/api";
import { useSongsLibrary } from "./useSongsLibrary";

const apiMocks = vi.hoisted(() => ({
  fetchAllTracks: vi.fn(),
  fetchConfig: vi.fn(),
  fetchPlaylists: vi.fn(),
}));

vi.mock("../../lib/api", () => apiMocks);

const tracks: LibraryTrack[] = [
  {
    mbid: "track-1",
    title: "Track",
    artist: "Artist",
    album: "Album",
    album_id: "album-1",
    track_number: 1,
    disc_number: 1,
    availability: "local",
    is_liked: false,
  },
];

const playlists: Playlist[] = [
  {
    playlist_id: "playlist-1",
    name: "Favourites",
    track_count: 1,
    updated_at: "2026-07-14T00:00:00Z",
    smart_rules: null,
  },
];

describe("useSongsLibrary", () => {
  beforeEach(() => {
    apiMocks.fetchAllTracks.mockResolvedValue(tracks);
    apiMocks.fetchPlaylists.mockResolvedValue(playlists);
    apiMocks.fetchConfig.mockResolvedValue({
      config: {
        device_role: "satellite",
        master_url: "http://master.tailnet:5001",
      },
    });
  });

  it("waits for readiness before loading", () => {
    const { result } = renderHook(() =>
      useSongsLibrary({
        ready: false,
        playlistsVersion: 0,
        catalogOnly: false,
        showOrphans: false,
      }),
    );

    expect(result.current.loading).toBe(true);
    expect(apiMocks.fetchAllTracks).not.toHaveBeenCalled();
    expect(apiMocks.fetchConfig).not.toHaveBeenCalled();
    expect(apiMocks.fetchPlaylists).not.toHaveBeenCalled();
  });

  it("loads scoped rows, playlist metadata, and contribution capability", async () => {
    const { result, rerender } = renderHook(
      (options: { catalogOnly: boolean; showOrphans: boolean }) =>
        useSongsLibrary({
          ready: true,
          playlistId: "playlist-1",
          playlistsVersion: 0,
          ...options,
        }),
      {
        initialProps: { catalogOnly: false, showOrphans: false },
      },
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.rows).toEqual(tracks);
    expect(result.current.playlistName).toBe("Favourites");
    expect(result.current.canContributeToMaster).toBe(true);
    expect(apiMocks.fetchAllTracks).toHaveBeenCalledWith({
      playlistId: "playlist-1",
      localOnly: true,
      includeOrphans: false,
    });

    rerender({ catalogOnly: true, showOrphans: true });

    await waitFor(() =>
      expect(apiMocks.fetchAllTracks).toHaveBeenLastCalledWith({
        playlistId: "playlist-1",
        localOnly: false,
        includeOrphans: true,
      }),
    );
  });
});
