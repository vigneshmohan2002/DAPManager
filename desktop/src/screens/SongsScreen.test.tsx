import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { LibraryTrack } from "../lib/api";
import SongsScreen from "./SongsScreen";

const apiMocks = vi.hoisted(() => ({
  addTrackToPlaylist: vi.fn(),
  applyTrackTags: vi.fn(),
  contributeTrack: vi.fn(),
  fetchAllTracks: vi.fn(),
  fetchConfig: vi.fn(),
  fetchPlaylists: vi.fn(),
  identifyTrack: vi.fn(),
  postSuggestions: vi.fn(),
  queueCatalogDownload: vi.fn(),
  setTrackLiked: vi.fn(),
  softDeleteTrack: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  play: vi.fn(),
  toggle: vi.fn(),
  playNext: vi.fn(),
  addToQueue: vi.fn(),
  setTrackLikedInQueue: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  ...apiMocks,
  SUGGESTION_HOST_KEY: "master_url",
  suggestionHostFromConfig: vi.fn(() => null),
}));

vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => ({
    ...playerMocks,
    current: null,
    isPlaying: false,
  }),
}));

vi.mock("../components/Toast", () => ({
  useToast: () => ({ show: vi.fn() }),
}));

const tracks: LibraryTrack[] = [
  {
    mbid: "local-1",
    title: "Local opener",
    artist: "Alpha",
    album: "First Album",
    album_id: "album-local",
    track_number: 1,
    disc_number: 1,
    availability: "local",
    is_liked: false,
  },
  {
    mbid: "missing-1",
    title: "Missing middle",
    artist: "Bravo",
    album: "Hidden Needle",
    album_id: "album-missing",
    track_number: 2,
    disc_number: 1,
    availability: "unavailable",
    is_liked: false,
  },
  {
    mbid: "drive-1",
    title: "Target closer",
    artist: "Charlie",
    album: "Last Album",
    album_id: "album-drive",
    track_number: 3,
    disc_number: 1,
    availability: "drive",
    is_liked: true,
  },
];

describe("SongsScreen list and playback contract", () => {
  beforeEach(() => {
    apiMocks.fetchAllTracks.mockResolvedValue(tracks);
    apiMocks.fetchPlaylists.mockResolvedValue([]);
    apiMocks.fetchConfig.mockResolvedValue({
      config: { device_role: "standalone", master_url: "" },
      editable_keys: [],
      secret_keys: [],
      bool_keys: [],
      groups: [],
    });
    apiMocks.setTrackLiked.mockResolvedValue({ success: true });
  });

  it("filters case-insensitively across album metadata", async () => {
    const user = userEvent.setup();
    render(
      <SongsScreen
        ready
        playlistsVersion={0}
        onPlaylistsChanged={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
    await screen.findByText("Local opener");

    await user.type(screen.getByPlaceholderText("Search"), "nEeDlE");

    expect(screen.getByText("Missing middle")).toBeInTheDocument();
    expect(screen.queryByText("Local opener")).not.toBeInTheDocument();
    expect(screen.queryByText("Target closer")).not.toBeInTheDocument();
  });

  it("removes unavailable tracks and remaps the clicked row to its queue index", async () => {
    render(
      <SongsScreen
        ready
        playlistId="playlist-1"
        playlistsVersion={0}
        onPlaylistsChanged={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
    const target = await screen.findByText("Target closer");

    const row = target.closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    expect(apiMocks.fetchAllTracks).toHaveBeenCalledWith({
      playlistId: "playlist-1",
      localOnly: true,
      includeOrphans: false,
    });
    expect(playerMocks.play).toHaveBeenCalledTimes(1);
    const [queue, startIndex] = playerMocks.play.mock.calls[0] as [
      Array<LibraryTrack & { albumId: string | null }>,
      number,
    ];
    expect(queue.map((track) => track.mbid)).toEqual(["local-1", "drive-1"]);
    expect(queue.map((track) => track.albumId)).toEqual([
      "album-local",
      "album-drive",
    ]);
    expect(startIndex).toBe(1);
  });

  it("does not start playback when an unavailable row is clicked", async () => {
    render(
      <SongsScreen
        ready
        playlistsVersion={0}
        onPlaylistsChanged={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
    const missing = await screen.findByText("Missing middle");

    fireEvent.click(missing.closest("tr")!);

    await waitFor(() => expect(playerMocks.play).not.toHaveBeenCalled());
  });

  it("optimistically likes a track without triggering row playback", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    render(
      <SongsScreen
        ready
        playlistsVersion={0}
        onPlaylistsChanged={onPlaylistsChanged}
        onOpenSettings={vi.fn()}
      />,
    );
    const localTitle = await screen.findByText("Local opener");
    const likeButton = localTitle.closest("tr")?.querySelector("button");
    expect(likeButton).not.toBeNull();

    await user.click(likeButton!);

    expect(likeButton).toHaveAttribute("aria-label", "Unlike");
    expect(likeButton).toHaveAttribute("aria-pressed", "true");
    expect(apiMocks.setTrackLiked).toHaveBeenCalledWith("local-1", true);
    expect(playerMocks.setTrackLikedInQueue).toHaveBeenCalledWith(
      "local-1",
      true,
    );
    expect(playerMocks.play).not.toHaveBeenCalled();
    expect(onPlaylistsChanged).toHaveBeenCalledTimes(1);
  });
});
