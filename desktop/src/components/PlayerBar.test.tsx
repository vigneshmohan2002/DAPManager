import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  backendUrl: vi.fn(),
  setTrackLiked: vi.fn(),
  streamUrl: vi.fn(),
}));

const toastMocks = vi.hoisted(() => ({ show: vi.fn() }));

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
  setTrackLikedInQueue: vi.fn(),
  setVolume: vi.fn(),
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
    is_liked: false,
  } as {
    mbid: string;
    title: string;
    artist: string;
    album: string | null;
    track_number: number | null;
    disc_number: number | null;
    albumId: string | null;
    is_liked?: boolean;
  } | null,
  isPlaying: false,
  position: 45,
  duration: 180,
  shuffle: false,
  repeat: "off" as "off" | "all" | "one",
  volume: 0.75,
  sleepTimerExpiresAt: null as number | null,
}));

vi.mock("../lib/api", () => apiMocks);
vi.mock("./Toast", () => ({ useToast: () => toastMocks }));
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
    apiMocks.setTrackLiked.mockResolvedValue({ success: true });
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
        is_liked: false,
      },
      isPlaying: false,
      position: 45,
      duration: 180,
      shuffle: false,
      repeat: "off",
      volume: 0.75,
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

  it("changes the actual player volume through its range control", () => {
    renderPlayerBar();

    fireEvent.change(screen.getByRole("slider", { name: "Volume" }), {
      target: { value: "0.4" },
    });

    expect(playerMocks.setVolume).toHaveBeenCalledWith(0.4);
  });

  it("likes the current track and updates the queued copy", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    renderPlayerBar({ onPlaylistsChanged });

    await user.click(
      screen.getByRole("button", { name: "Like current track" }),
    );

    expect(playerMocks.setTrackLikedInQueue).toHaveBeenCalledWith(
      "track-1",
      true,
    );
    expect(apiMocks.setTrackLiked).toHaveBeenCalledWith("track-1", true);
    expect(onPlaylistsChanged).toHaveBeenCalledOnce();
  });

  it("rolls back a failed current-track like", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    apiMocks.setTrackLiked.mockResolvedValue({
      success: false,
      message: "Nope",
    });
    renderPlayerBar({ onPlaylistsChanged });

    await user.click(
      screen.getByRole("button", { name: "Like current track" }),
    );

    expect(playerMocks.setTrackLikedInQueue).toHaveBeenNthCalledWith(
      1,
      "track-1",
      true,
    );
    expect(playerMocks.setTrackLikedInQueue).toHaveBeenNthCalledWith(
      2,
      "track-1",
      false,
    );
    expect(toastMocks.show).toHaveBeenCalledWith("Nope", "err");
    expect(onPlaylistsChanged).not.toHaveBeenCalled();
  });

  it("does not refresh playlists after a successful unlike", async () => {
    const user = userEvent.setup();
    const onPlaylistsChanged = vi.fn();
    if (playerState.current) playerState.current.is_liked = true;
    renderPlayerBar({ onPlaylistsChanged });

    await user.click(
      screen.getByRole("button", { name: "Unlike current track" }),
    );

    expect(playerMocks.setTrackLikedInQueue).toHaveBeenCalledWith(
      "track-1",
      false,
    );
    expect(apiMocks.setTrackLiked).toHaveBeenCalledWith("track-1", false);
    expect(onPlaylistsChanged).not.toHaveBeenCalled();
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

  it("keeps compact player utilities in a keyboard-accessible overflow menu", async () => {
    const user = userEvent.setup();
    const { onToggleQueue, onToggleLyrics } = renderPlayerBar();
    const trigger = screen.getByRole("button", {
      name: "More player controls",
      hidden: true,
    });

    fireEvent.click(trigger);
    const menu = screen.getByRole("menu", {
      name: "More player controls",
    });
    const sleepItem = within(menu).getByRole("menuitem", {
      name: "Sleep Timer",
    });
    const lyricsItem = within(menu).getByRole("menuitem", {
      name: "Show Lyrics",
    });
    const miniItem = within(menu).getByRole("menuitem", {
      name: "Enter Mini Player",
    });

    await waitFor(() => expect(sleepItem).toHaveFocus());
    expect(
      within(menu).queryByRole("menuitem", { name: /queue/i }),
    ).not.toBeInTheDocument();
    await user.keyboard("{ArrowDown}");
    expect(lyricsItem).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onToggleLyrics).toHaveBeenCalledOnce();
    expect(onToggleQueue).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    await waitFor(() =>
      expect(
        within(
          screen.getByRole("menu", { name: "More player controls" }),
        ).getByRole("menuitem", { name: "Sleep Timer" }),
      ).toHaveFocus(),
    );
    await user.keyboard("{End}");
    expect(
      within(
        screen.getByRole("menu", { name: "More player controls" }),
      ).getByRole("menuitem", { name: "Enter Mini Player" }),
    ).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(windowMocks.enterMiniPlayer).toHaveBeenCalledOnce();

    fireEvent.click(trigger);
    expect(
      screen.getByRole("menu", { name: "More player controls" }),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(
      screen.queryByRole("menu", { name: "More player controls" }),
    ).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    expect(screen.getByRole("button", { name: "Show queue" })).toBeEnabled();
    expect(miniItem).not.toBeInTheDocument();
  });

  it("opens the sleep timer from the compact overflow and restores focus", async () => {
    const user = userEvent.setup();
    renderPlayerBar();
    const trigger = screen.getByRole("button", {
      name: "More player controls",
      hidden: true,
    });

    fireEvent.click(trigger);
    await user.click(
      within(
        screen.getByRole("menu", { name: "More player controls" }),
      ).getByRole("menuitem", { name: "Sleep Timer" }),
    );

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const sleepMenu = screen.getByRole("menu", {
      name: "Sleep timer duration",
    });
    await waitFor(() =>
      expect(
        within(sleepMenu).getByRole("menuitem", { name: "15 minutes" }),
      ).toHaveFocus(),
    );
    await user.click(
      within(sleepMenu).getByRole("menuitem", { name: "30 minutes" }),
    );
    expect(playerMocks.setSleepTimer).toHaveBeenCalledWith(1_800_000);
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
