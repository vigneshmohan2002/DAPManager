import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  clampPlayerVolume,
  readPersistedPlayerState,
} from "./playerStorage";
import {
  buildShufflePool,
  clampQueueIndex,
  cycleRepeatMode,
  insertQueueItems,
  normalizeQueueItems,
  pickNextQueueIndex,
  removeQueueItem,
  updateQueuedTrackLiked,
} from "./queueState";
import type {
  NextTrackReason,
  PlayerTrack,
  RepeatMode,
} from "./playerTypes";
import { useAlbumPlayback } from "./useAlbumPlayback";
import {
  createAudioElement,
  useAudioEvents,
  useBackendBaseUrl,
  useTrackSource,
} from "./useAudioPlayback";
import { useMediaSession } from "./useMediaSession";
import { usePlaybackTelemetry } from "./usePlaybackTelemetry";
import { usePlayerStorage } from "./usePlayerStorage";
import { useSleepTimer } from "./useSleepTimer";
import { PlaybackAudioProvider } from "./playbackClock";

export type { PlayerTrack, RepeatMode } from "./playerTypes";

type PlayerState = {
  queue: PlayerTrack[];
  index: number;
  current: PlayerTrack | null;
  isPlaying: boolean;
  position: number;
  duration: number;
  shuffle: boolean;
  repeat: RepeatMode;
  volume: number;
  play: (queue: PlayerTrack[], startIndex?: number) => void;
  // Fetch an album's ordered tracks, replace the queue, and start at track 1.
  // Returns the number of queued tracks so callers can surface an empty state.
  playAlbum: (albumId: string) => Promise<number>;
  toggle: () => void;
  next: () => void;
  prev: () => void;
  seek: (seconds: number) => void;
  jumpTo: (index: number) => void;
  removeFromQueue: (index: number) => void;
  clearQueue: () => void;
  // Append to the end. If the queue is empty, playback starts on the
  // first appended track.
  addToQueue: (tracks: PlayerTrack | PlayerTrack[]) => void;
  // Insert immediately after the currently-playing track. If the queue
  // is empty, behaves like ``addToQueue`` and starts playback.
  playNext: (tracks: PlayerTrack | PlayerTrack[]) => void;
  toggleShuffle: () => void;
  cycleRepeat: () => void;
  // Update is_liked on a queued track in place. Called by every
  // heart-toggle path so the queue panel doesn't drift out of sync.
  setTrackLikedInQueue: (mbid: string, liked: boolean) => void;
  setVolume: (volume: number) => void;
  // Sleep timer expiry (epoch ms) or null when no timer is set.
  sleepTimerExpiresAt: number | null;
  setSleepTimer: (durationMs: number | null) => void;
};

