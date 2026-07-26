import { useEffect, useState } from "react";
import { albumCoverUrl, backendUrl } from "../lib/api";
import { exitMiniPlayer } from "../lib/window";
import { usePlayer } from "../player/PlayerContext";
import Artwork from "./Artwork";
import Icon from "./Icon";

export default function MiniPlayer() {
  const { current, isPlaying, toggle, next, prev } = usePlayer();
  const [base, setBase] = useState("");

  useEffect(() => {
    backendUrl().then(setBase).catch(() => {});
  }, []);

  const cover =
    current?.albumId && base ? albumCoverUrl(base, current.albumId) : null;

  return (
    <div
      data-tauri-drag-region
      className="relative h-screen w-screen select-none overflow-hidden bg-[var(--color-bg-elevated)]"
    >
      <Artwork
        src={cover}
        alt={current ? `${current.album ?? current.title} cover` : ""}
        fallbackLabel={current ? "No cover" : "Nothing playing"}
        loading="eager"
        className="pointer-events-none absolute inset-0 h-full w-full rounded-none"
      />

      <div
        data-tauri-drag-region
        className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/55 to-transparent px-2.5 pb-2 pt-10 text-[11px] text-white"
      >
        {current ? (
          <>
            <div className="truncate font-medium">{current.title}</div>
            <div className="truncate text-[10px] text-white/70">
              {current.artist}
            </div>
          </>
        ) : null}
      </div>

      <div className="absolute inset-0 flex items-center justify-center gap-1.5 bg-black/40 opacity-0 transition-opacity hover:opacity-100 focus-within:opacity-100">
        <button
          type="button"
          onClick={prev}
          disabled={!current}
          aria-label="Previous"
          className="grid h-8 w-8 place-items-center rounded-full bg-black/35 text-white backdrop-blur-md transition hover:bg-black/55 disabled:opacity-40"
        >
          <Icon name="previous" size={15} />
        </button>
        <button
          type="button"
          onClick={toggle}
          disabled={!current}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="grid h-10 w-10 place-items-center rounded-full bg-white text-black shadow-lg transition hover:scale-[1.03] active:scale-95 disabled:bg-white/45 disabled:opacity-40"
        >
          <Icon
            name={isPlaying ? "pause" : "play"}
            size={17}
            className={isPlaying ? "" : "translate-x-px"}
          />
        </button>
        <button
          type="button"
          onClick={next}
          disabled={!current}
          aria-label="Next"
          className="grid h-8 w-8 place-items-center rounded-full bg-black/35 text-white backdrop-blur-md transition hover:bg-black/55 disabled:opacity-40"
        >
          <Icon name="next" size={15} />
        </button>
        <button
          type="button"
          onClick={() => {
            exitMiniPlayer().catch(() => {});
          }}
          aria-label="Exit mini-player"
          className="ml-1 grid h-8 w-8 place-items-center rounded-full bg-black/35 text-white backdrop-blur-md transition hover:bg-black/55"
        >
          <Icon name="mini" size={14} />
        </button>
      </div>
    </div>
  );
}
