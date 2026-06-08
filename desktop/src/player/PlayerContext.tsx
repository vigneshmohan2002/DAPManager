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
  albumCoverUrl,
  backendUrl,
  recordPlay,
  streamUrl,
  type Track,
} from "../lib/api";

export type PlayerTrack = Track & { albumId: string | null };

export type RepeatMode = "off" | "all" | "one";

type PlayerState = {
  queue: PlayerTrack[];
  index: number;
  current: PlayerTrack | null;
  isPlaying: boolean;
  position: number;
  duration: number;
  shuffle: boolean;
  repeat: RepeatMode;
  play: (queue: PlayerTrack[], startIndex?: number) => void;
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
  // heart-toggle path so the queue panel doesn't drift out of sync
  // with the rest of the app. No-op when the mbid isn't queued.
  setTrackLikedInQueue: (mbid: string, liked: boolean) => void;
  // Sleep timer expiry (epoch ms) or null when no timer is set. The
  // player auto-pauses when Date.now() reaches the target.
  sleepTimerExpiresAt: number | null;
  // Set a sleep timer for ``durationMs`` ms from now. ``null``
  // cancels any running timer immediately.
  setSleepTimer: (durationMs: number | null) => void;
};

const LS_SHUFFLE = "dap.player.shuffle";
const LS_REPEAT = "dap.player.repeat";
const LS_QUEUE = "dap.player.queue";
// Bumped if the persisted-queue shape ever changes incompatibly so old
// blobs are dropped silently instead of crashing the boot.
const LS_QUEUE_VERSION = 1;

function loadShuffle(): boolean {
  try {
    return localStorage.getItem(LS_SHUFFLE) === "1";
  } catch {
    return false;
  }
}

function loadRepeat(): RepeatMode {
  try {
    const v = localStorage.getItem(LS_REPEAT);
    return v === "all" || v === "one" ? v : "off";
  } catch {
    return "off";
  }
}

type PersistedQueue = {
  v: number;
  queue: PlayerTrack[];
  index: number;
};

function loadQueue(): PersistedQueue {
  // Default to empty queue; any parse / shape failure also falls back
  // to empty so a corrupted localStorage entry doesn't crash boot.
  const empty: PersistedQueue = { v: LS_QUEUE_VERSION, queue: [], index: 0 };
  try {
    const raw = localStorage.getItem(LS_QUEUE);
    if (!raw) return empty;
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.v !== LS_QUEUE_VERSION) return empty;
    const queue = Array.isArray(parsed.queue) ? parsed.queue : [];
    // Re-validate each row's shape minimally — a stale dump from a
    // pre-Stage-11f Track type (no is_liked) is fine, but a totally
    // mismatched payload should drop.
    const valid = queue.every(
      (t: unknown) =>
        typeof t === "object" &&
        t !== null &&
        typeof (t as PlayerTrack).mbid === "string" &&
        typeof (t as PlayerTrack).title === "string",
    );
    if (!valid) return empty;
    const idx = typeof parsed.index === "number" ? parsed.index : 0;
    return { v: LS_QUEUE_VERSION, queue, index: Math.max(0, Math.min(idx, queue.length - 1)) };
  } catch {
    return empty;
  }
}

