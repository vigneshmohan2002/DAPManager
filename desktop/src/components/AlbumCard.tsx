import { useEffect, useRef, useState } from "react";
import type { Album } from "../lib/api";
import { albumDisplayArtist } from "../lib/album";
import Artwork from "./Artwork";
import ContextMenu from "./ContextMenu";
import { useAlbumCompletion } from "./useAlbumCompletion";

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
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const { completeAlbum, completingId } = useAlbumCompletion();
  const displayArtist = albumDisplayArtist(album);
  const completing = completingId === album.id;

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
    <>
      <button
        type="button"
        aria-label={`Open ${album.title} by ${displayArtist}`}
        className="group block w-full cursor-pointer select-none rounded-md border-0 bg-transparent p-0 text-left font-[inherit] text-[var(--color-text)] transition-transform duration-150 hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-content)]"
        onClick={handleClick}
        onDoubleClick={handleDoubleClick}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenu({ x: event.clientX, y: event.clientY });
        }}
      >
        <Artwork
          src={coverUrl}
          alt={album.title}
          className="aspect-square w-full rounded-[5px] shadow-[var(--shadow-artwork)]"
          imageClassName="transition-transform duration-200 group-hover:scale-[1.015]"
        />
        <span className="mt-2 block truncate text-[13px] font-medium leading-[1.35] text-[var(--color-text)]">
          {album.title}
        </span>
        <span className="mt-0.5 block truncate text-[11px] leading-[1.3] text-[var(--color-text-muted)]">
          {displayArtist}
        </span>
      </button>
      {menu ? (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          entries={[
            {
              kind: "item",
              label: completing ? "Completing Album…" : "Complete Album",
              disabled: completing,
              onSelect: () => void completeAlbum(album),
            },
          ]}
          onClose={() => setMenu(null)}
        />
      ) : null}
    </>
  );
}
