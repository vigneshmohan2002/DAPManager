import type { Track } from "../lib/api";

export type PlayerTrack = Track & { albumId: string | null };

export type RepeatMode = "off" | "all" | "one";

export type NextTrackReason = "user" | "auto";
