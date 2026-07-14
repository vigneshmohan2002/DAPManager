import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
  static instances: FakeAudio[] = [];

  preload = "";
  src = "";
  currentTime = 0;
  duration = 0;
  paused = true;
  private listeners = new Map<string, Set<EventListener>>();

  constructor() {
    FakeAudio.instances.push(this);
  }

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

  emit(type: string): void {
    if (type === "play") this.paused = false;
    if (type === "pause" || type === "ended") this.paused = true;
    const event = new Event(type);
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  listenerCount(type: string): number {
    return this.listeners.get(type)?.size ?? 0;
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
  const [albumCount, setAlbumCount] = useState<number | null>(null);
  return (
    <div>
      <output data-testid="current">{player.current?.mbid ?? "none"}</output>
      <output data-testid="index">{player.index}</output>
      <output data-testid="queue">{player.queue.map((track) => track.mbid).join(",")}</output>
      <output data-testid="shuffle">{String(player.shuffle)}</output>
      <output data-testid="repeat">{player.repeat}</output>
      <output data-testid="playing">{String(player.isPlaying)}</output>
      <output data-testid="position">{player.position}</output>
      <output data-testid="duration">{player.duration}</output>
      <output data-testid="sleep">
        {player.sleepTimerExpiresAt === null ? "none" : "armed"}
      </output>
      <output data-testid="album-count">{albumCount ?? "none"}</output>
      <output data-testid="player-api">
        {Object.keys(player).sort().join(",")}
      </output>
      <button onClick={() => player.play(queue, 0)}>Load queue</button>
      <button
        onClick={() => {
          void player.playAlbum("release-1").then(setAlbumCount);
        }}
      >
        Play album
      </button>
      <button onClick={player.next}>Next</button>
      <button onClick={player.prev}>Previous</button>
      <button onClick={player.toggleShuffle}>Toggle shuffle</button>
      <button onClick={player.cycleRepeat}>Cycle repeat</button>
      <button onClick={() => player.setSleepTimer(20)}>Set sleep timer</button>
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
    FakeAudio.instances = [];
    vi.stubGlobal("Audio", FakeAudio);
    vi.stubGlobal(
      "MediaMetadata",
      class FakeMediaMetadata {
        constructor(init: MediaMetadataInit) {
          Object.assign(this, init);
        }
      },
    );
    Object.defineProperty(navigator, "mediaSession", {
      configurable: true,
      value: {
        metadata: null,
        setActionHandler: vi.fn(),
      },
    });
    apiMocks.fetchAlbumTracks.mockResolvedValue([]);
  });

  it("preserves every public context field and method", () => {
    renderPlayer();

    expect(screen.getByTestId("player-api")).toHaveTextContent(
      [
        "addToQueue",
        "clearQueue",
        "current",
        "cycleRepeat",
        "duration",
        "index",
        "isPlaying",
        "jumpTo",
        "next",
        "play",
        "playAlbum",
        "playNext",
        "position",
        "prev",
        "queue",
        "removeFromQueue",
        "repeat",
        "seek",
        "setSleepTimer",
        "setTrackLikedInQueue",
        "shuffle",
        "sleepTimerExpiresAt",
        "toggle",
        "toggleShuffle",
      ]
        .sort()
        .join(","),
    );
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

  it("hydrates storage once instead of reparsing it on every render", async () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem");
    const user = userEvent.setup();
    renderPlayer();

    expect(getItem).toHaveBeenCalledTimes(3);
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
    expect(getItem).toHaveBeenCalledTimes(3);
  });

  it("loads an album in API order, starts at index zero, and reports its count", async () => {
    const user = userEvent.setup();
    apiMocks.fetchAlbumTracks.mockResolvedValue([
      { ...queue[2], albumId: undefined },
      { ...queue[0], albumId: undefined },
    ]);
    renderPlayer();

    await user.click(screen.getByRole("button", { name: "Play album" }));

    await waitFor(() =>
      expect(screen.getByTestId("album-count")).toHaveTextContent("2"),
    );
    expect(apiMocks.fetchAlbumTracks).toHaveBeenCalledWith("release-1");
    expect(screen.getByTestId("queue")).toHaveTextContent("track-3,track-1");
    expect(screen.getByTestId("index")).toHaveTextContent("0");
    expect(screen.getByTestId("current")).toHaveTextContent("track-3");
    expect(
      JSON.parse(localStorage.getItem("dap.player.queue") ?? "{}").queue,
    ).toEqual([
      { ...queue[2], albumId: "release-1" },
      { ...queue[0], albumId: "release-1" },
    ]);
  });

  it("maps audio events to time state and sequential auto-advance", async () => {
    const user = userEvent.setup();
    renderPlayer();
    const audio = FakeAudio.instances[0]!;
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await waitFor(() =>
      expect(audio.src).toBe("http://localhost:5001/stream/track-1"),
    );

    act(() => {
      audio.duration = 180;
      audio.currentTime = 42;
      audio.emit("durationchange");
      audio.emit("timeupdate");
    });
    expect(screen.getByTestId("duration")).toHaveTextContent("180");
    expect(screen.getByTestId("position")).toHaveTextContent("42");

    act(() => audio.emit("ended"));
    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
  });

  it("removes every audio event listener when the provider unmounts", () => {
    const player = renderPlayer();
    const audio = FakeAudio.instances[0]!;
    const eventTypes = [
      "play",
      "pause",
      "timeupdate",
      "durationchange",
      "ended",
    ];
    eventTypes.forEach((eventType) => {
      expect(audio.listenerCount(eventType)).toBe(1);
    });

    player.unmount();

    eventTypes.forEach((eventType) => {
      expect(audio.listenerCount(eventType)).toBe(0);
    });
  });

  it("restarts the current audio source when auto-repeat is one", async () => {
    const user = userEvent.setup();
    renderPlayer();
    const audio = FakeAudio.instances[0]!;
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await user.click(screen.getByRole("button", { name: "Cycle repeat" }));
    await user.click(screen.getByRole("button", { name: "Cycle repeat" }));
    await waitFor(() =>
      expect(screen.getByTestId("repeat")).toHaveTextContent("one"),
    );
    audio.play.mockClear();
    audio.currentTime = 120;

    act(() => audio.emit("ended"));

    expect(screen.getByTestId("current")).toHaveTextContent("track-1");
    expect(audio.currentTime).toBe(0);
    expect(audio.play).toHaveBeenCalledTimes(1);
  });

  it("publishes MediaSession metadata and transport handlers", async () => {
    const user = userEvent.setup();
    const handlers = new Map<string, (() => void) | null>();
    const setActionHandler = vi.fn(
      (action: string, handler: (() => void) | null) => {
        handlers.set(action, handler);
      },
    );
    const mediaSession = { metadata: null as unknown, setActionHandler };
    Object.defineProperty(navigator, "mediaSession", {
      configurable: true,
      value: mediaSession,
    });
    renderPlayer();
    await user.click(screen.getByRole("button", { name: "Load queue" }));

    await waitFor(() => expect(mediaSession.metadata).not.toBeNull());
    expect(mediaSession.metadata).toMatchObject({
      title: "One",
      artist: "Artist",
      album: "Album",
      artwork: [
        {
          src: "http://localhost:5001/cover/album-1",
          sizes: "512x512",
        },
      ],
    });
    expect(Array.from(handlers.keys())).toEqual([
      "play",
      "pause",
      "previoustrack",
      "nexttrack",
    ]);

    act(() => handlers.get("nexttrack")?.());
    expect(screen.getByTestId("current")).toHaveTextContent("track-2");
    act(() => handlers.get("previoustrack")?.());
    expect(screen.getByTestId("current")).toHaveTextContent("track-1");
  });

  it("records one scrobble while excluding paused wall-clock time", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
    const user = userEvent.setup();
    renderPlayer();
    const audio = FakeAudio.instances[0]!;
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await waitFor(() =>
      expect(audio.src).toBe("http://localhost:5001/stream/track-1"),
    );

    now.mockReturnValue(6_000);
    act(() => audio.emit("pause"));
    now.mockReturnValue(20_000);
    act(() => audio.emit("play"));
    now.mockReturnValue(23_000);
    act(() => {
      audio.duration = 20;
      audio.currentTime = 10;
      audio.emit("durationchange");
      audio.emit("timeupdate");
    });

    await waitFor(() =>
      expect(apiMocks.recordPlay).toHaveBeenCalledWith(
        "track-1",
        "desktop",
        8_000,
      ),
    );
    act(() => {
      audio.currentTime = 80;
      audio.emit("timeupdate");
    });
    expect(apiMocks.recordPlay).toHaveBeenCalledTimes(1);
  });

  it("pauses and clears an expiring sleep timer without persisting it", async () => {
    const user = userEvent.setup();
    renderPlayer();
    const audio = FakeAudio.instances[0]!;
    await user.click(screen.getByRole("button", { name: "Load queue" }));
    await waitFor(() => expect(screen.getByTestId("playing")).toHaveTextContent("true"));
    audio.pause.mockClear();

    await user.click(screen.getByRole("button", { name: "Set sleep timer" }));
    expect(screen.getByTestId("sleep")).toHaveTextContent("armed");
    await waitFor(() => expect(audio.pause).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId("sleep")).toHaveTextContent("none");
    expect(localStorage.getItem("dap.player.sleepTimer")).toBeNull();
  });
});
