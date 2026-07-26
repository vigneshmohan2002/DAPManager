import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchLyrics: vi.fn(),
  saveLyrics: vi.fn(),
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
  position: 15,
  seek: vi.fn(),
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => playerMocks,
}));

import LyricsPane from "./LyricsPane";

describe("LyricsPane", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1200,
    });
    playerMocks.current = {
      mbid: "track-1",
      title: "First Light",
      artist: "The Testers",
      album: "A Test Album",
      track_number: 1,
      disc_number: 1,
      albumId: "album-1",
    };
    playerMocks.position = 15;
    Element.prototype.scrollIntoView = vi.fn();
    apiMocks.fetchLyrics.mockResolvedValue({
      lrc: "[00:10.00]First line\n[00:20.00]Second line",
      synced: true,
      source: "lrclib",
      fetched_at: "2026-07-26T20:00:00Z",
    });
  });

  it("does not fetch while closed", () => {
    render(<LyricsPane open={false} onClose={vi.fn()} />);

    expect(apiMocks.fetchLyrics).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("complementary", { name: "Lyrics" }),
    ).not.toBeInTheDocument();
  });

  it("renders synced lyrics and seeks when a line is selected", async () => {
    const user = userEvent.setup();
    render(<LyricsPane open onClose={vi.fn()} />);

    await user.click(await screen.findByText("Second line"));

    expect(apiMocks.fetchLyrics).toHaveBeenCalledWith("track-1");
    expect(playerMocks.seek).toHaveBeenCalledWith(20);
  });

  it("lets keyboard users seek to a timed line", async () => {
    const user = userEvent.setup();
    render(<LyricsPane open onClose={vi.fn()} />);
    const line = await screen.findByRole("button", {
      name: "Seek to 0:20",
    });

    line.focus();
    await user.keyboard("{Enter}");

    expect(playerMocks.seek).toHaveBeenCalledWith(20);
  });

  it("saves a manual lyric edit without changing the public panel contract", async () => {
    apiMocks.saveLyrics.mockResolvedValue({
      lrc: "Replacement lyrics",
      synced: false,
      source: "manual",
      fetched_at: "2026-07-26T20:01:00Z",
    });
    const user = userEvent.setup();
    render(<LyricsPane open onClose={vi.fn()} />);

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const editor = screen.getByPlaceholderText("Lyrics here…");
    await user.clear(editor);
    await user.type(editor, "Replacement lyrics");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(apiMocks.saveLyrics).toHaveBeenCalledWith(
      "track-1",
      "Replacement lyrics",
      true,
    );
    expect(await screen.findByText("Replacement lyrics")).toBeInTheDocument();
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
            Open lyrics
          </button>
          <LyricsPane open={open} onClose={() => setOpen(false)} />
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "Open lyrics" });
    await user.click(trigger);

    expect(
      screen.getByRole("dialog", { name: "Lyrics" }),
    ).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "Close lyrics" });
    await waitFor(() => expect(close).toHaveFocus());

    await user.keyboard("{Escape}");

    expect(
      screen.queryByRole("dialog", { name: "Lyrics" }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
