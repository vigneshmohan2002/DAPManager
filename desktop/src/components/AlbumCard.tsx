import { useEffect, useRef, useState } from "react";
import type { Album } from "../lib/api";

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
  const [failed, setFailed] = useState(false);
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
      className="group block w-full cursor-pointer select-none rounded-md border-0 bg-transparent p-0 text-left font-[inherit] text-[var(--color-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg)]"
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
    >
      <span className="block aspect-square w-full rounded-md overflow-hidden bg-[var(--color-surface)] shadow-md">
        {failed ? (
          <span className="w-full h-full flex items-center justify-center text-[var(--color-text-muted)] text-xs">
            No cover
          </span>
        ) : (
          <img
            src={coverUrl}
            alt={album.title}
            loading="lazy"
            onError={() => setFailed(true)}
            className="w-full h-full object-cover transition-transform group-hover:scale-[1.02]"
          />
        )}
      </span>
      <span className="block mt-2 text-sm font-medium text-[var(--color-text)] truncate">
        {album.title}
      </span>
      <span className="block text-xs text-[var(--color-text-muted)] truncate">
        {album.artist}
      </span>
    </button>
  );
}
