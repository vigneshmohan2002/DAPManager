import { useCallback, useState } from "react";
import type { Album } from "../lib/api/types";
import { requestAlbumDownload } from "../lib/api/downloads";
import { postAction } from "../lib/api/sync";
import { useToast } from "./Toast";

const RELEASE_MBID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isExactAlbumRelease(album: Album): boolean {
  return RELEASE_MBID_PATTERN.test(album.id);
}

export function useAlbumCompletion() {
  const [completingId, setCompletingId] = useState<string | null>(null);
  const toast = useToast();

  const completeAlbum = useCallback(
    async (album: Album) => {
      if (completingId !== null) return;
      if (!isExactAlbumRelease(album)) {
        toast.show(
          "This album needs an exact MusicBrainz release ID before it can be completed safely.",
          "err",
        );
        return;
      }

      setCompletingId(album.id);
      try {
        const result = await requestAlbumDownload(album.id);
        if (!result.success || !result.request) {
          toast.show(
            result.message || "The master rejected this album request.",
            "err",
          );
          return;
        }

        if (result.request.stage === "success") {
          toast.show(`${album.title} is already complete on the master.`, "ok");
          return;
        }
        if (!result.queued) {
          toast.show(
            `${album.title} is already ${result.request.stage}.`,
            "ok",
          );
          return;
        }

        const downloader = await postAction("/api/download");
        toast.show(
          downloader.success
            ? `Completing ${album.title} — verified FLAC download started.`
            : `${album.title} is queued for completion.`,
          "ok",
        );
      } catch (error) {
        toast.show(
          error instanceof Error
            ? error.message
            : `Could not complete ${album.title}.`,
          "err",
        );
      } finally {
        setCompletingId(null);
      }
    },
    [completingId, toast],
  );

  return {
    completeAlbum,
    completingId,
  };
}
