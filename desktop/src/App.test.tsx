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
    </nav>
  ),
}));

vi.mock("./screens/HomeScreen", () => ({
  default: () => <div data-testid="home-screen">Home</div>,
}));

vi.mock("./screens/SongsScreen", () => ({
  default: ({ playlistId }: { playlistId?: string | null }) => (
    <div data-testid="songs-screen" data-playlist-id={playlistId ?? "none"}>
      Songs
    </div>
  ),
}));

vi.mock("./components/PlayerBar", () => ({ default: () => null }));
vi.mock("./components/LyricsPane", () => ({ default: () => null }));
vi.mock("./components/QueuePanel", () => ({ default: () => null }));
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
});
