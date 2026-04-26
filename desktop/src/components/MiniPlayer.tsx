import { useEffect, useState } from "react";
import { albumCoverUrl, backendUrl } from "../lib/api";
import { exitMiniPlayer } from "../lib/window";
import { usePlayer } from "../player/PlayerContext";

export default function MiniPlayer() {
  const { current, isPlaying, toggle, next, prev } = usePlayer();
  const [base, setBase] = useState("");

  useEffect(() => {
    backendUrl().then(setBase);
  }, []);

  const cover =
    current?.albumId && base ? albumCoverUrl(base, current.albumId) : null;

  return (
    <div
      data-tauri-drag-region
      className="h-screen w-screen relative bg-black overflow-hidden select-none"
    >
      {cover ? (
        <img
          src={cover}
          alt=""
          data-tauri-drag-region
          className="absolute inset-0 w-full h-full object-cover pointer-events-none"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
      ) : (
        <div
          data-tauri-drag-region
          className="absolute inset-0 flex items-center justify-center text-[var(--color-text-muted)] text-xs"
        >
          Nothing playing
        </div>
      )}

      <div
        data-tauri-drag-region
        className="absolute inset-x-0 bottom-0 px-2 py-1.5 bg-gradient-to-t from-black/85 via-black/50 to-transparent text-white text-[11px] truncate pointer-events-none"
      >
        {current ? (
          <>
            <div className="truncate">{current.title}</div>
            <div className="truncate text-white/70 text-[10px]">
              {current.artist}
            </div>
          </>
        ) : null}
      </div>

      <div className="absolute inset-0 flex items-center justify-center gap-1.5 bg-black/45 opacity-0 hover:opacity-100 transition">
        <button
          onClick={prev}
          disabled={!current}
          aria-label="Previous"
          className="w-9 h-9 rounded-full bg-white/15 hover:bg-white/25 text-white text-base flex items-center justify-center disabled:opacity-40"
        >
          ⏮
        </button>
        <button
          onClick={toggle}
          disabled={!current}
          aria-label={isPlaying ? "Pause" : "Play"}
          className="w-11 h-11 rounded-full bg-[var(--color-accent)] hover:brightness-110 text-white flex items-center justify-center disabled:opacity-40 disabled:bg-white/15"
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <button
          onClick={next}
          disabled={!current}
          aria-label="Next"
          className="w-9 h-9 rounded-full bg-white/15 hover:bg-white/25 text-white text-base flex items-center justify-center disabled:opacity-40"
        >
          ⏭
        </button>
        <button
          onClick={() => {
            exitMiniPlayer().catch(() => {});
          }}
          aria-label="Exit mini-player"
          className="w-9 h-9 rounded-full bg-white/15 hover:bg-white/25 text-white text-xs flex items-center justify-center ml-1"
        >
          ⤢
        </button>
      </div>
    </div>
  );
}
