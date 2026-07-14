import { useEffect } from "react";
import {
  persistQueue,
  persistRepeat,
  persistShuffle,
} from "./playerStorage";
import type { PlayerTrack, RepeatMode } from "./playerTypes";

export function usePlayerStorage(
  queue: PlayerTrack[],
  index: number,
  shuffle: boolean,
  repeat: RepeatMode,
): void {
  useEffect(() => {
    persistShuffle(shuffle);
  }, [shuffle]);

  useEffect(() => {
    persistRepeat(repeat);
  }, [repeat]);

  useEffect(() => {
    persistQueue(queue, index);
  }, [queue, index]);
}
