import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Album, Artist, LibraryTrack, Track } from "../lib/api";

const apiMocks = vi.hoisted(() => ({
  albumCoverUrl: vi.fn(
    (base: string, albumId: string) => `${base}/covers/${albumId}`,
  ),
  backendUrl: vi.fn(),
  fetchAlbumTracks: vi.fn(),
  fetchAlbums: vi.fn(),
  fetchArtistInfo: vi.fn(),
  fetchArtistRadio: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  play: vi.fn(),
  playAlbum: vi.fn(),
  shuffle: false,
  toggleShuffle: vi.fn(),
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

const otherArtist: Artist = {
  name: "Portishead",
  album_count: 1,
  track_count: 11,
};

const featuredArtist: Artist = {
  name: "2Pac featuring Big Syke",
  album_count: 0,
  track_count: 2,
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function embeddedProps(
  selectedArtist: Artist = artist,
  selectedAlbums: readonly Album[] = albums.filter(
    (album) => album.artist === selectedArtist.name,
  ),
) {
  return {
    artist: selectedArtist,
    embedded: true,
    onBack: vi.fn(),
    onOpenAlbum: vi.fn(),
    preloadedAlbums: selectedAlbums,
    preloadedBaseUrl: "http://localhost:5001",
    preloadedLoading: false,
    preloadedError: null,
  } as const;
}

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
    playerMocks.shuffle = false;
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.fetchAlbums.mockResolvedValue(albums);
    apiMocks.fetchAlbumTracks.mockImplementation(async (albumId: string) => [
      {
        mbid: `${albumId}-track`,
        title: `${albumId} song`,
        artist: artist.name,
        album: albums.find((album) => album.id === albumId)?.title ?? null,
        track_number: 1,
        disc_number: 1,
        is_liked: false,
      },
    ]);
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
    expect(playerMocks.playAlbum).toHaveBeenCalledOnce();
    expect(props.onOpenAlbum).not.toHaveBeenCalled();
  });

  it("renders as an embedded detail pane with its own album filter", async () => {
    render(<ArtistDetailScreen {...embeddedProps()} />);

    expect(
      await screen.findByRole("searchbox", {
        name: "Search Massive Attack albums",
      }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Open Blue Lines by Massive Attack",
      }),
    ).toBeInTheDocument();
    expect(apiMocks.fetchAlbums).not.toHaveBeenCalled();
  });

  it("plays every artist album in deterministic album order", async () => {
    const user = userEvent.setup();
    render(<ArtistDetailScreen {...embeddedProps()} />);

    await user.click(
      await screen.findByRole("button", {
        name: "Play all Massive Attack",
      }),
    );

    expect(apiMocks.fetchAlbumTracks).toHaveBeenNthCalledWith(1, "album-1");
    expect(apiMocks.fetchAlbumTracks).toHaveBeenNthCalledWith(2, "album-2");
    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        expect.objectContaining({
          mbid: "album-1-track",
          albumId: "album-1",
        }),
        expect.objectContaining({
          mbid: "album-2-track",
          albumId: "album-2",
        }),
      ],
      0,
    );
  });

  it("shows a credited parent album and queues only the selected artist's tracks", async () => {
    const user = userEvent.setup();
    const parentAlbum: Album = {
      id: "all-eyez-on-me",
      title: "All Eyez on Me",
      artist: featuredArtist.name,
      track_count: 4,
      primary_artist: "2Pac",
      credited_artists: ["2Pac", featuredArtist.name],
    };
    apiMocks.fetchAlbumTracks.mockResolvedValue([
      {
        mbid: "primary-track",
        title: "Primary",
        artist: "2Pac",
        album: parentAlbum.title,
        track_number: 1,
        disc_number: 1,
      },
      {
        mbid: "featured-track-1",
        title: "Featured One",
        artist: featuredArtist.name,
        album: parentAlbum.title,
        track_number: 2,
        disc_number: 1,
      },
      {
        mbid: "other-feature",
        title: "Other Feature",
        artist: "2Pac featuring Method Man",
        album: parentAlbum.title,
        track_number: 3,
        disc_number: 1,
      },
      {
        mbid: "featured-track-2",
        title: "Featured Two",
        artist: featuredArtist.name,
        album: parentAlbum.title,
        track_number: 4,
        disc_number: 1,
      },
    ]);

    render(
      <ArtistDetailScreen
        {...embeddedProps(featuredArtist, [parentAlbum])}
      />,
    );

    expect(
      screen.getByRole("button", {
        name: `Open ${parentAlbum.title} by ${parentAlbum.primary_artist}`,
      }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: `Play all ${featuredArtist.name}`,
      }),
    );

    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        expect.objectContaining({
          mbid: "featured-track-1",
          albumId: parentAlbum.id,
        }),
        expect.objectContaining({
          mbid: "featured-track-2",
          albumId: parentAlbum.id,
        }),
      ],
      0,
    );
  });

  it("keeps credited album double-click as full-album playback", async () => {
    const parentAlbum: Album = {
      id: "all-eyez-on-me",
      title: "All Eyez on Me",
      artist: featuredArtist.name,
      track_count: 2,
      primary_artist: "2Pac",
      credited_artists: ["2Pac", featuredArtist.name],
    };
    const onOpenAlbum = vi.fn();
    render(
      <ArtistDetailScreen
        {...embeddedProps(featuredArtist, [parentAlbum])}
        onOpenAlbum={onOpenAlbum}
      />,
    );

    fireEvent.doubleClick(
      screen.getByRole("button", {
        name: `Open ${parentAlbum.title} by ${parentAlbum.primary_artist}`,
      }),
    );

    expect(playerMocks.playAlbum).toHaveBeenCalledOnce();
    expect(playerMocks.playAlbum).toHaveBeenCalledWith(parentAlbum.id);
    expect(apiMocks.fetchAlbumTracks).not.toHaveBeenCalled();
    expect(onOpenAlbum).not.toHaveBeenCalled();
  });

  it("keeps every track when the selected artist is the album's primary artist", async () => {
    const user = userEvent.setup();
    const parentAlbum: Album = {
      id: "all-eyez-on-me",
      title: "All Eyez on Me",
      artist: featuredArtist.name,
      track_count: 2,
      primary_artist: "2Pac",
      credited_artists: ["2Pac", featuredArtist.name],
    };
    const primaryArtist: Artist = {
      name: "2Pac",
      album_count: 1,
      track_count: 2,
    };
    apiMocks.fetchAlbumTracks.mockResolvedValue([
      {
        mbid: "primary-track",
        title: "Primary",
        artist: primaryArtist.name,
        album: parentAlbum.title,
        track_number: 1,
        disc_number: 1,
      },
      {
        mbid: "featured-track",
        title: "Featured",
        artist: featuredArtist.name,
        album: parentAlbum.title,
        track_number: 2,
        disc_number: 1,
      },
    ]);

    render(
      <ArtistDetailScreen
        {...embeddedProps(primaryArtist, [parentAlbum])}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "Play all 2Pac" }),
    );

    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        expect.objectContaining({ mbid: "primary-track" }),
        expect.objectContaining({ mbid: "featured-track" }),
      ],
      0,
    );
  });

  it.each([
    {
      caseName: "an explicitly tied primary credit",
      primaryArtist: null,
    },
    {
      caseName: "credit-aware data with no primary field",
      primaryArtist: undefined,
    },
  ])(
    "filters exact tracks for $caseName",
    async ({ primaryArtist }) => {
      const user = userEvent.setup();
      const primaryArtistName = "2Pac";
      const parentAlbum: Album = {
        id: "all-eyez-on-me",
        title: "All Eyez on Me",
        artist: primaryArtistName,
        track_count: 2,
        ...(primaryArtist === undefined
          ? {}
          : { primary_artist: primaryArtist }),
        credited_artists: [primaryArtistName, featuredArtist.name],
      };
      apiMocks.fetchAlbumTracks.mockResolvedValue([
        {
          mbid: "primary-track",
          title: "Primary",
          artist: primaryArtistName,
          album: parentAlbum.title,
          track_number: 1,
          disc_number: 1,
        },
        {
          mbid: "featured-track",
          title: "Featured",
          artist: featuredArtist.name,
          album: parentAlbum.title,
          track_number: 2,
          disc_number: 1,
        },
      ]);

      render(
        <ArtistDetailScreen
          {...embeddedProps(
            {
              name: primaryArtistName,
              album_count: 1,
              track_count: 2,
            },
            [parentAlbum],
          )}
        />,
      );
      await user.click(
        screen.getByRole("button", {
          name: `Play all ${primaryArtistName}`,
        }),
      );

      expect(playerMocks.play).toHaveBeenCalledWith(
        [expect.objectContaining({ mbid: "primary-track" })],
        0,
      );
    },
  );

  it("preserves full-album playback for a legacy response without credit fields", async () => {
    const user = userEvent.setup();
    const primaryArtistName = "2Pac";
    const legacyAlbum: Album = {
      id: "legacy-all-eyez-on-me",
      title: "All Eyez on Me",
      artist: primaryArtistName,
      track_count: 2,
    };
    apiMocks.fetchAlbumTracks.mockResolvedValue([
      {
        mbid: "primary-track",
        title: "Primary",
        artist: primaryArtistName,
        album: legacyAlbum.title,
        track_number: 1,
        disc_number: 1,
      },
      {
        mbid: "featured-track",
        title: "Featured",
        artist: featuredArtist.name,
        album: legacyAlbum.title,
        track_number: 2,
        disc_number: 1,
      },
    ]);

    render(
      <ArtistDetailScreen
        {...embeddedProps(
          {
            name: primaryArtistName,
            album_count: 1,
            track_count: 2,
          },
          [legacyAlbum],
        )}
      />,
    );
    await user.click(
      screen.getByRole("button", {
        name: `Play all ${primaryArtistName}`,
      }),
    );

    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        expect.objectContaining({ mbid: "primary-track" }),
        expect.objectContaining({ mbid: "featured-track" }),
      ],
      0,
    );
  });

  it("associates credited albums when loaded in standalone mode", async () => {
    const parentAlbum: Album = {
      id: "all-eyez-on-me",
      title: "All Eyez on Me",
      artist: "2Pac",
      track_count: 2,
      credited_artists: ["2Pac", featuredArtist.name],
    };
    apiMocks.fetchAlbums.mockResolvedValue([parentAlbum]);

    render(
      <ArtistDetailScreen
        artist={featuredArtist}
        onBack={vi.fn()}
        onOpenAlbum={vi.fn()}
      />,
    );

    expect(
      await screen.findByRole("button", {
        name: `Open ${parentAlbum.title} by ${parentAlbum.artist}`,
      }),
    ).toBeInTheDocument();
  });

  it("keeps successful albums in order when one album fails to load", async () => {
    const user = userEvent.setup();
    const thirdAlbum: Album = {
      id: "album-3",
      title: "Collected",
      artist: artist.name,
      track_count: 7,
    };
    apiMocks.fetchAlbumTracks.mockImplementation(async (albumId: string) => {
      if (albumId === "album-2") throw new Error("offline");
      return [
        {
          mbid: `${albumId}-track`,
          title: `${albumId} song`,
          artist: artist.name,
          album: albumId,
          track_number: 1,
          disc_number: 1,
          is_liked: false,
        },
      ];
    });
    render(
      <ArtistDetailScreen
        {...embeddedProps(artist, [albums[0], albums[1], thirdAlbum])}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Play all Massive Attack" }),
    );

    expect(playerMocks.play).toHaveBeenCalledWith(
      [
        expect.objectContaining({
          mbid: "album-1-track",
          albumId: "album-1",
        }),
        expect.objectContaining({
          mbid: "album-3-track",
          albumId: "album-3",
        }),
      ],
      0,
    );
    expect(toastMocks.show).toHaveBeenCalledWith(
      "Playing available tracks; skipped 1 album that could not be loaded.",
    );
  });

  it("bounds album-track requests while preserving queue order", async () => {
    const user = userEvent.setup();
    const fiveAlbums: Album[] = Array.from({ length: 5 }, (_, index) => ({
      id: `bounded-${index + 1}`,
      title: `Album ${index + 1}`,
      artist: artist.name,
      track_count: 1,
    }));
    const requests = new Map<string, ReturnType<typeof deferred<Track[]>>>();
    apiMocks.fetchAlbumTracks.mockImplementation((albumId: string) => {
      const request = deferred<Track[]>();
      requests.set(albumId, request);
      return request.promise;
    });
    render(
      <ArtistDetailScreen
        {...embeddedProps(artist, fiveAlbums)}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Play all Massive Attack" }),
    );
    expect(apiMocks.fetchAlbumTracks).toHaveBeenCalledTimes(4);

    await act(async () => {
      requests.get("bounded-1")?.resolve([
        {
          mbid: "bounded-1-track",
          title: "Track 1",
          artist: artist.name,
          album: "Album 1",
          track_number: 1,
          disc_number: 1,
        },
      ]);
      await requests.get("bounded-1")?.promise;
    });
    await waitFor(() =>
      expect(apiMocks.fetchAlbumTracks).toHaveBeenCalledTimes(5),
    );

    await act(async () => {
      for (let index = 2; index <= 5; index += 1) {
        requests.get(`bounded-${index}`)?.resolve([
          {
            mbid: `bounded-${index}-track`,
            title: `Track ${index}`,
            artist: artist.name,
            album: `Album ${index}`,
            track_number: 1,
            disc_number: 1,
          },
        ]);
      }
      await Promise.all([...requests.values()].map((request) => request.promise));
    });

    await waitFor(() => expect(playerMocks.play).toHaveBeenCalledOnce());
    const [queue] = playerMocks.play.mock.calls[0] as [
      Array<Track & { albumId: string }>,
      number,
    ];
    expect(queue.map((track) => track.albumId)).toEqual(
      fiveAlbums.map((album) => album.id),
    );
  });

  it.each([
    {
      action: "Shuffle all Massive Attack",
      initiallyShuffled: false,
      expectedToggles: 1,
    },
    {
      action: "Shuffle all Massive Attack",
      initiallyShuffled: true,
      expectedToggles: 0,
    },
    {
      action: "Play all Massive Attack",
      initiallyShuffled: true,
      expectedToggles: 1,
    },
    {
      action: "Play all Massive Attack",
      initiallyShuffled: false,
      expectedToggles: 0,
    },
  ])(
    "aligns shuffle for $action from $initiallyShuffled",
    async ({ action, initiallyShuffled, expectedToggles }) => {
      playerMocks.shuffle = initiallyShuffled;
      const user = userEvent.setup();
      render(
        <ArtistDetailScreen
          {...embeddedProps(artist, [albums[0]])}
        />,
      );

      await user.click(screen.getByRole("button", { name: action }));
      await waitFor(() => expect(playerMocks.play).toHaveBeenCalledOnce());
      expect(playerMocks.toggleShuffle).toHaveBeenCalledTimes(expectedToggles);
    },
  );

  it("discards a stale Play All completion after the artist changes", async () => {
    const pending = deferred<Track[]>();
    apiMocks.fetchAlbumTracks.mockReturnValueOnce(pending.promise);
    const user = userEvent.setup();
    const { rerender } = render(
      <ArtistDetailScreen
        {...embeddedProps(artist, [albums[0]])}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Play all Massive Attack" }),
    );
    rerender(
      <ArtistDetailScreen
        {...embeddedProps(otherArtist, [albums[2]])}
      />,
    );
    await act(async () => {
      pending.resolve([
        {
          mbid: "stale-track",
          title: "Stale",
          artist: artist.name,
          album: albums[0].title,
          track_number: 1,
          disc_number: 1,
        },
      ]);
      await pending.promise;
    });

    expect(playerMocks.play).not.toHaveBeenCalled();
    expect(toastMocks.show).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Play all Portishead" }),
    ).toBeEnabled();
  });

  it("discards a stale radio completion after the artist changes", async () => {
    const pending = deferred<{
      tracks: LibraryTrack[];
      top_tag: string | null;
      seed_count: number;
      related_count: number;
    }>();
    apiMocks.fetchArtistRadio.mockReturnValueOnce(pending.promise);
    const user = userEvent.setup();
    const { rerender } = render(
      <ArtistDetailScreen
        {...embeddedProps(artist, [albums[0]])}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Start Massive Attack radio" }),
    );
    rerender(
      <ArtistDetailScreen
        {...embeddedProps(otherArtist, [albums[2]])}
      />,
    );
    await act(async () => {
      pending.resolve({
        tracks: [radioTrack],
        top_tag: "trip-hop",
        seed_count: 1,
        related_count: 3,
      });
      await pending.promise;
    });

    expect(playerMocks.play).not.toHaveBeenCalled();
    expect(toastMocks.show).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Start Portishead radio" }),
    ).toBeEnabled();
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
