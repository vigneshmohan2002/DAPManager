export default function PlayerBar() {
  return (
    <footer className="h-20 shrink-0 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] flex items-center px-6 gap-4">
      <div className="w-12 h-12 rounded bg-[var(--color-surface)] shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate text-[var(--color-text-muted)]">
          Nothing playing
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          Select a track to start playback
        </div>
      </div>
      <div className="flex items-center gap-3 text-[var(--color-text-muted)]">
        <button
          className="w-8 h-8 rounded-full hover:text-[var(--color-text)]"
          aria-label="Previous"
          disabled
        >
          ⏮
        </button>
        <button
          className="w-10 h-10 rounded-full bg-[var(--color-surface)] hover:bg-[var(--color-surface)]/70 flex items-center justify-center"
          aria-label="Play"
          disabled
        >
          ▶
        </button>
        <button
          className="w-8 h-8 rounded-full hover:text-[var(--color-text)]"
          aria-label="Next"
          disabled
        >
          ⏭
        </button>
      </div>
    </footer>
  );
}
