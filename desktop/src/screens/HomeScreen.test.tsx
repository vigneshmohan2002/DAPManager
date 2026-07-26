import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { HomePayload } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(
    (base: string, albumId: string) => `${base}/covers/${albumId}`,
  ),
  backendUrl: vi.fn(),
  fetchHome: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  playAlbum: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => playerMocks,
}));
vi.mock("../components/Toast", () => ({
  useToast: () => toastMocks,
}));

import HomeScreen from "./HomeScreen";

const payload: HomePayload = {
  daily_mixes: [
    {
      playlist_id: "mix-1",
      name: "Daily Mix: Soul",
      tag: "Soul",
      track_count: 18,
    },
  ],
  jump_back_in: [
    {
      album_id: "album-1",
      title: "Blue Lines",
      artist: "Massive Attack",
    },
  ],
  top_artists: [
    {
      artist: "Little Simz",
      plays: 12,
      distinct_tracks: 7,
    },
  ],
  liked: {
    total: 1,
    preview: [
      {
        mbid: "track-1",
        title: "Introvert",
        artist: "Little Simz",
        album: "Sometimes I Might Be Introvert",
        album_id: "album-2",
      },
    ],
  },
  recent: [
    {
      id: 1,
      mbid: "track-2",
      played_at: new Date().toISOString(),
      source: "desktop",
      title: "Angel",
      artist: "Massive Attack",
      album: "Mezzanine",
      album_id: "album-3",
    },
  ],
};

function renderHome(ready = true) {
  const props = {
    ready,
    onOpenAlbum: vi.fn(),
    onOpenArtist: vi.fn(),
    onOpenPlaylist: vi.fn(),
    onOpenStats: vi.fn(),
  };
  render(<HomeScreen {...props} />);
  return props;
}

describe("HomeScreen", () => {
  beforeEach(() => {
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.fetchHome.mockResolvedValue(payload);
    playerMocks.playAlbum.mockResolvedValue(9);
  });

  it("routes mixes, artists, liked songs, and history through App callbacks", async () => {
    const user = userEvent.setup();
    const props = renderHome();

    await user.click(
      await screen.findByRole("button", {
        name: "Open playlist Daily Mix: Soul",
      }),
    );
    expect(props.onOpenPlaylist).toHaveBeenCalledWith("mix-1");

    await user.click(
      screen.getByRole("button", { name: "Open artist Little Simz" }),
    );
    expect(props.onOpenArtist).toHaveBeenCalledWith({
      name: "Little Simz",
      album_count: 0,
      track_count: 7,
    });

    await user.click(screen.getByRole("button", { name: /Introvert/ }));
    expect(props.onOpenAlbum).toHaveBeenCalledWith({
      id: "album-2",
      title: "Sometimes I Might Be Introvert",
      artist: "Little Simz",
      track_count: 0,
    });

    await user.click(screen.getByRole("button", { name: /Angel/ }));
    expect(props.onOpenAlbum).toHaveBeenCalledWith({
      id: "album-3",
      title: "Mezzanine",
      artist: "Massive Attack",
      track_count: 0,
    });
  });

  it("does not request home data before setup is ready", () => {
    renderHome(false);

    expect(apiMocks.fetchHome).not.toHaveBeenCalled();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });

  it("opens a jump-back album after a single click", async () => {
    const props = renderHome();
    const card = await screen.findByRole("button", {
      name: "Open Blue Lines by Massive Attack",
    });
    vi.useFakeTimers();

    fireEvent.click(card, { detail: 1 });
    act(() => vi.advanceTimersByTime(400));

    expect(props.onOpenAlbum).toHaveBeenCalledWith({
      id: "album-1",
      title: "Blue Lines",
      artist: "Massive Attack",
      track_count: 0,
    });
  });

  it("plays a jump-back album from its beginning on double-click", async () => {
    const props = renderHome();
    const card = await screen.findByRole("button", {
      name: "Open Blue Lines by Massive Attack",
    });
    vi.useFakeTimers();

    fireEvent.click(card, { detail: 1 });
    fireEvent.click(card, { detail: 2 });
    fireEvent.doubleClick(card, { detail: 2 });
    act(() => vi.runAllTimers());

    expect(playerMocks.playAlbum).toHaveBeenCalledWith("album-1");
    expect(props.onOpenAlbum).not.toHaveBeenCalled();
  });
});
