import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PlaybackAudioProvider,
  clampPlaybackPosition,
  useSmoothPlaybackPosition,
} from "./playbackClock";

type FrameCallback = (time: number) => void;

let nextFrameId = 1;
let frames = new Map<number, FrameCallback>();

function runAnimationFrame(time = 16): void {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(time));
}

function PositionProbe({
  position,
  duration,
  isPlaying,
  trackKey = "track-1",
}: {
  position: number;
  duration: number;
  isPlaying: boolean;
  trackKey?: string | null;
}) {
  const smooth = useSmoothPlaybackPosition({
    position,
    duration,
    isPlaying,
    trackKey,
  });
  return <output>{smooth}</output>;
}

function renderProbe({
  audio,
  ...props
}: React.ComponentProps<typeof PositionProbe> & {
  audio: HTMLAudioElement | null;
}) {
  return render(
    <PlaybackAudioProvider audio={audio}>
      <PositionProbe {...props} />
    </PlaybackAudioProvider>,
  );
}

describe("playback clock", () => {
  beforeEach(() => {
    nextFrameId = 1;
    frames = new Map();
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn((callback: FrameCallback) => {
        const id = nextFrameId++;
        frames.set(id, callback);
        return id;
      }),
    );
    vi.stubGlobal(
      "cancelAnimationFrame",
      vi.fn((id: number) => frames.delete(id)),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("samples the real audio clock every animation frame while playing", () => {
    const audio = { currentTime: 12.25 } as HTMLAudioElement;
    renderProbe({ audio, position: 12, duration: 120, isPlaying: true });

    act(() => runAnimationFrame());
    expect(screen.getByRole("status")).toHaveTextContent("12.25");

    audio.currentTime = 12.5;
    act(() => runAnimationFrame(32));
    expect(screen.getByRole("status")).toHaveTextContent("12.5");
  });

  it("uses the exact coarse position while paused", () => {
    const audio = { currentTime: 80 } as HTMLAudioElement;
    const view = renderProbe({
      audio,
      position: 14,
      duration: 120,
      isPlaying: false,
    });

    expect(screen.getByRole("status")).toHaveTextContent("14");
    expect(requestAnimationFrame).not.toHaveBeenCalled();

    view.rerender(
      <PlaybackAudioProvider audio={audio}>
        <PositionProbe position={42} duration={120} isPlaying={false} />
      </PlaybackAudioProvider>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("42");
  });

  it("clamps invalid and out-of-range positions", () => {
    expect(clampPlaybackPosition(Number.NaN, 100)).toBe(0);
    expect(clampPlaybackPosition(-1, 100)).toBe(0);
    expect(clampPlaybackPosition(101, 100)).toBe(100);
    expect(clampPlaybackPosition(5, 0)).toBe(5);
  });
});
