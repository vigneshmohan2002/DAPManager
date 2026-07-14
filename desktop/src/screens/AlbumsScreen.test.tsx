import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Album } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(),
  backendUrl: vi.fn(),
  fetchAlbums: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({ playAlbum: vi.fn() }));
const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => ({ playAlbum: playerMocks.playAlbum }),
}));
vi.mock("../components/Toast", () => ({
  useToast: () => toastMocks,
}));

import AlbumsScreen from "./AlbumsScreen";

const album: Album = {
  id: "album-1",
  title: "Test Album",
  artist: "Test Artist",
  track_count: 32,
};

describe("AlbumsScreen", () => {
  beforeEach(() => {
    apiMocks.albumCoverUrl.mockReturnValue("http://localhost/cover.jpg");
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.fetchAlbums.mockResolvedValue([album]);
    playerMocks.playAlbum.mockResolvedValue(16);
  });

  it("opens an album after a single click", async () => {
    const onOpen = vi.fn();
    render(<AlbumsScreen ready onOpen={onOpen} />);
    const card = await screen.findByRole("button", {
      name: "Open Test Album by Test Artist",
    });
    vi.useFakeTimers();

    fireEvent.click(card, { detail: 1 });
    act(() => vi.advanceTimersByTime(400));

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith(album);
  });

  it("plays an album on double-click without opening the detail screen", async () => {
    const onOpen = vi.fn();
    render(<AlbumsScreen ready onOpen={onOpen} />);
    const card = await screen.findByRole("button", {
      name: "Open Test Album by Test Artist",
    });
    vi.useFakeTimers();

    fireEvent.click(card, { detail: 1 });
    fireEvent.click(card, { detail: 2 });
    fireEvent.doubleClick(card, { detail: 2 });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => vi.runAllTimers());

    expect(playerMocks.playAlbum).toHaveBeenCalledOnce();
    expect(playerMocks.playAlbum).toHaveBeenCalledWith(album.id);
    expect(onOpen).not.toHaveBeenCalled();
  });
});
