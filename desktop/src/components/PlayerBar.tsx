import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type Ref,
} from "react";
import { backendUrl, setTrackLiked, streamUrl } from "../lib/api";
import { useWaveformPeaks } from "../lib/useWaveformPeaks";
import { enterMiniPlayer } from "../lib/window";
import { usePlayer } from "../player/PlayerContext";
import Icon, { type IconName } from "./Icon";
import { useToast } from "./Toast";
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
  onPlaylistsChanged?: () => void;
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

function focusFirstMenuItem(menu: HTMLDivElement | null): void {
  menu
    ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
    ?.focus();
}

function handleMenuKeyDown(
  event: ReactKeyboardEvent<HTMLDivElement>,
): void {
  if (
    event.key !== "ArrowDown" &&
    event.key !== "ArrowUp" &&
    event.key !== "Home" &&
    event.key !== "End"
  ) {
    return;
  }

  const items = Array.from(
    event.currentTarget.querySelectorAll<HTMLButtonElement>(
      '[role="menuitem"]:not(:disabled)',
    ),
  );
  if (items.length === 0) return;

  event.preventDefault();
  const currentIndex = items.indexOf(
    document.activeElement as HTMLButtonElement,
  );
  if (event.key === "Home") {
    items[0]?.focus();
    return;
  }
  if (event.key === "End") {
    items[items.length - 1]?.focus();
    return;
  }

  const offset = event.key === "ArrowDown" ? 1 : -1;
  const nextIndex =
    currentIndex < 0
      ? offset > 0
        ? 0
        : items.length - 1
      : (currentIndex + offset + items.length) % items.length;
  items[nextIndex]?.focus();
}

