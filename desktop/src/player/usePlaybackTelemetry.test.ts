import { describe, expect, it } from "vitest";
import { hasReachedScrobbleThreshold } from "./usePlaybackTelemetry";

describe("playback telemetry threshold", () => {
  it.each([
    [29, 120, false],
    [30, 120, true],
    [10, 20, true],
    [9.9, 20, false],
    [29, 0, false],
    [Number.NaN, 20, true],
    [10, Number.NaN, true],
  ])(
    "returns %s seconds of %s as %s",
    (position, duration, expected) => {
      expect(hasReachedScrobbleThreshold(position, duration)).toBe(expected);
    },
  );
});
