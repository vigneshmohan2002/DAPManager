import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(),
  backendUrl: vi.fn(),
}));

const windowMocks = vi.hoisted(() => ({
  exitMiniPlayer: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  current: {
    mbid: "track-1",
    title: "First Light",
    artist: "The Testers",
    album: "A Test Album",
    track_number: 1,
    disc_number: 1,
    albumId: "album-1",
  } as {
    mbid: string;
    title: string;
    artist: string;
    album: string | null;
    track_number: number | null;
    disc_number: number | null;
    albumId: string | null;
  } | null,
  isPlaying: false,
  toggle: vi.fn(),
  next: vi.fn(),
  prev: vi.fn(),
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../lib/window", () => windowMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => playerMocks,
}));

import MiniPlayer from "./MiniPlayer";

describe("MiniPlayer", () => {
  beforeEach(() => {
    playerMocks.current = {
      mbid: "track-1",
      title: "First Light",
      artist: "The Testers",
      album: "A Test Album",
      track_number: 1,
      disc_number: 1,
      albumId: "album-1",
    };
    playerMocks.isPlaying = false;
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.albumCoverUrl.mockReturnValue(
      "http://localhost:5001/artwork/album-1",
    );
    windowMocks.exitMiniPlayer.mockResolvedValue(undefined);
  });

  it("shows track details and wires the compact transport", async () => {
    const user = userEvent.setup();
    render(<MiniPlayer />);

    expect(screen.getByText("First Light")).toBeInTheDocument();
    expect(screen.getByText("The Testers")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Previous" }));
    await user.click(screen.getByRole("button", { name: "Play" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(
      screen.getByRole("button", { name: "Exit mini-player" }),
    );

    expect(playerMocks.prev).toHaveBeenCalledOnce();
    expect(playerMocks.toggle).toHaveBeenCalledOnce();
    expect(playerMocks.next).toHaveBeenCalledOnce();
    expect(windowMocks.exitMiniPlayer).toHaveBeenCalledOnce();
  });

  it("disables playback controls without a current track", () => {
    playerMocks.current = null;
    render(<MiniPlayer />);

    expect(screen.getByText("Nothing playing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  });
});
