import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  backendUrl: vi.fn(),
  streamUrl: vi.fn(),
}));

const waveformMocks = vi.hoisted(() => ({
  useWaveformPeaks: vi.fn(),
}));

const windowMocks = vi.hoisted(() => ({
  enterMiniPlayer: vi.fn(),
}));

const playerMocks = vi.hoisted(() => ({
  toggle: vi.fn(),
  next: vi.fn(),
  prev: vi.fn(),
  seek: vi.fn(),
  toggleShuffle: vi.fn(),
  cycleRepeat: vi.fn(),
  setSleepTimer: vi.fn(),
}));

const playerState = vi.hoisted(() => ({
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
  isPlaying: false,
  position: 45,
  duration: 180,
  shuffle: false,
  repeat: "off" as "off" | "all" | "one",
  sleepTimerExpiresAt: null as number | null,
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../lib/useWaveformPeaks", () => waveformMocks);
vi.mock("../lib/window", () => windowMocks);
vi.mock("../player/PlayerContext", () => ({
  usePlayer: () => ({
    ...playerState,
    ...playerMocks,
  }),
}));

import PlayerBar from "./PlayerBar";

function renderPlayerBar(
  props: Partial<React.ComponentProps<typeof PlayerBar>> = {},
) {
  const onToggleQueue = vi.fn();
  const onToggleLyrics = vi.fn();
  const view = render(
    <PlayerBar
      queueOpen={false}
      onToggleQueue={onToggleQueue}
      lyricsOpen={false}
      onToggleLyrics={onToggleLyrics}
      {...props}
    />,
  );
  return { ...view, onToggleQueue, onToggleLyrics };
}

describe("PlayerBar", () => {
  beforeEach(() => {
    apiMocks.backendUrl.mockResolvedValue("http://localhost:5001");
    apiMocks.streamUrl.mockReturnValue("http://localhost:5001/stream/track-1");
    waveformMocks.useWaveformPeaks.mockReturnValue(null);
    windowMocks.enterMiniPlayer.mockResolvedValue(undefined);
    Object.assign(playerState, {
      current: {
        mbid: "track-1",
        title: "First Light",
        artist: "The Testers",
        album: "A Test Album",
        track_number: 1,
        disc_number: 1,
        albumId: "album-1",
      },
      isPlaying: false,
      position: 45,
      duration: 180,
      shuffle: false,
      repeat: "off",
      sleepTimerExpiresAt: null,
    });
  });

  it("wires transport, playback mode, and utility controls", async () => {
    const user = userEvent.setup();
    const { onToggleQueue, onToggleLyrics } = renderPlayerBar();

    await user.click(screen.getByRole("button", { name: "Shuffle" }));
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await user.click(screen.getByRole("button", { name: "Play" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Repeat: off" }));
    await user.click(screen.getByRole("button", { name: "Show lyrics" }));
    await user.click(screen.getByRole("button", { name: "Show queue" }));
    await user.click(
      screen.getByRole("button", { name: "Enter mini-player" }),
    );

    expect(playerMocks.toggleShuffle).toHaveBeenCalledOnce();
    expect(playerMocks.prev).toHaveBeenCalledOnce();
    expect(playerMocks.toggle).toHaveBeenCalledOnce();
    expect(playerMocks.next).toHaveBeenCalledOnce();
    expect(playerMocks.cycleRepeat).toHaveBeenCalledOnce();
    expect(onToggleLyrics).toHaveBeenCalledOnce();
    expect(onToggleQueue).toHaveBeenCalledOnce();
    expect(windowMocks.enterMiniPlayer).toHaveBeenCalledOnce();
  });

  it("shows active mode state and the current track", () => {
    Object.assign(playerState, {
      isPlaying: true,
      shuffle: true,
      repeat: "one",
    });

    renderPlayerBar({ queueOpen: true, lyricsOpen: true });

    expect(screen.getByText("First Light")).toBeInTheDocument();
    expect(screen.getByText("The Testers — A Test Album")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pause" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Shuffle" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Repeat: one" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Hide queue" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Hide lyrics" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("seeks with the fallback progress control", () => {
    renderPlayerBar();

    fireEvent.change(screen.getByRole("slider", { name: "Seek" }), {
      target: { value: "90" },
    });

    expect(playerMocks.seek).toHaveBeenCalledWith(90);
  });

  it("sets and clears the sleep timer from its menu", async () => {
    const user = userEvent.setup();
    const view = renderPlayerBar();

    await user.click(screen.getByRole("button", { name: "Sleep timer" }));
    await user.click(screen.getByRole("menuitem", { name: "30 minutes" }));
    expect(playerMocks.setSleepTimer).toHaveBeenCalledWith(1_800_000);

    playerState.sleepTimerExpiresAt = Date.now() + 30 * 60_000;
    view.rerender(
      <PlayerBar
        queueOpen={false}
        onToggleQueue={vi.fn()}
        lyricsOpen={false}
        onToggleLyrics={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Sleep timer" }));
    await user.click(screen.getByRole("menuitem", { name: "Turn Off" }));
    expect(playerMocks.setSleepTimer).toHaveBeenCalledWith(null);
  });

  it("exposes and dismisses the sleep menu without a focusable backdrop", async () => {
    const user = userEvent.setup();
    renderPlayerBar();
    const trigger = screen.getByRole("button", { name: "Sleep timer" });

    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByRole("menu", { name: "Sleep timer duration" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Close sleep timer menu" }),
    ).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("disables track-specific controls when nothing is playing", () => {
    playerState.current = null;
    playerState.position = 0;
    playerState.duration = 0;

    renderPlayerBar();

    expect(screen.getByText("Nothing Playing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Show lyrics" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "Seek" })).toBeDisabled();
  });
});
