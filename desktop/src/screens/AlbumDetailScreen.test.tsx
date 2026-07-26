import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Album, Track } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(),
  backendUrl: vi.fn(),
  fetchAlbumTracks: vi.fn(),
  setTrackLiked: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  play: vi.fn(),
  toggle: vi.fn(),
  toggleShuffle: vi.fn(),
  setTrackLikedInQueue: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => ({
    play: playerMocks.play,
    current: null,
    isPlaying: false,
    shuffle: false,
    toggle: playerMocks.toggle,
    toggleShuffle: playerMocks.toggleShuffle,
    setTrackLikedInQueue: playerMocks.setTrackLikedInQueue,
  }),
}));
vi.mock("../components/Toast", () => ({
  useToast: () => toastMocks,
}));

import AlbumDetailScreen from "./AlbumDetailScreen";

const album: Album = {
  id: "album-1",
  title: "Test Album",
  artist: "Test Artist",
  track_count: 32,
};

function makeTrack(index: number, title = `Track ${index}`): Track {
  return {
    mbid: `track-${index}`,
    title,
    artist: "Test Artist",
    album: "Test Album",
    track_number: index,
    disc_number: 1,
    is_liked: false,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderAlbum(currentAlbum = album) {
  return render(
    <AlbumDetailScreen album={currentAlbum} onBack={vi.fn()} />,
  );
}

describe("AlbumDetailScreen", () => {
  beforeEach(() => {
    apiMocks.albumCoverUrl.mockReturnValue("http://localhost/cover.jpg");
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.setTrackLiked.mockResolvedValue({ success: true });
  });

  it("shows a loading summary and disables Play while tracks load", () => {
    const pending = deferred<Track[]>();
    apiMocks.fetchAlbumTracks.mockReturnValue(pending.promise);

    renderAlbum();

    expect(screen.getByText(/Loading tracks…/)).toBeInTheDocument();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Play/ })).toBeDisabled();
  });

  it("uses the 16 playable rows instead of the 32-row catalog count", async () => {
    apiMocks.fetchAlbumTracks.mockResolvedValue(
      Array.from({ length: 16 }, (_, index) => makeTrack(index + 1)),
    );

    renderAlbum();

    expect(await screen.findByText(/16 tracks/)).toBeInTheDocument();
    expect(screen.queryByText(/32 tracks/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(16);
  });

  it("uses the canonical primary artist for presentation and feature badges", async () => {
    const featuredCredit = "2Pac featuring Big Syke";
    const canonicalAlbum: Album = {
      id: "all-eyez-on-me",
      title: "All Eyez on Me",
      artist: featuredCredit,
      primary_artist: "2Pac",
      credited_artists: ["2Pac", featuredCredit],
      track_count: 2,
    };
    apiMocks.fetchAlbumTracks.mockResolvedValue([
      {
        mbid: "primary-track",
        title: "Primary Track",
        artist: "2Pac",
        album: canonicalAlbum.title,
        track_number: 1,
        disc_number: 1,
      },
      {
        mbid: "featured-track",
        title: "Featured Track",
        artist: featuredCredit,
        album: canonicalAlbum.title,
        track_number: 2,
        disc_number: 1,
      },
    ]);

    renderAlbum(canonicalAlbum);

    const primaryRow = (await screen.findByText("Primary Track")).closest("li");
    const featuredRow = screen.getByText("Featured Track").closest("li");
    expect(primaryRow).not.toBeNull();
    expect(featuredRow).not.toBeNull();
    expect(screen.getAllByText("2Pac")).toHaveLength(2);
    expect(within(primaryRow!).queryByText("2Pac")).not.toBeInTheDocument();
    expect(
      within(featuredRow!).getByText(featuredCredit),
    ).toBeInTheDocument();
  });

  it.each([
    [0, "0 tracks"],
    [1, "1 track"],
  ])("formats a playable count of %i correctly", async (count, summary) => {
    apiMocks.fetchAlbumTracks.mockResolvedValue(
      Array.from({ length: count }, (_, index) => makeTrack(index + 1)),
    );

    renderAlbum();

    expect(await screen.findByText(new RegExp(summary))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Play/ })).toHaveProperty(
      "disabled",
      count === 0,
    );
  });

  it("shows an unavailable count when the playable-track request fails", async () => {
    apiMocks.fetchAlbumTracks.mockRejectedValue(new Error("master offline"));

    renderAlbum();

    expect(
      await screen.findByText(/Track count unavailable/),
    ).toBeInTheDocument();
    expect(screen.getByText("Error: master offline")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Play/ })).toBeDisabled();
  });

  it("keeps the total count while filtering and plays from the absolute index", async () => {
    const tracks = [
      makeTrack(1, "First Track"),
      makeTrack(2, "Second Track"),
      makeTrack(3, "Third Track"),
    ];
    apiMocks.fetchAlbumTracks.mockResolvedValue(tracks);
    const user = userEvent.setup();
    renderAlbum();
    await screen.findByText(/3 tracks/);

    await user.type(screen.getByRole("searchbox"), "SECOND");

    expect(screen.getByText(/3 tracks/)).toBeInTheDocument();
    expect(screen.queryByText("First Track")).not.toBeInTheDocument();
    expect(screen.getByText("Second Track")).toBeInTheDocument();
    expect(screen.queryByText("Third Track")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Second Track").closest("li")!);
    expect(playerMocks.play).toHaveBeenCalledWith(
      tracks.map((track) => ({ ...track, albumId: album.id })),
      1,
    );
  });

  it("enables shuffle and starts the complete album at its first row", async () => {
    const tracks = [makeTrack(1), makeTrack(2), makeTrack(3)];
    apiMocks.fetchAlbumTracks.mockResolvedValue(tracks);
    const user = userEvent.setup();
    renderAlbum();
    await screen.findByText(/3 tracks/);

    await user.click(screen.getByRole("button", { name: "Shuffle" }));

    expect(playerMocks.toggleShuffle).toHaveBeenCalledOnce();
    expect(playerMocks.play).toHaveBeenCalledWith(
      tracks.map((track) => ({ ...track, albumId: album.id })),
      0,
    );
  });

  it("clears stale rows and search when switching directly between albums", async () => {
    const nextTracks = deferred<Track[]>();
    apiMocks.fetchAlbumTracks.mockImplementation((albumId: string) =>
      albumId === album.id
        ? Promise.resolve([makeTrack(1, "Old Track")])
        : nextTracks.promise,
    );
    const user = userEvent.setup();
    const view = renderAlbum();
    await screen.findByText("Old Track");
    const search = screen.getByRole("searchbox");
    await user.type(search, "old");

    view.rerender(
      <AlbumDetailScreen
        album={{ ...album, id: "album-2", title: "Next Album" }}
        onBack={vi.fn()}
      />,
    );

    await waitFor(() => expect(search).toHaveValue(""));
    expect(screen.getByText(/Loading tracks…/)).toBeInTheDocument();
    expect(screen.queryByText("Old Track")).not.toBeInTheDocument();

    await act(async () => {
      nextTracks.resolve([makeTrack(1, "New Track")]);
      await nextTracks.promise;
    });
    expect(await screen.findByText("New Track")).toBeInTheDocument();
  });
});
