import { useEffect, useRef } from "react";
import { recordPlay } from "../lib/api";
import type { PlayerTrack } from "./playerTypes";

type PlaybackTelemetryOptions = {
  audio: HTMLAudioElement | null;
  baseUrl: string;
  current: PlayerTrack | null;
  isPlaying: boolean;
  position: number;
  duration: number;
};

export function hasReachedScrobbleThreshold(
  position: number,
  duration: number,
): boolean {
  return !(
    position < 30 &&
    (duration <= 0 || position / duration < 0.5)
  );
}

export function usePlaybackTelemetry({
  audio,
  baseUrl,
  current,
  isPlaying,
  position,
  duration,
}: PlaybackTelemetryOptions): void {
  const scrobbledTrackRef = useRef<string | null>(null);
  const listenedMsRef = useRef(0);
  const lastPlayStartRef = useRef<number | null>(null);

  useEffect(() => {
    // Match source loading exactly: telemetry belongs to a loaded source,
    // rather than merely to a queue selection awaiting its backend URL.
    if (!audio || !current || !baseUrl) return;
    scrobbledTrackRef.current = null;
    listenedMsRef.current = 0;
  }, [audio, current?.mbid, baseUrl]);

  useEffect(() => {
    if (!isPlaying) return;
    const start = Date.now();
    return () => {
      listenedMsRef.current += Date.now() - start;
    };
  }, [isPlaying, current?.mbid]);

  useEffect(() => {
    if (!current) return;
    if (scrobbledTrackRef.current === current.mbid) return;
    if (!hasReachedScrobbleThreshold(position, duration)) return;

    scrobbledTrackRef.current = current.mbid;
    const liveMs = isPlaying
      ? Date.now() - (lastPlayStartRef.current ?? Date.now())
      : 0;
    void recordPlay(
      current.mbid,
      "desktop",
      listenedMsRef.current + liveMs,
    );
  }, [current, position, duration, isPlaying]);

  useEffect(() => {
    lastPlayStartRef.current = isPlaying ? Date.now() : null;
  }, [isPlaying, current?.mbid]);
}
