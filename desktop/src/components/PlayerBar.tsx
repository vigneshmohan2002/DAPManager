import { useEffect, useState } from "react";
import { albumCoverUrl, backendUrl, streamUrl } from "../lib/api";
import { useWaveformPeaks } from "../lib/useWaveformPeaks";
import { enterMiniPlayer } from "../lib/window";
import { usePlayer } from "../player/PlayerContext";
import WaveformSeeker from "./WaveformSeeker";

function fmt(secs: number): string {
  if (!isFinite(secs) || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

type Props = {
  queueOpen: boolean;
  onToggleQueue: () => void;
  lyricsOpen: boolean;
  onToggleLyrics: () => void;
};

export default function PlayerBar({
  queueOpen,
  onToggleQueue,
  lyricsOpen,
  onToggleLyrics,
}: Props) {
  const {
    current,
    isPlaying,
    position,
    duration,
    toggle,
    next,
    prev,
    seek,
    shuffle,
    repeat,
    toggleShuffle,
    cycleRepeat,
    sleepTimerExpiresAt,
    setSleepTimer,
  } = usePlayer();
  const [base, setBase] = useState("");
  const [sleepMenuOpen, setSleepMenuOpen] = useState(false);
  // Re-render periodically so the "minutes left" badge stays current
  // while a timer runs; the actual pause is driven by the context.
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    backendUrl().then(setBase);
  }, []);

  useEffect(() => {
    if (sleepTimerExpiresAt === null) return;
    const id = window.setInterval(() => setNowTick(Date.now()), 30000);
    return () => window.clearInterval(id);
  }, [sleepTimerExpiresAt]);

  const sleepMinutesLeft =
    sleepTimerExpiresAt !== null
      ? Math.max(0, Math.ceil((sleepTimerExpiresAt - nowTick) / 60000))
      : null;

  const progress = duration > 0 ? (position / duration) * 100 : 0;
  const cover =
    current?.albumId && base ? albumCoverUrl(base, current.albumId) : null;
  const peaks = useWaveformPeaks(
    current && base ? streamUrl(base, current.mbid) : null,
    current?.mbid ?? null,
  );

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
          {peaks ? (
            <WaveformSeeker
              peaks={peaks}
              position={position}
              duration={duration}
              onSeek={seek}
            />
          ) : (
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
          )}
          <span className="w-9 tabular-nums">{fmt(duration)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
        <button
          onClick={toggleShuffle}
          aria-label="Shuffle"
          aria-pressed={shuffle}
          title={shuffle ? "Shuffle on" : "Shuffle off"}
          className={`w-9 h-9 rounded-md text-sm hover:text-[var(--color-text)] ${
            shuffle
              ? "text-[var(--color-accent)]"
              : "hover:bg-[var(--color-surface)]/60"
          }`}
        >
          ⇄
        </button>
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
        <button
          onClick={cycleRepeat}
          aria-label={`Repeat: ${repeat}`}
          aria-pressed={repeat !== "off"}
          title={
            repeat === "off"
              ? "Repeat off"
              : repeat === "all"
                ? "Repeat all"
                : "Repeat one"
          }
          className={`relative w-9 h-9 rounded-md text-sm hover:text-[var(--color-text)] ${
            repeat !== "off"
              ? "text-[var(--color-accent)]"
              : "hover:bg-[var(--color-surface)]/60"
          }`}
        >
          ↻
          {repeat === "one" && (
            <span className="absolute top-0.5 right-1.5 text-[9px] font-bold">
              1
            </span>
          )}
        </button>
        <div className="relative">
          <button
            onClick={() => setSleepMenuOpen((o) => !o)}
            aria-label="Sleep timer"
            aria-pressed={sleepTimerExpiresAt !== null}
            title={
              sleepMinutesLeft !== null
                ? `Sleep timer: ~${sleepMinutesLeft} min left`
                : "Sleep timer"
            }
            className={`relative w-9 h-9 rounded-md text-sm hover:text-[var(--color-text)] ${
              sleepTimerExpiresAt !== null
                ? "text-[var(--color-accent)]"
                : "hover:bg-[var(--color-surface)]/60"
            }`}
          >
            ☾
            {sleepMinutesLeft !== null && (
              <span className="absolute top-0.5 right-0.5 text-[9px] font-bold">
                {sleepMinutesLeft}
              </span>
            )}
          </button>
          {sleepMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-10"
                onClick={() => setSleepMenuOpen(false)}
              />
              <div className="absolute bottom-full right-0 mb-1 z-20 w-32 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-lg">
                {[15, 30, 45, 60].map((min) => (
                  <button
                    key={min}
                    onClick={() => {
                      setSleepTimer(min * 60000);
                      setSleepMenuOpen(false);
                    }}
                    className="block w-full px-3 py-1 text-left text-sm hover:bg-[var(--color-bg-elevated)]"
                  >
                    {min} min
                  </button>
                ))}
                <button
                  onClick={() => {
                    setSleepTimer(null);
                    setSleepMenuOpen(false);
                  }}
                  disabled={sleepTimerExpiresAt === null}
                  className="block w-full px-3 py-1 text-left text-sm hover:bg-[var(--color-bg-elevated)] disabled:opacity-40"
                >
                  Off
                </button>
              </div>
            </>
          )}
        </div>
        <button
          onClick={onToggleLyrics}
          aria-label={lyricsOpen ? "Hide lyrics" : "Show lyrics"}
          aria-pressed={lyricsOpen}
          disabled={!current}
          title="Lyrics"
          className={`w-9 h-9 rounded-md ml-1 text-sm hover:text-[var(--color-text)] disabled:opacity-40 ${
            lyricsOpen
              ? "bg-[var(--color-surface)] text-[var(--color-text)]"
              : "hover:bg-[var(--color-surface)]/60"
          }`}
        >
          ♪
        </button>
        <button
          onClick={onToggleQueue}
          aria-label={queueOpen ? "Hide queue" : "Show queue"}
          aria-pressed={queueOpen}
          className={`w-9 h-9 rounded-md text-sm hover:text-[var(--color-text)] ${
            queueOpen
              ? "bg-[var(--color-surface)] text-[var(--color-text)]"
              : "hover:bg-[var(--color-surface)]/60"
          }`}
        >
          ☰
        </button>
        <button
          onClick={() => {
            enterMiniPlayer().catch(() => {});
          }}
          aria-label="Enter mini-player"
          className="w-9 h-9 rounded-md text-sm hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/60"
        >
          ⤡
        </button>
      </div>
    </footer>
  );
}