export default function PlayerBar({
  queueOpen,
  onToggleQueue,
  lyricsOpen,
  onToggleLyrics,
  onPlaylistsChanged,
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
    setTrackLikedInQueue,
    volume,
    setVolume,
    sleepTimerExpiresAt,
    setSleepTimer,
  } = usePlayer();
  const toast = useToast();
  const [base, setBase] = useState("");
  const [sleepMenuOpen, setSleepMenuOpen] = useState(false);
  const [overflowMenuOpen, setOverflowMenuOpen] = useState(false);
  const [likeSaving, setLikeSaving] = useState(false);
  const sleepButtonRef = useRef<HTMLButtonElement | null>(null);
  const overflowButtonRef = useRef<HTMLButtonElement | null>(null);
  const sleepMenuRef = useRef<HTMLDivElement | null>(null);
  const overflowMenuRef = useRef<HTMLDivElement | null>(null);
  const sleepReturnFocusRef = useRef<HTMLButtonElement | null>(null);
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
    if (!sleepMenuOpen && !overflowMenuOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      if (sleepMenuOpen) {
        setSleepMenuOpen(false);
        queueMicrotask(() => sleepReturnFocusRef.current?.focus());
        return;
      }
      setOverflowMenuOpen(false);
      queueMicrotask(() => overflowButtonRef.current?.focus());
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [overflowMenuOpen, sleepMenuOpen]);

  useEffect(() => {
    if (!sleepMenuOpen) return;
    queueMicrotask(() => focusFirstMenuItem(sleepMenuRef.current));
  }, [sleepMenuOpen]);

  useEffect(() => {
    if (!overflowMenuOpen) return;
    queueMicrotask(() => focusFirstMenuItem(overflowMenuRef.current));
  }, [overflowMenuOpen]);

  const sleepMinutesLeft =
    sleepTimerExpiresAt === null
      ? null
      : Math.max(0, Math.ceil((sleepTimerExpiresAt - nowTick) / 60_000));
  const progress = duration > 0 ? (position / duration) * 100 : 0;
  const peaks = useWaveformPeaks(
    current && base ? streamUrl(base, current.mbid) : null,
    current?.mbid ?? null,
  );

  const toggleCurrentLike = async () => {
    if (!current || likeSaving) return;
    const wasLiked = Boolean(current.is_liked);
    const nextLiked = !wasLiked;
    let persisted = false;
    setLikeSaving(true);
    setTrackLikedInQueue(current.mbid, nextLiked);
    try {
      const result = await setTrackLiked(current.mbid, nextLiked);
      if (result.success) {
        persisted = true;
      } else {
        setTrackLikedInQueue(current.mbid, wasLiked);
        toast.show(result.message ?? "Could not save like", "err");
      }
    } catch (error) {
      setTrackLikedInQueue(current.mbid, wasLiked);
      toast.show(`Could not save like: ${String(error)}`, "err");
    } finally {
      setLikeSaving(false);
    }
    if (persisted && !wasLiked) onPlaylistsChanged?.();
  };

  const openSleepMenu = (returnFocus: HTMLButtonElement | null) => {
    sleepReturnFocusRef.current = returnFocus;
    setOverflowMenuOpen(false);
    setSleepMenuOpen(true);
  };

  const closeSleepMenu = () => {
    setSleepMenuOpen(false);
    queueMicrotask(() => sleepReturnFocusRef.current?.focus());
  };

  const closeOverflowMenu = () => {
    setOverflowMenuOpen(false);
    queueMicrotask(() => overflowButtonRef.current?.focus());
  };

  return (
    <footer className="doppler-player grid h-20 shrink-0 grid-cols-[minmax(190px,260px)_minmax(160px,1fr)_minmax(190px,260px)] items-center gap-3 border-t border-[var(--color-border)] px-4">
      <div className="flex items-center justify-start gap-0.5 pl-1">
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
          className="doppler-control grid h-8 w-8 shrink-0 place-items-center rounded disabled:opacity-30"
        >
          <Icon
            name={isPlaying ? "pause" : "play"}
            size={20}
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

      <div className="min-w-0 self-stretch py-2">
        <div className="flex h-8 min-w-0 flex-col items-center justify-center text-center">
          <div className="max-w-full truncate text-[12px] font-medium leading-4">
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
              : ""}
          </div>
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-[10px] tabular-nums text-[var(--color-text-muted)]">
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
              className="doppler-range h-4 flex-1 cursor-pointer appearance-none bg-transparent disabled:cursor-default"
              style={{
                "--range-progress": `${progress}%`,
              } as CSSProperties}
            />
          )}
          <span className="w-8">
            {current ? `-${formatTime(Math.max(0, duration - position))}` : "0:00"}
          </span>
        </div>
      </div>

      <div className="relative flex items-center justify-end gap-0.5 pr-1">
        <IconControl
          icon="heart"
          label={
            current?.is_liked ? "Unlike current track" : "Like current track"
          }
          onClick={() => {
            void toggleCurrentLike();
          }}
          active={Boolean(current?.is_liked)}
          disabled={!current || likeSaving}
          title={current?.is_liked ? "Unlike" : "Like"}
        />
        <label className="mx-1 flex min-w-16 max-w-24 flex-1 items-center gap-1.5">
          <Icon
            name="volume"
            size={15}
            className="shrink-0 text-[var(--color-text-muted)]"
          />
          <input
            type="range"
            aria-label="Volume"
            min={0}
            max={1}
            step={0.01}
            value={volume}
            onChange={(event) => setVolume(Number(event.target.value))}
            className="doppler-range h-4 min-w-0 flex-1 cursor-pointer appearance-none bg-transparent"
            style={
              {
                "--range-progress": `${volume * 100}%`,
              } as CSSProperties
            }
          />
        </label>
        <div className="player-secondary-control relative">
          <IconControl
            icon="sleep"
            label="Sleep timer"
            onClick={() => {
              if (sleepMenuOpen) {
                closeSleepMenu();
                return;
              }
              openSleepMenu(sleepButtonRef.current);
            }}
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
        </div>
        <IconControl
          icon="lyrics"
          label={lyricsOpen ? "Hide lyrics" : "Show lyrics"}
          onClick={onToggleLyrics}
          active={lyricsOpen}
          disabled={!current}
          title="Lyrics"
          className="player-secondary-control"
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
          className="player-secondary-control"
        />
        <button
          ref={overflowButtonRef}
          type="button"
          aria-label="More player controls"
          aria-haspopup="menu"
          aria-expanded={
            overflowMenuOpen ||
            (sleepMenuOpen &&
              sleepReturnFocusRef.current === overflowButtonRef.current)
          }
          onClick={() => {
            if (overflowMenuOpen) {
              closeOverflowMenu();
              return;
            }
            setSleepMenuOpen(false);
            setOverflowMenuOpen(true);
          }}
          className="doppler-control player-overflow-control relative h-8 w-8 shrink-0 place-items-center rounded-md text-[17px] leading-none"
        >
          <span aria-hidden="true" className="-translate-y-0.5">
            •••
          </span>
        </button>
        {overflowMenuOpen ? (
          <>
            <div
              aria-hidden="true"
              className="fixed inset-0 z-10 cursor-default"
              onClick={closeOverflowMenu}
            />
            <div
              ref={overflowMenuRef}
              role="menu"
              aria-label="More player controls"
              onKeyDown={handleMenuKeyDown}
              className="absolute bottom-full right-0 z-20 mb-2 w-44 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)] py-1 shadow-xl"
            >
              <button
                type="button"
                role="menuitem"
                onClick={() => openSleepMenu(overflowButtonRef.current)}
                className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[13px] hover:bg-[var(--color-surface)]"
              >
                <span>Sleep Timer</span>
                {sleepMinutesLeft !== null ? (
                  <span className="text-[11px] text-[var(--color-text-muted)]">
                    {sleepMinutesLeft}m
                  </span>
                ) : null}
              </button>
              <button
                type="button"
                role="menuitem"
                disabled={!current}
                onClick={() => {
                  setOverflowMenuOpen(false);
                  onToggleLyrics();
                  queueMicrotask(() => overflowButtonRef.current?.focus());
                }}
                className="block w-full px-3 py-2 text-left text-[13px] hover:bg-[var(--color-surface)] disabled:opacity-40"
              >
                {lyricsOpen ? "Hide Lyrics" : "Show Lyrics"}
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOverflowMenuOpen(false);
                  queueMicrotask(() => overflowButtonRef.current?.focus());
                  void enterMiniPlayer().catch(() => {});
                }}
                className="block w-full px-3 py-2 text-left text-[13px] hover:bg-[var(--color-surface)]"
              >
                Enter Mini Player
              </button>
            </div>
          </>
        ) : null}
        {sleepMenuOpen ? (
          <>
            <div
              aria-hidden="true"
              className="fixed inset-0 z-10 cursor-default"
              onClick={closeSleepMenu}
            />
            <div
              ref={sleepMenuRef}
              role="menu"
              aria-label="Sleep timer duration"
              onKeyDown={handleMenuKeyDown}
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
                    closeSleepMenu();
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
                  closeSleepMenu();
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
    </footer>
  );
}
