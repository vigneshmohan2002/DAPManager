import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Artist } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(
    (base: string, albumId: string) => `${base}/covers/${albumId}`,
  ),
  backendUrl: vi.fn(),
  fetchAlbums: vi.fn(),
  fetchArtists: vi.fn(),
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("./ArtistDetailScreen", () => ({
  default: ({
    artist,
    preloadedAlbums,
    preloadedBaseUrl,
  }: {
    artist: Artist;
    preloadedAlbums?: readonly { id: string }[];
    preloadedBaseUrl?: string;
  }) => (
    <section
      aria-label={`Artist detail ${artist.name}`}
      data-album-count={preloadedAlbums?.length ?? 0}
      data-base-url={preloadedBaseUrl ?? ""}
    >
      Selected artist: {artist.name}
    </section>
  ),
}));

import ArtistsScreen from "./ArtistsScreen";

const artists: Artist[] = [
  { name: "Massive Attack", album_count: 3, track_count: 31 },
  { name: "Little Simz", album_count: 5, track_count: 62 },
];

describe("ArtistsScreen", () => {
  beforeEach(() => {
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.fetchAlbums.mockResolvedValue([]);
    apiMocks.fetchArtists.mockResolvedValue(artists);
  });

  it("filters case-insensitively and opens the selected artist", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ArtistsScreen ready onOpen={onOpen} />);

    expect(
      await screen.findByRole("button", { name: "Open artist Massive Attack" }),
    ).toBeInTheDocument();
    expect(screen.getByText("2 artists")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox"), "SIMZ");

    expect(screen.getByText("1 of 2 artists")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open artist Massive Attack" }),
    ).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Open artist Little Simz" }),
    );
    expect(onOpen).toHaveBeenCalledWith(artists[1]);
  });

  it("selects an artist from the keyboard", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<ArtistsScreen ready onOpen={onOpen} />);

    const artistButton = await screen.findByRole("button", {
      name: "Open artist Massive Attack",
    });
    artistButton.focus();
    await user.keyboard("{Enter}");

    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith(artists[0]);
  });

  it("keeps the artist rail mounted while selection updates the detail pane", async () => {
    const user = userEvent.setup();

    function BrowserHarness() {
      const [selected, setSelected] = useState<Artist | null>(artists[0]);
      return (
        <ArtistsScreen
          ready
          selectedArtist={selected}
          onOpen={setSelected}
          onBack={() => setSelected(null)}
        />
      );
    }

    render(<BrowserHarness />);
    await screen.findByRole("button", {
      name: "Open artist Massive Attack",
    });

    expect(screen.getByLabelText("Artist browser")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Artist detail Massive Attack"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open artist Massive Attack" }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(
      screen.getByRole("button", { name: "Open artist Little Simz" }),
    );

    expect(screen.getByLabelText("Artist browser")).toBeInTheDocument();
    expect(
      screen.getByLabelText("Artist detail Little Simz"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open artist Little Simz" }),
    ).toHaveAttribute("aria-current", "page");

    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText("Choose an artist")).toBeInTheDocument();
    expect(screen.getByLabelText("Artist browser")).toBeInTheDocument();
  });

  it("derives lazy representative artwork through the proxied album URL", async () => {
    apiMocks.fetchAlbums.mockResolvedValue([
      {
        id: "massive-cover",
        title: "Blue Lines",
        artist: "Massive Attack",
        track_count: 9,
      },
    ]);
    const { container } = render(
      <ArtistsScreen
        ready
        selectedArtist={artists[0]}
        onOpen={vi.fn()}
      />,
    );

    await screen.findByRole("button", {
      name: "Open artist Massive Attack",
    });
    await waitFor(() =>
      expect(apiMocks.albumCoverUrl).toHaveBeenCalledWith(
        "http://localhost:5001",
        "massive-cover",
      ),
    );

    const artwork = container.querySelector<HTMLImageElement>(
      'img[src="http://localhost:5001/covers/massive-cover"]',
    );
    expect(artwork).not.toBeNull();
    expect(artwork).toHaveAttribute("loading", "lazy");
    expect(apiMocks.fetchAlbums).toHaveBeenCalledOnce();
    expect(
      screen.getByLabelText("Artist detail Massive Attack"),
    ).toHaveAttribute("data-album-count", "1");
    expect(
      screen.getByLabelText("Artist detail Massive Attack"),
    ).toHaveAttribute("data-base-url", "http://localhost:5001");
  });

  it("associates a credited track artist with the parent album and its artwork", async () => {
    const featuredArtist: Artist = {
      name: "2Pac featuring Big Syke",
      album_count: 0,
      track_count: 2,
    };
    apiMocks.fetchArtists.mockResolvedValue([featuredArtist]);
    apiMocks.fetchAlbums.mockResolvedValue([
      {
        id: "all-eyez-on-me",
        title: "All Eyez on Me",
        artist: "2Pac",
        track_count: 27,
        primary_artist: "2Pac",
        credited_artists: ["2Pac", featuredArtist.name],
      },
    ]);

    const { container } = render(
      <ArtistsScreen
        ready
        selectedArtist={featuredArtist}
        onOpen={vi.fn()}
      />,
    );

    await screen.findByRole("button", {
      name: `Open artist ${featuredArtist.name}`,
    });
    await waitFor(() =>
      expect(
        screen.getByLabelText(`Artist detail ${featuredArtist.name}`),
      ).toHaveAttribute("data-album-count", "1"),
    );

    expect(
      container.querySelector<HTMLImageElement>(
        'img[src="http://localhost:5001/covers/all-eyez-on-me"]',
      ),
    ).not.toBeNull();
  });

  it("does not load artists before setup is ready", () => {
    render(<ArtistsScreen ready={false} onOpen={vi.fn()} />);

    expect(apiMocks.fetchArtists).not.toHaveBeenCalled();
    expect(screen.getByText("Loading…")).toBeInTheDocument();
  });
});
