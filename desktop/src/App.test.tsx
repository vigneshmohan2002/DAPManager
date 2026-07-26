import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const apiMocks = vi.hoisted(() => ({
  waitForBackend: vi.fn(),
  fetchSetupStatus: vi.fn(),
}));

vi.mock("./lib/api", () => ({
  waitForBackend: apiMocks.waitForBackend,
  fetchSetupStatus: apiMocks.fetchSetupStatus,
}));

vi.mock("./player/PlayerContext", () => ({
  PlayerProvider: ({ children }: { children: ReactNode }) => children,
}));

vi.mock("./components/Sidebar", () => ({
  default: ({
    activeId,
    onSelect,
  }: {
    activeId: string;
    onSelect: (id: string) => void;
  }) => (
    <nav data-testid="sidebar" data-active-id={activeId}>
      <button onClick={() => onSelect("playlist:road/trip?2026")}>Playlist</button>
      <button onClick={() => onSelect("songs")}>All songs</button>
      <button onClick={() => onSelect("artists")}>Artists</button>
      <button onClick={() => onSelect("settings")}>Settings</button>
      <button onClick={() => onSelect("future-screen")}>Future screen</button>
    </nav>
  ),
}));

vi.mock("./screens/HomeScreen", () => ({
  default: ({
    onOpenAlbum,
    onOpenArtist,
  }: {
    onOpenAlbum: (album: {
      id: string;
      title: string;
      artist: string;
      track_count: number;
    }) => void;
    onOpenArtist: (artist: {
      name: string;
      album_count: number;
      track_count: number;
    }) => void;
  }) => (
    <div data-testid="home-screen">
      Home
      <button
        onClick={() =>
          onOpenAlbum({
            id: "release-1",
            title: "First Album",
            artist: "Example Artist",
            track_count: 9,
          })
        }
      >
        Open album
      </button>
      <button
        onClick={() =>
          onOpenArtist({
            name: "Example Artist",
            album_count: 2,
            track_count: 18,
          })
        }
      >
        Open artist
      </button>
    </div>
  ),
}));

vi.mock("./screens/SongsScreen", () => ({
  default: ({
    playlistId,
    onOpenSettings,
  }: {
    playlistId?: string | null;
    onOpenSettings: (focusKey?: string) => void;
  }) => (
    <div data-testid="songs-screen" data-playlist-id={playlistId ?? "none"}>
      Songs
      <button onClick={() => onOpenSettings("acoustid_api_key")}>
        Configure tagging
      </button>
    </div>
  ),
}));

vi.mock("./screens/ArtistsScreen", () => ({
  default: () => <div data-testid="artists-screen">Artist list</div>,
}));

vi.mock("./screens/ArtistDetailScreen", () => ({
  default: ({
    artist,
    onBack,
    onOpenAlbum,
  }: {
    artist: { name: string };
    onBack: () => void;
    onOpenAlbum: (album: {
      id: string;
      title: string;
      artist: string;
      track_count: number;
    }) => void;
  }) => (
    <div data-testid="artist-detail" data-artist={artist.name}>
      <button onClick={onBack}>Back to artists</button>
      <button
        onClick={() =>
          onOpenAlbum({
            id: "release-2",
            title: "Second Album",
            artist: artist.name,
            track_count: 11,
          })
        }
      >
        Open artist album
      </button>
    </div>
  ),
}));

vi.mock("./screens/AlbumDetailScreen", () => ({
  default: ({
    album,
    onBack,
  }: {
    album: { id: string; title: string };
    onBack: () => void;
  }) => (
    <div data-testid="album-detail" data-album-id={album.id}>
      {album.title}
      <button onClick={onBack}>Back from album</button>
    </div>
  ),
}));

vi.mock("./screens/SettingsScreen", () => ({
  default: ({
    focusKey,
    onConsumedFocusKey,
  }: {
    focusKey: string | null;
    onConsumedFocusKey: () => void;
  }) => (
    <div data-testid="settings-screen" data-focus-key={focusKey ?? "none"}>
      Settings
      <button onClick={onConsumedFocusKey}>Consume focus</button>
    </div>
  ),
}));

vi.mock("./screens/SetupScreen", () => ({
  default: () => <div data-testid="setup-screen">Setup</div>,
}));

vi.mock("./components/MiniPlayer", () => ({
  default: () => <div data-testid="mini-player">Mini player</div>,
}));

