import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

const PlaybackAudioContext = createContext<HTMLAudioElement | null>(null);

export function PlaybackAudioProvider({
  audio,
  children,
}: {
  audio: HTMLAudioElement | null;
  children: ReactNode;
}) {
  return (
    <PlaybackAudioContext.Provider value={audio}>
      {children}
    </PlaybackAudioContext.Provider>
  );
}

export function clampPlaybackPosition(
  value: number,
  duration: number,
): number {
  if (!Number.isFinite(value)) return 0;
  const nonNegative = Math.max(0, value);
  if (!Number.isFinite(duration) || duration <= 0) return nonNegative;
  return Math.min(nonNegative, duration);
}

export function useSmoothPlaybackPosition({
  position,
  duration,
  isPlaying,
  trackKey,
}: {
  position: number;
  duration: number;
  isPlaying: boolean;
  trackKey: string | null;
}): number {
  const audio = useContext(PlaybackAudioContext);
  const positionRef = useRef(position);
  const [smoothPosition, setSmoothPosition] = useState(() =>
    clampPlaybackPosition(position, duration),
  );
  positionRef.current = position;

  // Paused playback and explicit seeks should remain exact even when no
  // animation loop is active.
  useEffect(() => {
    if (!isPlaying) {
      setSmoothPosition(clampPlaybackPosition(position, duration));
    }
  }, [duration, isPlaying, position]);

  // Reset when the queue advances so the previous track's final position
  // cannot linger while the new media source is being attached.
  useEffect(() => {
    setSmoothPosition(clampPlaybackPosition(positionRef.current, duration));
  }, [duration, trackKey]);

  useEffect(() => {
    if (!audio || !isPlaying || typeof requestAnimationFrame === "undefined") {
      return;
    }

    let frame = 0;
    const update = () => {
      const audioPosition = Number.isFinite(audio.currentTime)
        ? audio.currentTime
        : positionRef.current;
      const nextPosition = clampPlaybackPosition(audioPosition, duration);
      setSmoothPosition((currentPosition) =>
        Math.abs(currentPosition - nextPosition) < 0.001
          ? currentPosition
          : nextPosition,
      );
      frame = requestAnimationFrame(update);
    };

    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [audio, duration, isPlaying, trackKey]);

  return smoothPosition;
}
