import { useEffect, useState } from "react";
import { albumCoverUrl, backendUrl } from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

function fmt(secs: number): string {
  if (!isFinite(secs) || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function PlayerBar() {
  const { current, isPlaying, position, duration, toggle, next, prev, seek } =
    usePlayer();
  const [base, setBase] = useState("");

  useEffect(() => {
    backendUrl().then(setBase);
  }, []);

  const progress = duration > 0 ? (position / duration) * 100 : 0;
  const cover =
    current?.albumId && base ? albumCoverUrl(base, current.albumId) : null;

  return (
    <footer className="h-20 shrink-0 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] flex items-center px-6 gap-4">
      <div className="w-12 h-12 rounded bg-[var(--color-surface)] shrink-0 overflow-hidden">
        {cover ? (
          <img
            src={cover}
            alt=""
            className="w-full h-full object-cover"
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = "none";
            }}
          />
        ) : null}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">
          {current ? current.title : (
            <span className="text-[var(--color-text-muted)]">
              Nothing playing
            </span>
          )}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] truncate">
          {current
            ? `${current.artist}${current.album ? ` — ${current.album}` : ""}`
            : "Select a track to start playback"}
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-[var(--color-text-muted)]">
          <span className="w-9 text-right tabular-nums">{fmt(position)}</span>
          <input
            type="range"
            min={0}
            max={duration || 0}
            step={0.1}
            value={position}
            onChange={(e) => seek(Number(e.target.value))}
            disabled={!current || !duration}
            className="flex-1 accent-[var(--color-accent)]"
            style={{ background: `linear-gradient(to right, var(--color-accent) ${progress}%, #3a3a3c ${progress}%)` }}
          />
          <span className="w-9 tabular-nums">{fmt(duration)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
        <button
          onClick={prev}
          disabled={!current}
          aria-label="Previous"
          className="w-9 h-9 rounded-full hover:text-[var(--color-text)] disabled:opacity-40"
        >
          ⏮
        </button>
        <button
          onClick={toggle}
          disabled={!current}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="w-11 h-11 rounded-full bg-[var(--color-accent)] text-white flex items-center justify-center disabled:opacity-40 disabled:bg-[var(--color-surface)]"
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <button
          onClick={next}
          disabled={!current}
          aria-label="Next"
          className="w-9 h-9 rounded-full hover:text-[var(--color-text)] disabled:opacity-40"
        >
          ⏭
        </button>
      </div>
    </footer>
  );
}
