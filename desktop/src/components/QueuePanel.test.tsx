import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(),
  backendUrl: vi.fn(),
  setTrackLiked: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  queue: [
    {
      mbid: "track-1",
      title: "First Song",
      artist: "First Artist",
      album: "First Album",
      track_number: 1,
      disc_number: 1,
      albumId: "album-1",
      is_liked: false,
    },
    {
      mbid: "track-2",
      title: "Second Song",
      artist: "Second Artist",
      album: null,
      track_number: null,
      disc_number: null,
      albumId: null,
      is_liked: true,
    },
  ],
  index: 0,
  isPlaying: true,
  jumpTo: vi.fn(),
  removeFromQueue: vi.fn(),
  clearQueue: vi.fn(),
  setTrackLikedInQueue: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => playerMocks,
}));
vi.mock("./Toast", () => ({
  useToast: () => toastMocks,
}));

import QueuePanel from "./QueuePanel";

describe("QueuePanel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1200,
    });
    playerMocks.queue.splice(
      0,
      playerMocks.queue.length,
      {
        mbid: "track-1",
        title: "First Song",
        artist: "First Artist",
        album: "First Album",
        track_number: 1,
        disc_number: 1,
        albumId: "album-1",
        is_liked: false,
      },
      {
        mbid: "track-2",
        title: "Second Song",
        artist: "Second Artist",
        album: null,
        track_number: null,
        disc_number: null,
        albumId: null,
        is_liked: true,
      },
    );
    playerMocks.index = 0;
    playerMocks.isPlaying = true;
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.albumCoverUrl.mockImplementation(
      (base: string, albumId: string) => `${base}/covers/${albumId}`,
    );
    apiMocks.setTrackLiked.mockResolvedValue({ success: true });
  });

  it("renders nothing while closed and avoids resolving artwork", () => {
    render(<QueuePanel open={false} onClose={vi.fn()} />);

    expect(
      screen.queryByRole("complementary", { name: "Playback queue" }),
    ).not.toBeInTheDocument();
    expect(apiMocks.backendUrl).not.toHaveBeenCalled();
  });

  it("shows the active track and uses the authenticated album artwork URL", async () => {
    render(<QueuePanel open onClose={vi.fn()} />);

    expect(screen.getByText("2 tracks")).toBeInTheDocument();
    expect(screen.getByText("First Song").closest("li")).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByLabelText("Now playing")).toBeInTheDocument();

    const artwork = await screen.findByRole("img", {
      name: "First Song cover",
    });
    expect(apiMocks.albumCoverUrl).toHaveBeenCalledWith(
      "http://localhost:5001",
      "album-1",
    );
    expect(artwork).toHaveAttribute(
      "src",
      "http://localhost:5001/covers/album-1",
    );
  });

  it("jumps, removes, clears, and closes without cross-triggering row playback", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<QueuePanel open onClose={onClose} />);

    await user.click(
      screen.getByRole("button", {
        name: "Play Second Song by Second Artist",
      }),
    );
    expect(playerMocks.jumpTo).toHaveBeenCalledWith(1);

    await user.click(
      screen.getAllByRole("button", { name: "Remove from queue" })[1],
    );
    expect(playerMocks.removeFromQueue).toHaveBeenCalledWith(1);
    expect(playerMocks.jumpTo).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(playerMocks.clearQueue).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: "Close queue" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("updates a like optimistically and does not activate the row", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    render(
      <QueuePanel
        open
        onClose={vi.fn()}
        onPlaylistsChanged={onPlaylistsChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Like" }));

    expect(playerMocks.setTrackLikedInQueue).toHaveBeenCalledWith(
      "track-1",
      true,
    );
    expect(apiMocks.setTrackLiked).toHaveBeenCalledWith("track-1", true);
    expect(onPlaylistsChanged).toHaveBeenCalledOnce();
    expect(playerMocks.jumpTo).not.toHaveBeenCalled();
  });

  it("does not refresh playlists for an unlike", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    render(
      <QueuePanel
        open
        onClose={vi.fn()}
        onPlaylistsChanged={onPlaylistsChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Unlike" }));

    expect(apiMocks.setTrackLiked).toHaveBeenCalledWith("track-2", false);
    expect(onPlaylistsChanged).not.toHaveBeenCalled();
  });

  it("rolls back an optimistic like when the API rejects it", async () => {
    apiMocks.setTrackLiked.mockResolvedValue({
      success: false,
      message: "Master refused the change",
    });
    const user = userEvent.setup();
    render(<QueuePanel open onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Like" }));

    await waitFor(() => {
      expect(playerMocks.setTrackLikedInQueue).toHaveBeenNthCalledWith(
        2,
        "track-1",
        false,
      );
    });
    expect(toastMocks.show).toHaveBeenCalledWith(
      "Master refused the change",
      "err",
    );
  });

  it("rolls back an optimistic like when the request throws", async () => {
    apiMocks.setTrackLiked.mockRejectedValue(new Error("master offline"));
    const user = userEvent.setup();
    render(<QueuePanel open onClose={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Like" }));

    await waitFor(() => {
      expect(playerMocks.setTrackLikedInQueue).toHaveBeenNthCalledWith(
        2,
        "track-1",
        false,
      );
    });
    expect(toastMocks.show).toHaveBeenCalledWith(
      "Could not save like: Error: master offline",
      "err",
    );
  });

  it("lets keyboard users select a queued track", async () => {
    const user = userEvent.setup();
    render(<QueuePanel open onClose={vi.fn()} />);

    const trackButton = screen.getByRole("button", {
      name: "Play Second Song by Second Artist",
    });
    trackButton.focus();
    await user.keyboard("{Enter}");

    expect(playerMocks.jumpTo).toHaveBeenCalledWith(1);
  });

  it("keeps the empty queue actionable only through closing", () => {
    playerMocks.queue.splice(0);
    render(<QueuePanel open onClose={vi.fn()} />);

    expect(screen.getByText("Nothing queued")).toBeInTheDocument();
    expect(
      screen.getByText("Pick an album or song to start."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled();
  });

  it("acts as a compact drawer, closes with Escape, and restores focus", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 900,
    });
    const user = userEvent.setup();

    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open queue
          </button>
          <QueuePanel open={open} onClose={() => setOpen(false)} />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open queue" });
    await user.click(trigger);

    expect(
      screen.getByRole("dialog", { name: "Playback queue" }),
    ).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "Close queue" });
    await waitFor(() => expect(close).toHaveFocus());

    await user.keyboard("{Escape}");

    expect(
      screen.queryByRole("dialog", { name: "Playback queue" }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
