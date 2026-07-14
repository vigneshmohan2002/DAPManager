import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  PlayerProvider,
  usePlayer,
  type PlayerTrack,
} from "./PlayerContext";

const apiMocks = vi.hoisted(() => ({
  fetchAlbumTracks: vi.fn(),
  recordPlay: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  albumCoverUrl: (base: string, albumId: string) => `${base}/cover/${albumId}`,
  backendUrl: vi.fn().mockResolvedValue("http://localhost:5001"),
  fetchAlbumTracks: apiMocks.fetchAlbumTracks,
  recordPlay: apiMocks.recordPlay,
  streamUrl: (base: string, mbid: string) => `${base}/stream/${mbid}`,
}));

class FakeAudio {
  preload = "";
  src = "";
  currentTime = 0;
  duration = 0;
  paused = true;
  private listeners = new Map<string, Set<EventListener>>();

  play = vi.fn(async () => {
    this.paused = false;
  });

  pause = vi.fn(() => {
    this.paused = true;
  });

  load = vi.fn();

  removeAttribute = vi.fn((name: string) => {
    if (name === "src") this.src = "";
  });

  addEventListener(type: string, listener: EventListener): void {
    const listeners = this.listeners.get(type) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }
}

const queue: PlayerTrack[] = [
  {
    mbid: "track-1",
    title: "One",
    artist: "Artist",
    album: "Album",
    albumId: "album-1",
    track_number: 1,
    disc_number: 1,
  },
  {
    mbid: "track-2",
    title: "Two",
    artist: "Artist",
    album: "Album",
    albumId: "album-1",
    track_number: 2,
    disc_number: 1,
  },
  {
    mbid: "track-3",
    title: "Three",
    artist: "Artist",
    album: "Album",
    albumId: "album-1",
    track_number: 3,
    disc_number: 1,
  },
];

function PlayerHarness() {
  const player = usePlayer();
  return (
    <div>
      <output data-testid="current">{player.current?.mbid ?? "none"}</output>
      <output data-testid="index">{player.index}</output>
      <output data-testid="queue">{player.queue.map((track) => track.mbid).join(",")}</output>
      <output data-testid="shuffle">{String(player.shuffle)}</output>
      <output data-testid="repeat">{player.repeat}</output>
      <button onClick={() => player.play(queue, 0)}>Load queue</button>
      <button onClick={player.next}>Next</button>
      <button onClick={player.toggleShuffle}>Toggle shuffle</button>
      <button onClick={player.cycleRepeat}>Cycle repeat</button>
    </div>
  );
}

function renderPlayer() {
  return render(
    <PlayerProvider>
      <PlayerHarness />
    </PlayerProvider>,
  );
}

describe("PlayerProvider persistence and traversal contracts", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("Audio", FakeAudio);
  });

  it("hydrates a versioned queue, clamps its index, and restores modes", async () => {
    localStorage.setItem(
      "dap.player.queue",
      JSON.stringify({ v: 1, queue: queue.slice(0, 2), index: 99 }),
    );
    localStorage.setItem("dap.player.shuffle", "1");
    localStorage.setItem("dap.player.repeat", "one");

    renderPlayer();

    expect(screen.getByTestId("queue")).toHaveTextContent("track-1,track-2");
    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
    expect(screen.getByTestId("index")).toHaveTextContent("1");
    expect(screen.getByTestId("shuffle")).toHaveTextContent("true");
    expect(screen.getByTestId("repeat")).toHaveTextContent("one");
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem("dap.player.queue") ?? "{}")).toEqual({
        v: 1,
        queue: queue.slice(0, 2),
        index: 1,
      }),
    );
  });

  it("persists queue replacement and cycles repeat off → all → one → off", async () => {
    const user = userEvent.setup();
    renderPlayer();

    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem("dap.player.queue") ?? "{}")).toEqual({
        v: 1,
        queue,
        index: 0,
      }),
    );

    const cycle = screen.getByRole("button", { name: "Cycle repeat" });
    await user.click(cycle);
    expect(screen.getByTestId("repeat")).toHaveTextContent("all");
    expect(localStorage.getItem("dap.player.repeat")).toBe("all");
    await user.click(cycle);
    expect(screen.getByTestId("repeat")).toHaveTextContent("one");
    expect(localStorage.getItem("dap.player.repeat")).toBe("one");
    await user.click(cycle);
    expect(screen.getByTestId("repeat")).toHaveTextContent("off");
    expect(localStorage.getItem("dap.player.repeat")).toBe("off");
  });

  it("shuffle visits each remaining queue index once before stopping", async () => {
    const user = userEvent.setup();
    vi.spyOn(Math, "random").mockReturnValue(0.99);
    renderPlayer();
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await user.click(screen.getByRole("button", { name: "Toggle shuffle" }));
    await waitFor(() =>
      expect(screen.getByTestId("shuffle")).toHaveTextContent("true"),
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByTestId("current")).toHaveTextContent("track-3");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
  });
});
