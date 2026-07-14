import { describe, expect, it, vi } from "vitest";
import {
  buildShufflePool,
  clampQueueIndex,
  cycleRepeatMode,
  insertQueueItems,
  pickNextQueueIndex,
  removeQueueItem,
  updateQueuedTrackLiked,
} from "./queueState";
import type { PlayerTrack } from "./playerTypes";

const tracks: PlayerTrack[] = [
  {
    mbid: "one",
    title: "One",
    artist: "Artist",
    album: "Album",
    albumId: "album",
    track_number: 1,
    disc_number: 1,
  },
  {
    mbid: "two",
    title: "Two",
    artist: "Artist",
    album: "Album",
    albumId: "album",
    track_number: 2,
    disc_number: 1,
  },
  {
    mbid: "three",
    title: "Three",
    artist: "Artist",
    album: "Album",
    albumId: "album",
    track_number: 3,
    disc_number: 1,
  },
];

describe("queue state", () => {
  it("clamps persisted and requested indices to the available queue", () => {
    expect(clampQueueIndex(-4, 3)).toBe(0);
    expect(clampQueueIndex(1, 3)).toBe(1);
    expect(clampQueueIndex(99, 3)).toBe(2);
    expect(clampQueueIndex(99, 0)).toBe(0);
  });

  it("keeps the established sequential and repeat traversal semantics", () => {
    expect(
      pickNextQueueIndex({
        queueLength: 3,
        currentIndex: 1,
        shuffle: false,
        repeat: "off",
        reason: "user",
        shufflePool: new Set(),
      }).targetIndex,
    ).toBe(2);
    expect(
      pickNextQueueIndex({
        queueLength: 3,
        currentIndex: 2,
        shuffle: false,
        repeat: "all",
        reason: "auto",
        shufflePool: new Set(),
      }).targetIndex,
    ).toBe(0);
    expect(
      pickNextQueueIndex({
        queueLength: 3,
        currentIndex: 1,
        shuffle: false,
        repeat: "one",
        reason: "auto",
        shufflePool: new Set(),
      }).targetIndex,
    ).toBe(1);
    expect(
      pickNextQueueIndex({
        queueLength: 3,
        currentIndex: 2,
        shuffle: false,
        repeat: "one",
        reason: "user",
        shufflePool: new Set(),
      }).targetIndex,
    ).toBeNull();
  });

  it("consumes a shuffle pool once and refills it for repeat-all", () => {
    const random = vi.fn().mockReturnValue(0.99);
    const first = pickNextQueueIndex({
      queueLength: 3,
      currentIndex: 0,
      shuffle: true,
      repeat: "off",
      reason: "user",
      shufflePool: buildShufflePool(3, 0),
      random,
    });
    expect(first.targetIndex).toBe(2);
    expect(Array.from(first.shufflePool)).toEqual([1]);

    const second = pickNextQueueIndex({
      queueLength: 3,
      currentIndex: 2,
      shuffle: true,
      repeat: "off",
      reason: "user",
      shufflePool: first.shufflePool,
      random,
    });
    expect(second.targetIndex).toBe(1);
    expect(second.shufflePool.size).toBe(0);

    const stopped = pickNextQueueIndex({
      queueLength: 3,
      currentIndex: 1,
      shuffle: true,
      repeat: "off",
      reason: "user",
      shufflePool: second.shufflePool,
      random,
    });
    expect(stopped.targetIndex).toBeNull();

    const refilled = pickNextQueueIndex({
      queueLength: 3,
      currentIndex: 1,
      shuffle: true,
      repeat: "all",
      reason: "auto",
      shufflePool: stopped.shufflePool,
      random: () => 0,
    });
    expect(refilled.targetIndex).toBe(0);
    expect(Array.from(refilled.shufflePool)).toEqual([2]);
  });

  it("returns no next shuffle target for a single repeated track", () => {
    expect(
      pickNextQueueIndex({
        queueLength: 1,
        currentIndex: 0,
        shuffle: true,
        repeat: "all",
        reason: "auto",
        shufflePool: new Set(),
      }).targetIndex,
    ).toBeNull();
  });

  it("cycles repeat modes in the public off → all → one order", () => {
    expect(cycleRepeatMode("off")).toBe("all");
    expect(cycleRepeatMode("all")).toBe("one");
    expect(cycleRepeatMode("one")).toBe("off");
  });

  it("performs queue mutations without changing no-op queue identity", () => {
    expect(insertQueueItems(tracks, 0, [tracks[2]!]).map((t) => t.mbid)).toEqual([
      "one",
      "three",
      "two",
      "three",
    ]);
    expect(removeQueueItem(tracks, -1)).toBe(tracks);
    expect(removeQueueItem(tracks, 1).map((track) => track.mbid)).toEqual([
      "one",
      "three",
    ]);
    expect(updateQueuedTrackLiked(tracks, "missing", true)).toBe(tracks);
    expect(updateQueuedTrackLiked(tracks, "two", true)[1]?.is_liked).toBe(true);
  });
});