const Ctx = createContext<PlayerState | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  if (!audioRef.current && typeof Audio !== "undefined") {
    audioRef.current = new Audio();
    audioRef.current.preload = "auto";
  }

  // Hydrate from localStorage so the queue (and current index)
  // survives a window reload — matches Spotify's behavior of picking
  // back up where the user left off, minus playback state since
  // browser autoplay restrictions block resuming without a user
  // gesture anyway.
  const persisted = loadQueue();
  const [queue, setQueue] = useState<PlayerTrack[]>(persisted.queue);
  const [index, setIndex] = useState(persisted.index);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [base, setBase] = useState<string>("");
  const [shuffle, setShuffle] = useState<boolean>(loadShuffle);
  const [repeat, setRepeat] = useState<RepeatMode>(loadRepeat);
  // Sleep timer is intentionally NOT persisted — a fresh window
  // shouldn't surprise the user with an old timer auto-firing.
  const [sleepTimerExpiresAt, setSleepTimerExpiresAt] = useState<
    number | null
  >(null);

  // Pool of queue indices left to play in the current shuffle cycle.
  // Held in a ref so picking the next track doesn't itself trigger a
  // re-render, and so the pool survives between sequential ``next()``
  // calls inside the same React tick.
  const shufflePoolRef = useRef<Set<number>>(new Set());

  useEffect(() => {
    try {
      localStorage.setItem(LS_SHUFFLE, shuffle ? "1" : "0");
    } catch {
      /* private mode etc. — silently fall back to session-only */
    }
  }, [shuffle]);

  useEffect(() => {
    try {
      localStorage.setItem(LS_REPEAT, repeat);
    } catch {
      /* see above */
    }
  }, [repeat]);

  // Persist queue + index whenever either changes. Writes are
  // batched naturally by React's render cycle — even a multi-track
  // playNext lands in one setItem because the effect runs after the
  // render that committed the new queue.
  useEffect(() => {
    try {
      const payload: PersistedQueue = {
        v: LS_QUEUE_VERSION,
        queue,
        index,
      };
      localStorage.setItem(LS_QUEUE, JSON.stringify(payload));
    } catch {
      /* over-quota / private mode — fall back to session-only */
    }
  }, [queue, index]);

  // Fire the sleep timer: pause playback once the absolute expiry is
  // reached, then clear the timer so the UI returns to "no timer". A
  // single setTimeout for the exact remaining time avoids polling; it
  // re-arms whenever the expiry changes (set/cancel/extend).
  useEffect(() => {
    if (sleepTimerExpiresAt === null) return;
    const fire = () => {
      audioRef.current?.pause();
      setSleepTimerExpiresAt(null);
    };
    const remaining = sleepTimerExpiresAt - Date.now();
    if (remaining <= 0) {
      fire();
      return;
    }
    const id = window.setTimeout(fire, remaining);
    return () => window.clearTimeout(id);
  }, [sleepTimerExpiresAt]);

  useEffect(() => {
    let cancelled = false;
    backendUrl().then((u) => {
      if (!cancelled) setBase(u);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const current = queue[index] ?? null;

  // Tracks the mbid we've already scrobbled for the current load. A new
  // load (track change in the queue) clears it; a replay of the same
  // track via re-queueing will re-record because the load effect runs
  // again. Using a ref so timeupdate-rate state changes don't re-render.
  const scrobbledRef = useRef<string | null>(null);

  // Wall-clock ms the audio has actually been playing on this load.
  // Forward seeks don't accumulate (we only count time the audio was
  // unpaused); the server caps the value defensively anyway. Cleared
  // on every track-load via the same effect that resets scrobbledRef.
  const listenedMsRef = useRef(0);

  // Load new src when current track changes.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !current || !base) return;
    audio.src = streamUrl(base, current.mbid);
    scrobbledRef.current = null;
    listenedMsRef.current = 0;
    audio.play().catch(() => setIsPlaying(false));
    setIsPlaying(true);
  }, [current?.mbid, base]);

  // Accumulate wall-clock listening time only while the audio is
  // actually playing. Effect runs on every isPlaying transition: the
  // cleanup captures the elapsed span when playback flips false (pause
  // or track change), so a paused track that's later resumed picks
  // back up at the next true→cleanup edge instead of losing the prior
  // span. Track changes blow away the accumulator via the load effect.
  useEffect(() => {
    if (!isPlaying) return;
    const start = Date.now();
    return () => {
      listenedMsRef.current += Date.now() - start;
    };
  }, [isPlaying, current?.mbid]);

  // Scrobble — record one play event per loaded track once the user
  // has heard enough of it. The threshold mirrors the long-standing
  // Last.fm convention so user expectations transfer: ≥30 seconds
  // listened, OR ≥50% of the track's duration, whichever comes first.
  // Scrubbing past the threshold counts; that's fine — the user did
  // engage with the track to that extent. Fire-and-forget on the
  // network side; the recordPlay helper swallows transient failures.
  useEffect(() => {
    if (!current) return;
    if (scrobbledRef.current === current.mbid) return;
    if (position < 30 && (duration <= 0 || position / duration < 0.5)) return;
    scrobbledRef.current = current.mbid;
    // At threshold time the cleanup hasn't fired yet, so the running
    // span isn't in listenedMsRef. Add the live delta so the very
    // first scrobble for a load reflects real listening time, not 0.
    const liveMs = isPlaying ? Date.now() - (lastPlayStartRef.current ?? Date.now()) : 0;
    recordPlay(current.mbid, "desktop", listenedMsRef.current + liveMs);
  }, [current, position, duration, isPlaying]);

  // Companion ref to the accumulator above — captures the wall-clock
  // start of the current playing span so the scrobble effect can
  // include in-flight ms without waiting for the next cleanup tick.
  const lastPlayStartRef = useRef<number | null>(null);
  useEffect(() => {
    lastPlayStartRef.current = isPlaying ? Date.now() : null;
  }, [isPlaying, current?.mbid]);

  // Returns the queue index that should play next, or null when there's
  // nothing left and the player should stop. Auto-advance (track ended)
  // with ``repeat="one"`` returns the current index so the caller knows
  // to seek-to-0 instead of changing tracks.
  const pickNextIndex = useCallback(
    (reason: "user" | "auto"): number | null => {
      if (queue.length === 0) return null;
      if (reason === "auto" && repeat === "one") return index;

      if (shuffle) {
        let pool = shufflePoolRef.current;
        if (pool.size === 0) {
          if (repeat === "all") {
            // Refill but exclude the current index so we don't pick the
            // same track we just finished as the very next one.
            pool = new Set(
              queue.map((_, i) => i).filter((i) => i !== index),
            );
            shufflePoolRef.current = pool;
          } else {
            return null;
          }
        }
        if (pool.size === 0) return null; // single-track queue + repeat=all
        const arr = Array.from(pool);
        const target = arr[Math.floor(Math.random() * arr.length)];
        pool.delete(target);
        return target;
      }

      if (index + 1 < queue.length) return index + 1;
      if (repeat === "all") return 0;
      return null;
    },
    [queue, shuffle, repeat, index],
  );

  // Whenever the queue identity or shuffle mode changes, reset the
  // shuffle pool so new tracks are eligible and removed ones can't be
  // picked. Excluding the current index keeps the immediate next-pick
  // from being a same-track repeat.
  useEffect(() => {
    if (!shuffle) {
      shufflePoolRef.current = new Set();
      return;
    }
    shufflePoolRef.current = new Set(
      queue.map((_, i) => i).filter((i) => i !== index),
    );
    // Intentionally omits `index` — we don't want a brand-new pool every
    // time playback advances, only on identity-changing events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queue, shuffle]);

  // Wire <audio> events → state.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onTime = () => setPosition(audio.currentTime);
    const onDur = () => setDuration(audio.duration || 0);
    const onEnded = () => {
      const target = pickNextIndex("auto");
      if (target === null) {
        setIsPlaying(false);
        return;
      }
      if (target === index && repeat === "one") {
        // Same track again — don't change index (the src-load effect
        // only fires on mbid change), just seek and replay.
        audio.currentTime = 0;
        audio.play().catch(() => setIsPlaying(false));
        return;
      }
      setIndex(target);
    };

    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("timeupdate", onTime);
    audio.addEventListener("durationchange", onDur);
    audio.addEventListener("ended", onEnded);
    return () => {
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("timeupdate", onTime);
      audio.removeEventListener("durationchange", onDur);
      audio.removeEventListener("ended", onEnded);
    };
  }, [queue.length, index, repeat, pickNextIndex]);

  const play = useCallback((q: PlayerTrack[], startIndex = 0) => {
    setQueue(q);
    setIndex(Math.max(0, Math.min(startIndex, q.length - 1)));
  }, []);

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !current) return;
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  }, [current]);

  const next = useCallback(() => {
    const target = pickNextIndex("user");
    if (target !== null) setIndex(target);
  }, [pickNextIndex]);

  const prev = useCallback(() => {
    const audio = audioRef.current;
    if (audio && audio.currentTime > 3) {
      audio.currentTime = 0;
      return;
    }
    // Sequential "previous" even when shuffle is on — matches Spotify's
    // behavior of letting the user step back through the visible queue
    // order. A future iteration could maintain a played-history stack
    // and pop from that instead.
    setIndex((i) => Math.max(0, i - 1));
  }, []);

  const toggleShuffle = useCallback(() => {
    setShuffle((s) => !s);
  }, []);

  const cycleRepeat = useCallback(() => {
    setRepeat((r) => (r === "off" ? "all" : r === "all" ? "one" : "off"));
  }, []);

  // Arm a sleep timer ``durationMs`` from now, or cancel it with null /
  // a non-positive duration. We store an absolute expiry (not a remaining
  // duration) so the firing effect doesn't drift across re-renders.
  const setSleepTimer = useCallback((durationMs: number | null) => {
    setSleepTimerExpiresAt(
      durationMs === null || durationMs <= 0 ? null : Date.now() + durationMs,
    );
  }, []);

  const addToQueue = useCallback(
    (tracks: PlayerTrack | PlayerTrack[]) => {
      const items = Array.isArray(tracks) ? tracks : [tracks];
      if (items.length === 0) return;
      setQueue((q) => {
        const startEmpty = q.length === 0;
        const next = q.concat(items);
        if (startEmpty) setIndex(0);
        return next;
      });
    },
    [],
  );

  const setTrackLikedInQueue = useCallback((mbid: string, liked: boolean) => {
    setQueue((q) => {
      // Bail when nothing matches — avoids forcing a React re-render
      // on every heart-toggle from screens that aren't currently
      // playing anything from the queue.
      if (!q.some((t) => t.mbid === mbid && Boolean(t.is_liked) !== liked)) {
        return q;
      }
      return q.map((t) =>
        t.mbid === mbid ? { ...t, is_liked: liked } : t,
      );
    });
  }, []);

  const playNext = useCallback(
    (tracks: PlayerTrack | PlayerTrack[]) => {
      const items = Array.isArray(tracks) ? tracks : [tracks];
      if (items.length === 0) return;
      setQueue((q) => {
        if (q.length === 0) {
          setIndex(0);
          return items;
        }
        const insertAt = Math.min(index + 1, q.length);
        return [...q.slice(0, insertAt), ...items, ...q.slice(insertAt)];
      });
    },
    [index],
  );

  const jumpTo = useCallback(
    (target: number) => {
      setIndex((i) => {
        if (target < 0 || target >= queue.length) return i;
        return target;
      });
    },
    [queue.length],
  );

  // Removing the currently-playing track jumps to the next one — or
  // stops playback if it was the last. Removing something earlier in
  // the queue has to decrement `index` so the current track doesn't
  // change identity under us.
  const removeFromQueue = useCallback((target: number) => {
    setQueue((q) => {
      if (target < 0 || target >= q.length) return q;
      return [...q.slice(0, target), ...q.slice(target + 1)];
    });
    setIndex((i) => {
      if (target < i) return i - 1;
      if (target === i) return i; // keep index; effect below clamps/stops
      return i;
    });
  }, []);

  // If the queue shrank out from under the current index, clamp it.
  useEffect(() => {
    if (queue.length === 0) {
      setIsPlaying(false);
      const audio = audioRef.current;
      if (audio) {
        audio.pause();
        audio.removeAttribute("src");
        audio.load();
      }
      return;
    }
    if (index >= queue.length) setIndex(queue.length - 1);
  }, [queue.length, index]);

  const clearQueue = useCallback(() => {
    setQueue([]);
    setIndex(0);
  }, []);

  const seek = useCallback((seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = seconds;
    setPosition(seconds);
  }, []);

  // Media Session API — lets macOS media keys + lock screen drive playback.
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator))
      return;
    const ms = navigator.mediaSession;
    if (!current || !base) {
      ms.metadata = null;
      return;
    }
    ms.metadata = new MediaMetadata({
      title: current.title,
      artist: current.artist,
      album: current.album ?? "",
      artwork: current.albumId
        ? [{ src: albumCoverUrl(base, current.albumId), sizes: "512x512" }]
        : [],
    });
    ms.setActionHandler("play", () => toggle());
    ms.setActionHandler("pause", () => toggle());
    ms.setActionHandler("previoustrack", () => prev());
    ms.setActionHandler("nexttrack", () => next());
  }, [current?.mbid, base, toggle, prev, next]);

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
      play,
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
      play,
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
      sleepTimerExpiresAt,
      setSleepTimer,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePlayer(): PlayerState {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePlayer must be used inside PlayerProvider");
  return v;
}
