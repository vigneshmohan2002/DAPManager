import type { Album } from "./api/types";

export function albumDisplayArtist(
  album: Pick<Album, "artist" | "primary_artist">,
): string {
  return album.primary_artist ?? album.artist;
}
