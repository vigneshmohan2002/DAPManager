import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  fetchAlbums: vi.fn(),
  fetchArtists: vi.fn(),
  searchTracks: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  play: vi.fn(),
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => playerMocks,
}));

import SearchOverlay from "./SearchOverlay";

function SearchHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open search
      </button>
      <SearchOverlay
        open={open}
        onClose={() => setOpen(false)}
        onOpenAlbum={vi.fn()}
        onOpenArtist={vi.fn()}
      />
    </>
  );
}

function renderSearch(
  props: Partial<React.ComponentProps<typeof SearchOverlay>> = {},
) {
  const onClose = vi.fn();
  const onOpenAlbum = vi.fn();
  const onOpenArtist = vi.fn();
  render(
    <SearchOverlay
      open
      onClose={onClose}
      onOpenAlbum={onOpenAlbum}
      onOpenArtist={onOpenArtist}
      {...props}
    />,
  );
  return { onClose, onOpenAlbum, onOpenArtist };
}

describe("SearchOverlay", () => {
  beforeEach(() => {
    apiMocks.fetchAlbums.mockResolvedValue([
      {
        id: "album-1",
        title: "Blue Lines",
        artist: "Massive Attack",
        track_count: 9,
      },
    ]);
    apiMocks.fetchArtists.mockResolvedValue([
      { name: "Massive Attack", album_count: 2, track_count: 20 },
    ]);
    apiMocks.searchTracks.mockResolvedValue([]);
  });

  it("loads library lookups and routes album and artist results", async () => {
    const user = userEvent.setup();
    const { onClose, onOpenAlbum, onOpenArtist } = renderSearch();
    const search = screen.getByRole("textbox", { name: "Search your library" });

    await user.type(search, "massive");
    await waitFor(() => {
      expect(screen.getAllByText("Massive Attack")).toHaveLength(2);
    });
    await user.click(
      screen.getAllByText("Massive Attack")[0].closest("button")!,
    );
    expect(onOpenArtist).toHaveBeenCalledWith({
      name: "Massive Attack",
      album_count: 2,
      track_count: 20,
    });
    expect(onClose).toHaveBeenCalledOnce();

    await user.clear(search);
    await user.type(search, "blue");
    await user.click(await screen.findByText("Blue Lines"));
    expect(onOpenAlbum).toHaveBeenCalledWith({
      id: "album-1",
      title: "Blue Lines",
      artist: "Massive Attack",
      track_count: 9,
    });
  });

  it("plays a playable track result and closes", async () => {
    apiMocks.searchTracks.mockResolvedValue([
      {
        mbid: "track-1",
        title: "Unfinished Sympathy",
        artist: "Massive Attack",
        album: "Blue Lines",
        path: "/music/track.flac",
      },
    ]);
    const user = userEvent.setup();
    const { onClose } = renderSearch();

    await user.type(
      screen.getByRole("textbox", { name: "Search your library" }),
      "unfinished",
    );
    await user.click(await screen.findByText("Unfinished Sympathy"));

    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        {
          mbid: "track-1",
          title: "Unfinished Sympathy",
          artist: "Massive Attack",
          album: "Blue Lines",
          track_number: null,
          disc_number: null,
          albumId: null,
        },
      ],
      0,
    );
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes with Escape", () => {
    const { onClose } = renderSearch();

    fireEvent.keyDown(
      screen.getByRole("textbox", { name: "Search your library" }),
      { key: "Escape" },
    );

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("traps focus inside the modal and restores the opening control", async () => {
    const user = userEvent.setup();
    render(<SearchHarness />);
    const opener = screen.getByRole("button", { name: "Open search" });

    await user.click(opener);
    const input = screen.getByRole("textbox", {
      name: "Search your library",
    });
    await waitFor(() => expect(input).toHaveFocus());

    const close = screen.getByRole("button", { name: "Close search" });
    const controls = Array.from(
      screen
        .getByRole("dialog", { name: "Search your library" })
        .querySelectorAll<HTMLElement>("*"),
    ).filter((element) =>
      element.matches(
        'button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(controls).toEqual([input, close]);
    fireEvent.keyDown(input, { key: "Tab", shiftKey: true });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab" });
    expect(input).toHaveFocus();

    await user.click(close);
    expect(opener).toHaveFocus();
  });
});
