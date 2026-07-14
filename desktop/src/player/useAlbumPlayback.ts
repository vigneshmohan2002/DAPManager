import { useCallback } from "react";
import { fetchAlbumTracks } from "../lib/api";
import type { PlayerTrack } from "./playerTypes";

type PlayQueue = (queue: PlayerTrack[], startIndex?: number) => void;

export function useAlbumPlayback(play: PlayQueue) {
  return useCallback(
    async (albumId: string): Promise<number> => {
      const tracks = await fetchAlbumTracks(albumId);
      if (tracks.length === 0) return 0;
      play(
        tracks.map((track) => ({ ...track, albumId })),
        0,
      );
      return tracks.length;
    },
    [play],
  );
}
