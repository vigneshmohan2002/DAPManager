import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Album, Artist, LibraryTrack } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(
    (base: string, albumId: string) => `${base}/covers/${albumId}`,
  ),
  backendUrl: vi.fn(),
  fetchAlbums: vi.fn(),
  fetchArtistInfo: vi.fn(),
  fetchArtistRadio: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  play: vi.fn(),
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

import ArtistDetailScreen from "./ArtistDetailScreen";

const artist: Artist = {
  name: "Massive Attack",
  album_count: 2,
  track_count: 20,
};

const albums: Album[] = [
  {
    id: "album-1",
    title: "Blue Lines",
    artist: "Massive Attack",
    track_count: 9,
  },
  {
    id: "album-2",
    title: "Mezzanine",
    artist: "Massive Attack",
    track_count: 11,
  },
  {
    id: "other-1",
    title: "Dummy",
    artist: "Portishead",
    track_count: 11,
  },
];

const radioTrack: LibraryTrack = {
  mbid: "track-1",
  title: "Angel",
  artist: "Massive Attack",
  album: "Mezzanine",
  album_id: "album-2",
  availability: "remote",
  disc_number: 1,
  track_number: 1,
  is_liked: false,
};

function renderArtist() {
  const props = {
    artist,
    onBack: vi.fn(),
    onOpenAlbum: vi.fn(),
  };
  render(<ArtistDetailScreen {...props} />);
  return props;
}

describe("ArtistDetailScreen", () => {
  beforeEach(() => {
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.fetchAlbums.mockResolvedValue(albums);
    apiMocks.fetchArtistInfo.mockResolvedValue({
      title: artist.name,
      summary: "An English electronic music group.",
      image_url: "https://example.com/artist.jpg",
      source_url: "https://example.com/artist",
    });
    apiMocks.fetchArtistRadio.mockResolvedValue({
      tracks: [radioTrack],
      top_tag: "trip-hop",
      seed_count: 1,
      related_count: 3,
    });
    playerMocks.playAlbum.mockResolvedValue(9);
  });

  it("keeps back navigation and only shows this artist's albums", async () => {
    const user = userEvent.setup();
    const props = renderArtist();

    expect(
      await screen.findByRole("button", {
        name: "Open Blue Lines by Massive Attack",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Open Mezzanine by Massive Attack",
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open Dummy by Portishead" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("An English electronic music group."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(props.onBack).toHaveBeenCalledOnce();
  });

  it("plays an album from its beginning on double-click without opening it", async () => {
    const props = renderArtist();
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

  it("maps artist radio tracks into the player queue", async () => {
    const user = userEvent.setup();
    renderArtist();

    await user.click(
      await screen.findByRole("button", {
        name: "Start Massive Attack radio",
      }),
    );

    expect(apiMocks.fetchArtistRadio).toHaveBeenCalledWith("Massive Attack");
    expect(playerMocks.play).toHaveBeenCalledWith(
      [{ ...radioTrack, albumId: "album-2" }],
      0,
    );
    expect(toastMocks.show).toHaveBeenCalledWith(
      "Radio: Massive Attack · 3 related (trip-hop)",
    );
  });
});