vi.mock("./components/PlayerBar", () => ({
  default: ({
    queueOpen,
    onToggleQueue,
    lyricsOpen,
    onToggleLyrics,
  }: {
    queueOpen: boolean;
    onToggleQueue: () => void;
    lyricsOpen: boolean;
    onToggleLyrics: () => void;
  }) => (
    <div>
      <button onClick={onToggleQueue}>
        {queueOpen ? "Hide test queue" : "Show test queue"}
      </button>
      <button onClick={onToggleLyrics}>
        {lyricsOpen ? "Hide test lyrics" : "Show test lyrics"}
      </button>
    </div>
  ),
}));
vi.mock("./components/LyricsPane", () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="lyrics-pane">Lyrics</div> : null,
}));
vi.mock("./components/QueuePanel", () => ({
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="queue-panel">Queue</div> : null,
}));
vi.mock("./components/SearchOverlay", () => ({ default: () => null }));

describe("App navigation contract", () => {
  beforeEach(() => {
    apiMocks.waitForBackend.mockResolvedValue({ ok: true });
    apiMocks.fetchSetupStatus.mockResolvedValue({ needs_setup: false });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 768 });
  });

  it("decodes playlist:<id> navigation without altering the playlist id", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");

    fireEvent.click(screen.getByRole("button", { name: "Playlist" }));

    expect(screen.getByTestId("songs-screen")).toHaveAttribute(
      "data-playlist-id",
      "road/trip?2026",
    );
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "playlist:road/trip?2026",
    );
  });

  it("clears playlist scope when navigating to the static songs id", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");
    fireEvent.click(screen.getByRole("button", { name: "Playlist" }));
    await waitFor(() =>
      expect(screen.getByTestId("songs-screen")).toHaveAttribute(
        "data-playlist-id",
        "road/trip?2026",
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "All songs" }));

    expect(screen.getByTestId("songs-screen")).toHaveAttribute(
      "data-playlist-id",
      "none",
    );
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "songs",
    );
  });

  it("returns from an album to the screen that opened it", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");

    fireEvent.click(screen.getByRole("button", { name: "Open album" }));
    expect(screen.getByTestId("album-detail")).toHaveAttribute(
      "data-album-id",
      "release-1",
    );
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "home",
    );

    fireEvent.click(screen.getByRole("button", { name: "Back from album" }));
    expect(screen.getByTestId("home-screen")).toBeInTheDocument();
  });

  it("returns from an artist album to the same artist detail", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");
    fireEvent.click(screen.getByRole("button", { name: "Open artist" }));

    expect(screen.getByTestId("artist-detail")).toHaveAttribute(
      "data-artist",
      "Example Artist",
    );
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "artists",
    );

    fireEvent.click(screen.getByRole("button", { name: "Open artist album" }));
    expect(screen.getByTestId("album-detail")).toHaveAttribute(
      "data-album-id",
      "release-2",
    );
    fireEvent.click(screen.getByRole("button", { name: "Back from album" }));

    expect(screen.getByTestId("artist-detail")).toHaveAttribute(
      "data-artist",
      "Example Artist",
    );
  });

  it("keeps settings focus attached to the settings route until consumed", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");
    fireEvent.click(screen.getByRole("button", { name: "All songs" }));
    fireEvent.click(screen.getByRole("button", { name: "Configure tagging" }));

    expect(screen.getByTestId("settings-screen")).toHaveAttribute(
      "data-focus-key",
      "acoustid_api_key",
    );
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "settings",
    );

    fireEvent.click(screen.getByRole("button", { name: "Consume focus" }));
    expect(screen.getByTestId("settings-screen")).toHaveAttribute(
      "data-focus-key",
      "none",
    );
  });

  it("retains the unknown-screen fallback for unrecognised sidebar ids", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");

    fireEvent.click(screen.getByRole("button", { name: "Future screen" }));

    expect(screen.getByRole("heading", { name: "future-screen" })).toBeInTheDocument();
    expect(screen.getByText("Unknown screen")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar")).toHaveAttribute(
      "data-active-id",
      "future-screen",
    );
  });

  it("shows setup instead of mini-player on a fresh small-window launch", async () => {
    apiMocks.fetchSetupStatus.mockResolvedValue({ needs_setup: true });
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 200 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 200 });

    render(<App />);

    expect(await screen.findByTestId("setup-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("mini-player")).not.toBeInTheDocument();
  });

  it("shows the mini-player after a successful small-window launch", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 200 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 200 });

    render(<App />);

    expect(await screen.findByTestId("mini-player")).toBeInTheDocument();
    expect(screen.queryByTestId("sidebar")).not.toBeInTheDocument();
  });

  it("keeps the queue and lyrics rails mutually exclusive", async () => {
    render(<App />);
    await screen.findByTestId("home-screen");

    fireEvent.click(screen.getByRole("button", { name: "Show test queue" }));
    expect(screen.getByTestId("queue-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("lyrics-pane")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show test lyrics" }));
    expect(screen.getByTestId("lyrics-pane")).toBeInTheDocument();
    expect(screen.queryByTestId("queue-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show test queue" }));
    expect(screen.getByTestId("queue-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("lyrics-pane")).not.toBeInTheDocument();
  });
});