const Ctx = createContext<PlayerState | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  if (audioRef.current === null) {
    audioRef.current = createAudioElement();
  }
  const audio = audioRef.current;

  // Read all persisted state once per provider mount. Playback remains paused
  // because browser autoplay rules require a fresh user gesture after reload.
  const [persisted] = useState(() => readPersistedPlayerState());
  const [queue, setQueue] = useState<PlayerTrack[]>(persisted.queue);
  const [index, setIndex] = useState(persisted.index);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [shuffle, setShuffle] = useState(persisted.shuffle);
  const [repeat, setRepeat] = useState<RepeatMode>(persisted.repeat);
  const [volume, setVolumeState] = useState(persisted.volume);

  useEffect(() => {
    if (audio) audio.volume = volume;
  }, [audio, volume]);

  usePlayerStorage(queue, index, shuffle, repeat, volume);
  const baseUrl = useBackendBaseUrl();
  const { sleepTimerExpiresAt, setSleepTimer } = useSleepTimer(audio);
  const current = queue[index] ?? null;

  // Queue indices still eligible in the active shuffle cycle. Keeping the
  // pool outside React state avoids a render for each random selection.
  const shufflePoolRef = useRef<Set<number>>(new Set());

  const pickNextIndex = useCallback(
    (reason: NextTrackReason): number | null => {
      const result = pickNextQueueIndex({
        queueLength: queue.length,
        currentIndex: index,
        shuffle,
        repeat,
        reason,
        shufflePool: shufflePoolRef.current,
      });
      shufflePoolRef.current = result.shufflePool;
      return result.targetIndex;
    },
    [queue.length, index, shuffle, repeat],
  );

  // Reset only when queue identity or shuffle mode changes. Playback
  // progression consumes the existing pool rather than recreating it.
  useEffect(() => {
    shufflePoolRef.current = shuffle
      ? buildShufflePool(queue.length, index)
      : new Set();
    // `index` is intentionally omitted; see the comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue, shuffle]);

  const play = useCallback(
    (nextQueue: PlayerTrack[], startIndex = 0) => {
      const targetIndex = clampQueueIndex(startIndex, nextQueue.length);
      const target = nextQueue[targetIndex] ?? null;
      const sameTrack = Boolean(target && target.mbid === current?.mbid);

      if (audio) {
        audio.currentTime = 0;
        // A same-track queue replacement does not retrigger the source effect.
        if (sameTrack) {
          setIsPlaying(true);
          void audio.play().catch(() => setIsPlaying(false));
        }
      }
      setPosition(0);
      if (!sameTrack) setDuration(0);
      setQueue(nextQueue);
      setIndex(targetIndex);
    },
    [audio, current?.mbid],
  );

  const playAlbum = useAlbumPlayback(play);

  const toggle = useCallback(() => {
    if (!audio || !current) return;
    if (audio.paused) void audio.play().catch(() => {});
    else audio.pause();
  }, [audio, current]);

  const next = useCallback(() => {
    const targetIndex = pickNextIndex("user");
    if (targetIndex !== null) setIndex(targetIndex);
  }, [pickNextIndex]);

  const prev = useCallback(() => {
    if (audio && audio.currentTime > 3) {
      audio.currentTime = 0;
      return;
    }
    // Previous remains sequential even in shuffle mode, matching the
    // visible queue order and the established desktop behavior.
    setIndex((currentIndex) => Math.max(0, currentIndex - 1));
  }, [audio]);

  const toggleShuffle = useCallback(() => {
    setShuffle((enabled) => !enabled);
  }, []);

  const cycleRepeat = useCallback(() => {
    setRepeat(cycleRepeatMode);
  }, []);

  const setVolume = useCallback((nextVolume: number) => {
    setVolumeState((currentVolume) =>
      clampPlayerVolume(nextVolume, currentVolume),
    );
  }, []);

  const addToQueue = useCallback(
    (tracks: PlayerTrack | PlayerTrack[]) => {
      const items = normalizeQueueItems(tracks);
      if (items.length === 0) return;
      setQueue((currentQueue) => {
        if (currentQueue.length === 0) setIndex(0);
        return currentQueue.concat(items);
      });
    },
    [],
  );

  const playNext = useCallback(
    (tracks: PlayerTrack | PlayerTrack[]) => {
      const items = normalizeQueueItems(tracks);
      if (items.length === 0) return;
      setQueue((currentQueue) => {
        if (currentQueue.length === 0) setIndex(0);
        return insertQueueItems(currentQueue, index, items);
      });
    },
    [index],
  );

  const setTrackLikedInQueue = useCallback(
    (mbid: string, liked: boolean) => {
      setQueue((currentQueue) =>
        updateQueuedTrackLiked(currentQueue, mbid, liked),
      );
    },
    [],
  );

  const jumpTo = useCallback(
    (targetIndex: number) => {
      setIndex((currentIndex) => {
        if (targetIndex < 0 || targetIndex >= queue.length) {
          return currentIndex;
        }
        return targetIndex;
      });
    },
    [queue.length],
  );

  // Removing the current item keeps its numeric index so the following
  // track takes over; removing an earlier item shifts the index back one.
  const removeFromQueue = useCallback((targetIndex: number) => {
    setQueue((currentQueue) =>
      removeQueueItem(currentQueue, targetIndex),
    );
    setIndex((currentIndex) => {
      if (targetIndex < currentIndex) return currentIndex - 1;
      return currentIndex;
    });
  }, []);

  const clearQueue = useCallback(() => {
    setQueue([]);
    setIndex(0);
  }, []);

  const seek = useCallback(
    (seconds: number) => {
      if (!audio) return;
      audio.currentTime = seconds;
      setPosition(seconds);
    },
    [audio],
  );

  useTrackSource(audio, current, baseUrl, setIsPlaying);
  usePlaybackTelemetry({
    audio,
    baseUrl,
    current,
    isPlaying,
    position,
    duration,
  });
  useAudioEvents({
    audio,
    currentIndex: index,
    repeat,
    pickNextIndex,
    setIndex,
    setIsPlaying,
    setPosition,
    setDuration,
  });

  // Clamp or stop after queue mutation. Clearing also releases the source so
  // the browser cannot continue buffered playback in the background.
  useEffect(() => {
    if (queue.length === 0) {
      setIsPlaying(false);
      if (audio) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      }
      return;
    }
    if (index >= queue.length) setIndex(queue.length - 1);
  }, [audio, queue.length, index]);

  useMediaSession({
    current,
    baseUrl,
    toggle,
    previous: prev,
    next,
  });

  const value = useMemo<PlayerState>(
    () => ({
      queue,
      index,
      current,
      isPlaying,
      position,
      duration,
      shuffle,
      repeat,
      volume,
      play,
      playAlbum,
      toggle,
      next,
      prev,
      seek,
      jumpTo,
      removeFromQueue,
      clearQueue,
      addToQueue,
      playNext,
      toggleShuffle,
      cycleRepeat,
      setTrackLikedInQueue,
      setVolume,
      sleepTimerExpiresAt,
      setSleepTimer,
    }),
    [
      queue,
      index,
      current,
      isPlaying,
      position,
      duration,
      shuffle,
      repeat,
      volume,
      play,
      playAlbum,
      toggle,
      next,
      prev,
      seek,
      jumpTo,
      removeFromQueue,
      clearQueue,
      addToQueue,
      playNext,
      toggleShuffle,
      cycleRepeat,
      setTrackLikedInQueue,
      setVolume,
      sleepTimerExpiresAt,
      setSleepTimer,
    ],
  );

  return (
    <PlaybackAudioProvider audio={audio}>
      <Ctx.Provider value={value}>{children}</Ctx.Provider>
    </PlaybackAudioProvider>
  );
}

export function usePlayer(): PlayerState {
  const value = useContext(Ctx);
  if (!value) throw new Error("usePlayer must be used inside PlayerProvider");
  return value;
}
