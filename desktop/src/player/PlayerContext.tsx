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
import { albumCoverUrl, backendUrl, streamUrl, type Track } from "../lib/api";

export type PlayerTrack = Track & { albumId: string | null };

type PlayerState = {
  queue: PlayerTrack[];
  index: number;
  current: PlayerTrack | null;
  isPlaying: boolean;
  position: number;
  duration: number;
  play: (queue: PlayerTrack[], startIndex?: number) => void;
  toggle: () => void;
  next: () => void;
  prev: () => void;
  seek: (seconds: number) => void;
  jumpTo: (index: number) => void;
  removeFromQueue: (index: number) => void;
  clearQueue: () => void;
};

const Ctx = createContext<PlayerState | null>(null);

export function PlayerProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  if (!audioRef.current && typeof Audio !== "undefined") {
    audioRef.current = new Audio();
    audioRef.current.preload = "auto";
  }

  const [queue, setQueue] = useState<PlayerTrack[]>([]);
  const [index, setIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [base, setBase] = useState<string>("");

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

  // Load new src when current track changes.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !current || !base) return;
    audio.src = streamUrl(base, current.mbid);
    audio.play().catch(() => setIsPlaying(false));
    setIsPlaying(true);
  }, [current?.mbid, base]);

  // Wire <audio> events → state.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onTime = () => setPosition(audio.currentTime);
    const onDur = () => setDuration(audio.duration || 0);
    const onEnded = () => {
      setIndex((i) => (i + 1 < queue.length ? i + 1 : i));
      if (index + 1 >= queue.length) setIsPlaying(false);
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
  }, [queue.length, index]);

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
    setIndex((i) => Math.min(i + 1, queue.length - 1));
  }, [queue.length]);

  const prev = useCallback(() => {
    const audio = audioRef.current;
    if (audio && audio.currentTime > 3) {
      audio.currentTime = 0;
      return;
    }
    setIndex((i) => Math.max(0, i - 1));
  }, []);

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
      play,
      toggle,
      next,
      prev,
      seek,
      jumpTo,
      removeFromQueue,
      clearQueue,
    }),
    [
      queue,
      index,
      current,
      isPlaying,
      position,
      duration,
      play,
      toggle,
      next,
      prev,
      seek,
      jumpTo,
      removeFromQueue,
      clearQueue,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePlayer(): PlayerState {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePlayer must be used inside PlayerProvider");
  return v;
}
