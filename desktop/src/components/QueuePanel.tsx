import { usePlayer } from "../player/PlayerContext";

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function QueuePanel({ open, onClose }: Props) {
  const { queue, index, isPlaying, jumpTo, removeFromQueue, clearQueue } =
    usePlayer();

  if (!open) return null;

  return (
    <aside className="w-80 shrink-0 bg-[var(--color-bg-sidebar)] border-l border-[var(--color-border)] flex flex-col">
      <header className="h-14 shrink-0 border-b border-[var(--color-border)] flex items-center px-4 gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">Up Next</div>
          <div className="text-xs text-[var(--color-text-muted)]">
            {queue.length} {queue.length === 1 ? "track" : "tracks"}
          </div>
        </div>
        <button
          onClick={clearQueue}
          disabled={queue.length === 0}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-40"
        >
          Clear
        </button>
        <button
          onClick={onClose}
          aria-label="Close queue"
          className="w-7 h-7 rounded hover:bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          ×
        </button>
      </header>
      <div className="flex-1 overflow-y-auto">
        {queue.length === 0 ? (
          <div className="px-4 py-6 text-sm text-[var(--color-text-muted)]">
            Nothing queued. Pick an album or song to start.
          </div>
        ) : (
          <ol className="divide-y divide-[var(--color-border)]/40">
            {queue.map((t, i) => {
              const isCurrent = i === index;
              return (
                <li
                  key={`${t.mbid}-${i}`}
                  onClick={() => jumpTo(i)}
                  className={`group flex items-center gap-2 px-3 py-2 cursor-pointer ${
                    isCurrent
                      ? "bg-[var(--color-surface)]"
                      : "hover:bg-[var(--color-surface)]/60"
                  }`}
                >
                  <div className="w-6 text-center text-xs text-[var(--color-text-muted)] tabular-nums">
                    {isCurrent && isPlaying ? (
                      <span className="text-[var(--color-accent)]">♪</span>
                    ) : (
                      i + 1
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div
                      className={`text-sm truncate ${isCurrent ? "text-[var(--color-accent)]" : ""}`}
                    >
                      {t.title}
                    </div>
                    <div className="text-xs text-[var(--color-text-muted)] truncate">
                      {t.artist}
                    </div>
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      removeFromQueue(i);
                    }}
                    aria-label="Remove from queue"
                    className="opacity-0 group-hover:opacity-100 w-6 h-6 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg)]/60"
                  >
                    ×
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
