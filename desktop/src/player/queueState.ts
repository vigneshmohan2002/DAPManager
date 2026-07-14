import type { NextTrackReason, PlayerTrack, RepeatMode } from "./playerTypes";

export type NextQueueIndexInput = {
  queueLength: number;
  currentIndex: number;
  shuffle: boolean;
  repeat: RepeatMode;
  reason: NextTrackReason;
  shufflePool: ReadonlySet<number>;
  random?: () => number;
};

export type NextQueueIndexResult = {
  targetIndex: number | null;
  shufflePool: Set<number>;
};

export function clampQueueIndex(index: number, queueLength: number): number {
  return Math.max(0, Math.min(index, queueLength - 1));
}

export function buildShufflePool(
  queueLength: number,
  currentIndex: number,
): Set<number> {
  return new Set(
    Array.from({ length: queueLength }, (_, index) => index).filter(
      (index) => index !== currentIndex,
    ),
  );
}

export function pickNextQueueIndex({
  queueLength,
  currentIndex,
  shuffle,
  repeat,
  reason,
  shufflePool,
  random = Math.random,
}: NextQueueIndexInput): NextQueueIndexResult {
  let nextPool = new Set(shufflePool);

  if (queueLength === 0) {
    return { targetIndex: null, shufflePool: nextPool };
  }
  if (reason === "auto" && repeat === "one") {
    return { targetIndex: currentIndex, shufflePool: nextPool };
  }

  if (shuffle) {
    if (nextPool.size === 0) {
      if (repeat !== "all") {
        return { targetIndex: null, shufflePool: nextPool };
      }
      nextPool = buildShufflePool(queueLength, currentIndex);
    }
    if (nextPool.size === 0) {
      return { targetIndex: null, shufflePool: nextPool };
    }

    const candidates = Array.from(nextPool);
    const targetIndex = candidates[Math.floor(random() * candidates.length)]!;
    nextPool.delete(targetIndex);
    return { targetIndex, shufflePool: nextPool };
  }

  if (currentIndex + 1 < queueLength) {
    return { targetIndex: currentIndex + 1, shufflePool: nextPool };
  }
  return {
    targetIndex: repeat === "all" ? 0 : null,
    shufflePool: nextPool,
  };
}

export function cycleRepeatMode(repeat: RepeatMode): RepeatMode {
  if (repeat === "off") return "all";
  if (repeat === "all") return "one";
  return "off";
}

export function normalizeQueueItems(
  tracks: PlayerTrack | PlayerTrack[],
): PlayerTrack[] {
  return Array.isArray(tracks) ? tracks : [tracks];
}

export function insertQueueItems(
  queue: PlayerTrack[],
  afterIndex: number,
  items: PlayerTrack[],
): PlayerTrack[] {
  if (queue.length === 0) return items;
  const insertAt = Math.min(afterIndex + 1, queue.length);
  return [
    ...queue.slice(0, insertAt),
    ...items,
    ...queue.slice(insertAt),
  ];
}

export function removeQueueItem(
  queue: PlayerTrack[],
  targetIndex: number,
): PlayerTrack[] {
  if (targetIndex < 0 || targetIndex >= queue.length) return queue;
  return [
    ...queue.slice(0, targetIndex),
    ...queue.slice(targetIndex + 1),
  ];
}

export function updateQueuedTrackLiked(
  queue: PlayerTrack[],
  mbid: string,
  liked: boolean,
): PlayerTrack[] {
  if (
    !queue.some(
      (track) => track.mbid === mbid && Boolean(track.is_liked) !== liked,
    )
  ) {
    return queue;
  }
  return queue.map((track) =>
    track.mbid === mbid ? { ...track, is_liked: liked } : track,
  );
}
