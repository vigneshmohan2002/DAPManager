import type { PlayerTrack, RepeatMode } from "./playerTypes";
import { clampQueueIndex } from "./queueState";

export const PLAYER_STORAGE_KEYS = {
  shuffle: "dap.player.shuffle",
  repeat: "dap.player.repeat",
  queue: "dap.player.queue",
} as const;

// Bump this only when the persisted queue shape changes incompatibly.
export const PLAYER_QUEUE_VERSION = 1;

export type PersistedQueue = {
  v: number;
  queue: PlayerTrack[];
  index: number;
};

export type PersistedPlayerState = {
  queue: PlayerTrack[];
  index: number;
  shuffle: boolean;
  repeat: RepeatMode;
};

type StorageReader = Pick<Storage, "getItem">;
type StorageWriter = Pick<Storage, "setItem">;

function browserStorage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function emptyQueue(): PersistedQueue {
  return { v: PLAYER_QUEUE_VERSION, queue: [], index: 0 };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isPersistedTrack(value: unknown): value is PlayerTrack {
  return (
    isRecord(value) &&
    typeof value.mbid === "string" &&
    typeof value.title === "string"
  );
}

function readStorageValue(
  storage: StorageReader,
  key: string,
): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

export function decodePersistedQueue(raw: string | null): PersistedQueue {
  if (!raw) return emptyQueue();

  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed) || parsed.v !== PLAYER_QUEUE_VERSION) {
      return emptyQueue();
    }

    const queue: unknown[] = Array.isArray(parsed.queue) ? parsed.queue : [];
    if (!queue.every(isPersistedTrack)) return emptyQueue();

    const persistedIndex =
      typeof parsed.index === "number" ? parsed.index : 0;
    return {
      v: PLAYER_QUEUE_VERSION,
      queue,
      index: clampQueueIndex(persistedIndex, queue.length),
    };
  } catch {
    return emptyQueue();
  }
}

export function readPersistedPlayerState(
  storage: StorageReader | null = browserStorage(),
): PersistedPlayerState {
  if (!storage) {
    return { queue: [], index: 0, shuffle: false, repeat: "off" };
  }

  const persistedQueue = decodePersistedQueue(
    readStorageValue(storage, PLAYER_STORAGE_KEYS.queue),
  );
  const shuffleValue = readStorageValue(
    storage,
    PLAYER_STORAGE_KEYS.shuffle,
  );
  const repeatValue = readStorageValue(storage, PLAYER_STORAGE_KEYS.repeat);
  return {
    queue: persistedQueue.queue,
    index: persistedQueue.index,
    shuffle: shuffleValue === "1",
    repeat:
      repeatValue === "all" || repeatValue === "one" ? repeatValue : "off",
  };
}

export function persistQueue(
  queue: PlayerTrack[],
  index: number,
  storage: StorageWriter | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    const payload: PersistedQueue = {
      v: PLAYER_QUEUE_VERSION,
      queue,
      index,
    };
    storage.setItem(PLAYER_STORAGE_KEYS.queue, JSON.stringify(payload));
  } catch {
    // Storage is an optional convenience; private mode/quota errors are safe.
  }
}

export function persistShuffle(
  shuffle: boolean,
  storage: StorageWriter | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(PLAYER_STORAGE_KEYS.shuffle, shuffle ? "1" : "0");
  } catch {
    // Fall back to session-only state.
  }
}

export function persistRepeat(
  repeat: RepeatMode,
  storage: StorageWriter | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(PLAYER_STORAGE_KEYS.repeat, repeat);
  } catch {
    // Fall back to session-only state.
  }
}
