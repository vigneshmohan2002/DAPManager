import { useEffect, useRef, useState, type Ref } from "react";
import { backendUrl, streamUrl } from "../lib/api";
import { useWaveformPeaks } from "../lib/useWaveformPeaks";
import { enterMiniPlayer } from "../lib/window";
import { usePlayer } from "../player/PlayerContext";
import Icon, { type IconName } from "./Icon";
import WaveformSeeker from "./WaveformSeeker";

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

type Props = {
  queueOpen: boolean;
  onToggleQueue: () => void;
  lyricsOpen: boolean;
  onToggleLyrics: () => void;
};

type IconControlProps = {
  icon: IconName;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  title?: string;
  badge?: string | number;
  className?: string;
  expanded?: boolean;
  hasPopup?: "menu";
  buttonRef?: Ref<HTMLButtonElement>;
};

function IconControl({
  icon,
  label,
  onClick,
  active,
  disabled = false,
  title,
  badge,
  className = "",
  expanded,
  hasPopup,
  buttonRef,
}: IconControlProps) {
  return (
    <button
      ref={buttonRef}
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      aria-expanded={expanded}
      aria-haspopup={hasPopup}
      title={title}
      className={`doppler-control relative grid h-8 w-8 shrink-0 place-items-center rounded-md ${
        active ? "doppler-selection text-[var(--color-accent)]" : ""
      } ${className}`}
    >
      <Icon name={icon} size={17} />
      {badge !== undefined ? (
        <span className="pointer-events-none absolute right-0 top-0 min-w-3 rounded-full text-center text-[8px] font-bold leading-3">
          {badge}
        </span>
      ) : null}
    </button>
  );
}

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
  const sleepButtonRef = useRef<HTMLButtonElement | null>(null);
  const [nowTick, setNowTick] = useState(() => Date.now());

  useEffect(() => {
    backendUrl().then(setBase);
  }, []);

  useEffect(() => {
    if (sleepTimerExpiresAt === null) return;
    const id = window.setInterval(() => setNowTick(Date.now()), 30_000);
    return () => window.clearInterval(id);
  }, [sleepTimerExpiresAt]);

  useEffect(() => {
    if (!sleepMenuOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setSleepMenuOpen(false);
      queueMicrotask(() => sleepButtonRef.current?.focus());
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [sleepMenuOpen]);

  const sleepMinutesLeft =
    sleepTimerExpiresAt === null
      ? null
      : Math.max(0, Math.ceil((sleepTimerExpiresAt - nowTick) / 60_000));
  const progress = duration > 0 ? (position / duration) * 100 : 0;
  const peaks = useWaveformPeaks(
    current && base ? streamUrl(base, current.mbid) : null,
    current?.mbid ?? null,
  );

  return (
    <footer className="grid h-[86px] shrink-0 grid-cols-[220px_minmax(280px,1fr)_220px] items-center gap-5 border-t border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-5 shadow-[0_-1px_10px_rgb(0_0_0/3%)]">
      <div className="flex items-center justify-start gap-1.5">
        <IconControl
          icon="shuffle"
          label="Shuffle"
          onClick={toggleShuffle}
          active={shuffle}
          title={shuffle ? "Shuffle on" : "Shuffle off"}
        />
        <IconControl
          icon="previous"
          label="Previous"
          onClick={prev}
          disabled={!current}
        />
        <button
          type="button"
          onClick={toggle}
          disabled={!current}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[var(--color-text)] text-[var(--color-content)] shadow-sm transition-transform hover:scale-[1.03] active:scale-95 disabled:opacity-30"
        >
          <Icon
            name={isPlaying ? "pause" : "play"}
            size={18}
            className={isPlaying ? "" : "translate-x-px"}
          />
        </button>
        <IconControl
          icon="next"
          label="Next"
          onClick={next}
          disabled={!current}
        />
        <IconControl
          icon="repeat"
          label={`Repeat: ${repeat}`}
          onClick={cycleRepeat}
          active={repeat !== "off"}
          title={
            repeat === "off"
              ? "Repeat off"
              : repeat === "all"
                ? "Repeat all"
                : "Repeat one"
          }
          badge={repeat === "one" ? 1 : undefined}
        />
      </div>

      <div className="min-w-0 self-stretch py-3">
        <div className="flex h-8 min-w-0 flex-col items-center justify-center text-center">
          <div className="max-w-full truncate text-[13px] font-medium leading-4">
            {current ? (
              current.title
            ) : (
              <span className="text-[var(--color-text-muted)]">
                Nothing Playing
              </span>
            )}
          </div>
          <div className="max-w-full truncate text-[11px] leading-4 text-[var(--color-text-muted)]">
            {current
              ? `${current.artist}${current.album ? ` — ${current.album}` : ""}`
              : "Choose an album or song to begin"}
          </div>
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10px] tabular-nums text-[var(--color-text-muted)]">
          <span className="w-8 text-right">{formatTime(position)}</span>
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
              aria-label="Seek"
              min={0}
              max={duration || 0}
              step={0.1}
              value={position}
              onChange={(event) => seek(Number(event.target.value))}
              disabled={!current || duration <= 0}
              className="h-1 flex-1 cursor-pointer appearance-none rounded-full accent-[var(--color-accent)] disabled:cursor-default"
              style={{
                background: `linear-gradient(to right, var(--color-accent) ${progress}%, var(--color-surface) ${progress}%)`,
              }}
            />
          )}
          <span className="w-8">{formatTime(duration)}</span>
        </div>
      </div>

      <div className="flex items-center justify-end gap-1">
        <div className="relative">
          <IconControl
            icon="sleep"
            label="Sleep timer"
            onClick={() => setSleepMenuOpen((open) => !open)}
            active={sleepTimerExpiresAt !== null}
            expanded={sleepMenuOpen}
            hasPopup="menu"
            buttonRef={sleepButtonRef}
            title={
              sleepMinutesLeft === null
                ? "Sleep timer"
                : `Sleep timer: ~${sleepMinutesLeft} min left`
            }
            badge={sleepMinutesLeft ?? undefined}
          />
          {sleepMenuOpen ? (
            <>
              <div
                aria-hidden="true"
                className="fixed inset-0 z-10 cursor-default"
                onClick={() => setSleepMenuOpen(false)}
              />
              <div
                role="menu"
                aria-label="Sleep timer duration"
                className="absolute bottom-full right-0 z-20 mb-2 w-36 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] py-1 shadow-xl"
              >
                <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-text-muted)]">
                  Stop playing in
                </div>
                {[15, 30, 45, 60].map((minutes) => (
                  <button
                    type="button"
                    role="menuitem"
                    key={minutes}
                    onClick={() => {
                      setSleepTimer(minutes * 60_000);
                      setSleepMenuOpen(false);
                      queueMicrotask(() => sleepButtonRef.current?.focus());
                    }}
                    className="block w-full px-3 py-1.5 text-left text-[13px] hover:bg-[var(--color-surface)]"
                  >
                    {minutes} minutes
                  </button>
                ))}
                <div className="my-1 border-t border-[var(--color-border)]" />
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setSleepTimer(null);
                    setSleepMenuOpen(false);
                    queueMicrotask(() => sleepButtonRef.current?.focus());
                  }}
                  disabled={sleepTimerExpiresAt === null}
                  className="block w-full px-3 py-1.5 text-left text-[13px] hover:bg-[var(--color-surface)] disabled:opacity-40"
                >
                  Turn Off
                </button>
              </div>
            </>
          ) : null}
        </div>
        <IconControl
          icon="lyrics"
          label={lyricsOpen ? "Hide lyrics" : "Show lyrics"}
          onClick={onToggleLyrics}
          active={lyricsOpen}
          disabled={!current}
          title="Lyrics"
        />
        <IconControl
          icon="queue"
          label={queueOpen ? "Hide queue" : "Show queue"}
          onClick={onToggleQueue}
          active={queueOpen}
          title="Up Next"
        />
        <IconControl
          icon="mini"
          label="Enter mini-player"
          onClick={() => {
            enterMiniPlayer().catch(() => {});
          }}
          title="Mini Player"
        />
      </div>
    </footer>
  );
}
