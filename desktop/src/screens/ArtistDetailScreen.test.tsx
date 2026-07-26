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
