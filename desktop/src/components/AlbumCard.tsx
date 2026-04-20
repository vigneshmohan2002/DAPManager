import { useState } from "react";
import type { Album } from "../lib/api";

type Props = {
  album: Album;
  coverUrl: string;
  onClick?: () => void;
};

export default function AlbumCard({ album, coverUrl, onClick }: Props) {
  const [failed, setFailed] = useState(false);

  return (
    <div className="group cursor-pointer" onClick={onClick}>
      <div className="aspect-square w-full rounded-md overflow-hidden bg-[var(--color-surface)] shadow-md">
        {failed ? (
          <div className="w-full h-full flex items-center justify-center text-[var(--color-text-muted)] text-xs">
            No cover
          </div>
        ) : (
          <img
            src={coverUrl}
            alt={album.title}
            loading="lazy"
            onError={() => setFailed(true)}
            className="w-full h-full object-cover transition-transform group-hover:scale-[1.02]"
          />
        )}
      </div>
      <div className="mt-2 text-sm font-medium text-[var(--color-text)] truncate">
        {album.title}
      </div>
      <div className="text-xs text-[var(--color-text-muted)] truncate">
        {album.artist}
      </div>
    </div>
  );
}
