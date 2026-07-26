import { useEffect, useState } from "react";
import { albumCoverUrl, backendUrl, setTrackLiked } from "../lib/api";
import { usePlayer } from "../player/PlayerContext";
import Artwork from "./Artwork";
import Icon from "./Icon";
import { useToast } from "./Toast";

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function QueuePanel({ open, onClose }: Props) {
  const {
    queue,
    index,
    isPlaying,
    jumpTo,
    removeFromQueue,
    clearQueue,
    setTrackLikedInQueue,
  } = usePlayer();
  const toast = useToast();
  const [base, setBase] = useState("");

  useEffect(() => {
    if (!open) return;

    let cancelled = false;
    backendUrl()
      .then((url) => {
        if (!cancelled) setBase(url);
      })
      .catch(() => {
        // Text-only queue rows remain fully usable while the backend starts.
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;

  const handleLikeToggle = async (mbid: string, wasLiked: boolean) => {
    const next = !wasLiked;
    setTrackLikedInQueue(mbid, next);
    try {
      const result = await setTrackLiked(mbid, next);
      if (result.success) return;
      setTrackLikedInQueue(mbid, wasLiked);
      toast.show(result.message ?? "Could not save like", "err");
    } catch (error) {
      setTrackLikedInQueue(mbid, wasLiked);
      toast.show(`Could not save like: ${String(error)}`, "err");
    }
    // Auto-created Liked Songs playlist refresh is intentionally
    // skipped here — the queue panel doesn't own the playlists-
    // version counter. Users will see the pin appear next time the
    // sidebar refreshes (next playlist mutation or screen change).
  };

  return (
    <aside
      aria-label="Playback queue"
      className="w-[21rem] min-w-[18rem] max-w-[22rem] shrink-0 border-l border-[var(--color-border)] bg-[var(--color-bg-elevated)] flex flex-col"
    >
      <header className="h-[54px] shrink-0 border-b border-[var(--color-border)] flex items-center px-4 gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold tracking-[-0.01em]">
            Up Next
          </div>
          <div className="text-[11px] leading-4 text-[var(--color-text-muted)]">
            {queue.length} {queue.length === 1 ? "track" : "tracks"}
          </div>
        </div>
        <button
          type="button"
          onClick={clearQueue}
          disabled={queue.length === 0}
          className="doppler-control h-7 rounded-md px-2 text-[11px] font-medium"
        >
          Clear
        </button>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close queue"
          className="doppler-control grid h-7 w-7 place-items-center rounded-md"
        >
          <Icon name="close" size={14} />
        </button>
      </header>
      <div className="flex-1 overflow-y-auto">
        {queue.length === 0 ? (
          <div className="grid min-h-40 place-items-center px-6 py-8 text-center">
            <div>
              <Icon
                name="queue"
                size={24}
                className="mx-auto mb-2 opacity-45"
              />
              <p className="text-[13px] font-medium">Nothing queued</p>
              <p className="mt-1 text-[11px] leading-4 text-[var(--color-text-muted)]">
                Pick an album or song to start.
              </p>
            </div>
          </div>
        ) : (
          <ol className="space-y-0.5 p-2">
            {queue.map((t, i) => {
              const isCurrent = i === index;
              const cover =
                t.albumId && base ? albumCoverUrl(base, t.albumId) : null;
              return (
                <li
                  key={`${t.mbid}-${i}`}
                  aria-current={isCurrent ? "true" : undefined}
                  className={`group relative flex min-h-[52px] items-center gap-2.5 rounded-md px-2 py-1.5 cursor-pointer transition-colors ${
                    isCurrent
                      ? "doppler-selection"
                      : "hover:bg-[color-mix(in_srgb,var(--color-surface)_58%,transparent)]"
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => jumpTo(i)}
                    aria-label={`Play ${t.title} by ${t.artist}`}
                    className="flex min-w-0 flex-1 items-center gap-2.5 rounded text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
                  >
                    <Artwork
                      src={cover}
                      alt={`${t.title} cover`}
                      fallbackLabel="♪"
                      className="h-10 w-10 shrink-0 rounded-[5px] shadow-[var(--shadow-artwork)]"
                    />
                    <div className="min-w-0 flex-1">
                      <div
                        className={`truncate text-[12px] font-medium leading-[17px] ${
                          isCurrent ? "text-[var(--color-accent)]" : ""
                        }`}
                      >
                        {t.title}
                      </div>
                      <div className="truncate text-[11px] leading-4 text-[var(--color-text-muted)]">
                        {t.artist}
                      </div>
                    </div>
                    {isCurrent && isPlaying ? (
                      <span
                        aria-label="Now playing"
                        className="mr-0.5 text-[12px] text-[var(--color-accent)]"
                      >
                        ♪
                      </span>
                    ) : null}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleLikeToggle(t.mbid, Boolean(t.is_liked));
                    }}
                    aria-label={t.is_liked ? "Unlike" : "Like"}
                    aria-pressed={Boolean(t.is_liked)}
                    className={`doppler-control grid h-7 w-7 shrink-0 place-items-center rounded-md ${
                      t.is_liked
                        ? "text-[var(--color-liked)]"
                        : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    }`}
                  >
                    <Icon
                      name="heart"
                      size={14}
                      className={t.is_liked ? "fill-current" : ""}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFromQueue(i);
                    }}
                    aria-label="Remove from queue"
                    className="doppler-control grid h-7 w-7 shrink-0 place-items-center rounded-md opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  >
                    <Icon name="close" size={13} />
                  </button>
                </li>
              );
            })}
          </ol>
        )}
      </div>
    </aside>
  );
}
