import { useEffect, useRef } from "react";
import type { Album } from "../lib/api";
import Artwork from "./Artwork";

type Props = {
  album: Album;
  coverUrl: string;
  onClick?: () => void;
  onDoubleClick?: () => void;
};

export default function AlbumCard({
  album,
  coverUrl,
  onClick,
  onDoubleClick,
}: Props) {
  const clickTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (clickTimer.current !== null) {
        window.clearTimeout(clickTimer.current);
      }
    },
    [],
  );

  const handleClick = () => {
    if (!onDoubleClick) {
      onClick?.();
      return;
    }
    if (clickTimer.current !== null) {
      window.clearTimeout(clickTimer.current);
    }
    // A short defer lets the browser distinguish a single click (open the
    // detail screen) from a double click (play the album) before navigation
    // unmounts this card.
    clickTimer.current = window.setTimeout(() => {
      clickTimer.current = null;
      onClick?.();
    }, 400);
  };

  const handleDoubleClick = () => {
    if (clickTimer.current !== null) {
      window.clearTimeout(clickTimer.current);
      clickTimer.current = null;
    }
    onDoubleClick?.();
  };

  return (
    <button
      type="button"
      aria-label={`Open ${album.title} by ${album.artist}`}
      className="group block w-full cursor-pointer select-none rounded-md border-0 bg-transparent p-0 text-left font-[inherit] text-[var(--color-text)] transition-transform duration-150 hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-content)]"
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
    >
      <Artwork
        src={coverUrl}
        alt={album.title}
        className="aspect-square w-full rounded-[5px] shadow-[var(--shadow-artwork)]"
        imageClassName="transition-transform duration-200 group-hover:scale-[1.015]"
      />
      <span className="block mt-1.5 truncate text-[11px] font-medium leading-[1.35] text-[var(--color-text)]">
        {album.title}
      </span>
      <span className="mt-0.5 block truncate text-[10px] leading-[1.3] text-[var(--color-text-muted)]">
        {album.artist}
      </span>
    </button>
  );
}
