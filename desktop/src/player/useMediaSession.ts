import { useEffect } from "react";
import { albumCoverUrl } from "../lib/api";
import type { PlayerTrack } from "./playerTypes";

type MediaSessionOptions = {
  current: PlayerTrack | null;
  baseUrl: string;
  toggle: () => void;
  previous: () => void;
  next: () => void;
};

export function useMediaSession({
  current,
  baseUrl,
  toggle,
  previous,
  next,
}: MediaSessionOptions): void {
  useEffect(() => {
    if (typeof navigator === "undefined" || !("mediaSession" in navigator)) {
      return;
    }

    const mediaSession = navigator.mediaSession;
    if (!current || !baseUrl) {
      mediaSession.metadata = null;
      return;
    }

    mediaSession.metadata = new MediaMetadata({
      title: current.title,
      artist: current.artist,
      album: current.album ?? "",
      artwork: current.albumId
        ? [
            {
              src: albumCoverUrl(baseUrl, current.albumId),
              sizes: "512x512",
            },
          ]
        : [],
    });
    mediaSession.setActionHandler("play", () => toggle());
    mediaSession.setActionHandler("pause", () => toggle());
    mediaSession.setActionHandler("previoustrack", () => previous());
    mediaSession.setActionHandler("nexttrack", () => next());
  }, [current?.mbid, baseUrl, toggle, previous, next]);
}
