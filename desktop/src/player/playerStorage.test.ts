import { describe, expect, it, vi } from "vitest";
import {
  PLAYER_QUEUE_VERSION,
  PLAYER_STORAGE_KEYS,
  decodePersistedVolume,
  decodePersistedQueue,
  persistQueue,
  persistRepeat,
  persistShuffle,
  persistVolume,
  readPersistedPlayerState,
} from "./playerStorage";
import type { PlayerTrack } from "./playerTypes";

const queue: PlayerTrack[] = [
  {
    mbid: "track-1",
    title: "One",
    artist: "Artist",
    album: "Album",
    albumId: null,
    track_number: 1,
    disc_number: 1,
  },
];

describe("player storage codec", () => {
  it("preserves the established keys and queue version", () => {
    expect(PLAYER_STORAGE_KEYS).toEqual({
      shuffle: "dap.player.shuffle",
      repeat: "dap.player.repeat",
      queue: "dap.player.queue",
      volume: "dap.player.volume",
    });
    expect(PLAYER_QUEUE_VERSION).toBe(1);
  });

  it("decodes minimally valid tracks and clamps the current index", () => {
    expect(
      decodePersistedQueue(
        JSON.stringify({ v: 1, queue, index: 99 }),
      ),
    ).toEqual({ v: 1, queue, index: 0 });
  });

  it.each([
    ["missing", null],
    ["malformed", "{"],
    ["wrong version", JSON.stringify({ v: 2, queue, index: 0 })],
    [
      "invalid track",
      JSON.stringify({ v: 1, queue: [{ title: "No MBID" }], index: 0 }),
    ],
  ])("drops a %s queue payload", (_label, raw) => {
    expect(decodePersistedQueue(raw)).toEqual({ v: 1, queue: [], index: 0 });
  });

  it("reads queue and modes through the typed storage boundary", () => {
    const values: Record<string, string> = {
      [PLAYER_STORAGE_KEYS.queue]: JSON.stringify({ v: 1, queue, index: 0 }),
      [PLAYER_STORAGE_KEYS.shuffle]: "1",
      [PLAYER_STORAGE_KEYS.repeat]: "one",
      [PLAYER_STORAGE_KEYS.volume]: "0.65",
    };
    const storage = {
      getItem: vi.fn((key: string) => values[key] ?? null),
    };

    expect(readPersistedPlayerState(storage)).toEqual({
      queue,
      index: 0,
      shuffle: true,
      repeat: "one",
      volume: 0.65,
    });
    expect(storage.getItem).toHaveBeenCalledTimes(4);
  });

  it("keeps independently readable modes when one storage key throws", () => {
    const storage = {
      getItem: vi.fn((key: string) => {
        if (key === PLAYER_STORAGE_KEYS.queue) throw new Error("queue denied");
        if (key === PLAYER_STORAGE_KEYS.shuffle) return "1";
        if (key === PLAYER_STORAGE_KEYS.repeat) return "all";
        return null;
      }),
    };

    expect(readPersistedPlayerState(storage)).toEqual({
      queue: [],
      index: 0,
      shuffle: true,
      repeat: "all",
      volume: 1,
    });
  });

  it("defaults old state safely and clamps persisted volume", () => {
    expect(decodePersistedVolume(null)).toBe(1);
    expect(decodePersistedVolume("not-a-number")).toBe(1);
    expect(decodePersistedVolume("-0.25")).toBe(0);
    expect(decodePersistedVolume("1.5")).toBe(1);
    expect(decodePersistedVolume("0.4")).toBe(0.4);
  });

  it("writes the exact persisted shape and ignores unavailable storage", () => {
    const storage = { setItem: vi.fn() };
    persistQueue(queue, 0, storage);
    persistShuffle(true, storage);
    persistRepeat("all", storage);
    persistVolume(0.35, storage);

    expect(storage.setItem).toHaveBeenNthCalledWith(
      1,
      PLAYER_STORAGE_KEYS.queue,
      JSON.stringify({ v: 1, queue, index: 0 }),
    );
    expect(storage.setItem).toHaveBeenNthCalledWith(
      2,
      PLAYER_STORAGE_KEYS.shuffle,
      "1",
    );
    expect(storage.setItem).toHaveBeenNthCalledWith(
      3,
      PLAYER_STORAGE_KEYS.repeat,
      "all",
    );
    expect(storage.setItem).toHaveBeenNthCalledWith(
      4,
      PLAYER_STORAGE_KEYS.volume,
      "0.35",
    );

    const unavailable = {
      setItem: vi.fn(() => {
        throw new Error("quota");
      }),
    };
    expect(() => persistQueue(queue, 0, unavailable)).not.toThrow();
    expect(() => persistVolume(2, unavailable)).not.toThrow();
  });
});
