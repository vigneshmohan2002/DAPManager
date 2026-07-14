import {
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import { backendUrl, streamUrl } from "../lib/api";
import type { NextTrackReason, PlayerTrack, RepeatMode } from "./playerTypes";

type StateSetter<T> = Dispatch<SetStateAction<T>>;

export type PickNextIndex = (reason: NextTrackReason) => number | null;

export function createAudioElement(): HTMLAudioElement | null {
  if (typeof Audio === "undefined") return null;
  const audio = new Audio();
  audio.preload = "auto";
  return audio;
}

export function useBackendBaseUrl(): string {
  const [baseUrl, setBaseUrl] = useState("");

  useEffect(() => {
    let cancelled = false;
    void backendUrl().then((url) => {
      if (!cancelled) setBaseUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return baseUrl;
}

export function useTrackSource(
  audio: HTMLAudioElement | null,
  current: PlayerTrack | null,
  baseUrl: string,
  setIsPlaying: StateSetter<boolean>,
): void {
  useEffect(() => {
    if (!audio || !current || !baseUrl) return;
    audio.src = streamUrl(baseUrl, current.mbid);
    void audio.play().catch(() => setIsPlaying(false));
    setIsPlaying(true);
  }, [audio, current?.mbid, baseUrl, setIsPlaying]);
}

type AudioEventOptions = {
  audio: HTMLAudioElement | null;
  currentIndex: number;
  repeat: RepeatMode;
  pickNextIndex: PickNextIndex;
  setIndex: StateSetter<number>;
  setIsPlaying: StateSetter<boolean>;
  setPosition: StateSetter<number>;
  setDuration: StateSetter<number>;
};

export function useAudioEvents({
  audio,
  currentIndex,
  repeat,
  pickNextIndex,
  setIndex,
  setIsPlaying,
  setPosition,
  setDuration,
}: AudioEventOptions): void {
  useEffect(() => {
    if (!audio) return;

    const onPlay = () => setIsPlaying(true);
    const onPause = () => setIsPlaying(false);
    const onTimeUpdate = () => setPosition(audio.currentTime);
    const onDurationChange = () => setDuration(audio.duration || 0);
    const onEnded = () => {
      const targetIndex = pickNextIndex("auto");
      if (targetIndex === null) {
        setIsPlaying(false);
        return;
      }
      if (targetIndex === currentIndex && repeat === "one") {
        audio.currentTime = 0;
        void audio.play().catch(() => setIsPlaying(false));
        return;
      }
      setIndex(targetIndex);
    };

    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("durationchange", onDurationChange);
    audio.addEventListener("ended", onEnded);
    return () => {
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("durationchange", onDurationChange);
      audio.removeEventListener("ended", onEnded);
    };
  }, [
    audio,
    currentIndex,
    repeat,
    pickNextIndex,
    setIndex,
    setIsPlaying,
    setPosition,
    setDuration,
  ]);
}
