import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAlbums,
  fetchArtists,
  searchTracks,
  type Album,
  type Artist,
  type SearchTrackResult,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type Props = {
  open: boolean;
  onClose: () => void;
  onOpenAlbum: (album: Album) => void;
  onOpenArtist: (artist: Artist) => void;
};

const RESULT_LIMIT = 6;

export default function SearchOverlay({
  open,
  onClose,
  onOpenAlbum,
  onOpenArtist,
}: Props) {
  const [query, setQuery] = useState("");
  const [albums, setAlbums] = useState<Album[]>([]);
  const [artists, setArtists] = useState<Artist[]>([]);
  const [tracks, setTracks] = useState<SearchTrackResult[]>([]);
  const [loadedLookups, setLoadedLookups] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const { play } = usePlayer();

  // Load albums+artists lazily on first open so the overlay is cheap to
  // mount on app start. Tracks are queried per-keystroke since the full
  // list can be large.
  useEffect(() => {
    if (!open || loadedLookups) return;
    let cancelled = false;
    (async () => {
      try {
        const [al, ar] = await Promise.all([fetchAlbums(), fetchArtists()]);
        if (cancelled) return;
        setAlbums(al);
        setArtists(ar);
        setLoadedLookups(true);
      } catch {
        // Non-fatal; search still works via /api/library/search.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, loadedLookups]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setTracks([]);
      // Focus after paint so the overlay is in the DOM.
      queueMicrotask(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Debounced track search: stale closures are OK because state setters
  // are stable and we cancel via the `abort` sentinel below.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (!q) {
      setTracks([]);
      return;
    }
    let abort = false;
    const handle = window.setTimeout(async () => {
      try {
        const results = await searchTracks(q);
        if (!abort) setTracks(results.slice(0, RESULT_LIMIT));
      } catch {
        if (!abort) setTracks([]);
      }
    }, 150);
    return () => {
      abort = true;
      window.clearTimeout(handle);
    };
  }, [query, open]);

  const matchedAlbums = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return albums
      .filter(
        (a) =>
          a.title.toLowerCase().includes(q) ||
          a.artist.toLowerCase().includes(q),
      )
      .slice(0, RESULT_LIMIT);
  }, [albums, query]);

  const matchedArtists = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return artists.filter((a) => a.name.toLowerCase().includes(q)).slice(0, RESULT_LIMIT);
  }, [artists, query]);

  if (!open) return null;

  const pickTrack = (t: SearchTrackResult) => {
    play(
      [
        {
          mbid: t.mbid,
          title: t.title,
          artist: t.artist,
          album: t.album,
          track_number: null,
          disc_number: null,
          albumId: null,
        },
      ],
      0,
    );
    onClose();
  };

  const hasAny =
    matchedArtists.length > 0 ||
    matchedAlbums.length > 0 ||
    tracks.length > 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/60"
      onClick={onClose}
    >
      <div
        className="w-[560px] max-w-[90vw] rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-border)] shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search artists, albums, songs…"
          className="w-full bg-transparent text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] px-4 py-3 outline-none border-b border-[var(--color-border)]"
        />
        <div className="max-h-[60vh] overflow-y-auto">
          {!query.trim() ? (
            <div className="px-4 py-6 text-sm text-[var(--color-text-muted)]">
              Type to search across artists, albums and songs.
            </div>
          ) : !hasAny ? (
            <div className="px-4 py-6 text-sm text-[var(--color-text-muted)]">
              No matches.
            </div>
          ) : (
            <>
              {matchedArtists.length > 0 && (
                <Section title="Artists">
                  {matchedArtists.map((a) => (
                    <ResultRow
                      key={`artist-${a.name}`}
                      onClick={() => {
                        onOpenArtist(a);
                        onClose();
                      }}
                      primary={a.name}
                      secondary={`${a.album_count} albums · ${a.track_count} tracks`}
                    />
                  ))}
                </Section>
              )}
              {matchedAlbums.length > 0 && (
                <Section title="Albums">
                  {matchedAlbums.map((a) => (
                    <ResultRow
                      key={`album-${a.id}`}
                      onClick={() => {
                        onOpenAlbum(a);
                        onClose();
                      }}
                      primary={a.title}
                      secondary={a.artist}
                    />
                  ))}
                </Section>
              )}
              {tracks.length > 0 && (
                <Section title="Songs">
                  {tracks.map((t) => (
                    <ResultRow
                      key={`track-${t.mbid}`}
                      onClick={() => pickTrack(t)}
                      primary={t.title}
                      secondary={`${t.artist}${t.album ? ` — ${t.album}` : ""}`}
                      disabled={!t.path}
                    />
                  ))}
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="py-1">
      <div className="px-4 pt-3 pb-1 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
        {title}
      </div>
      <ul>{children}</ul>
    </div>
  );
}

function ResultRow({
  primary,
  secondary,
  onClick,
  disabled,
}: {
  primary: string;
  secondary: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <li>
      <button
        onClick={onClick}
        disabled={disabled}
        className="w-full text-left px-4 py-2 hover:bg-[var(--color-surface)] disabled:opacity-40 disabled:cursor-not-allowed flex flex-col"
      >
        <span className="text-sm truncate">{primary}</span>
        <span className="text-xs text-[var(--color-text-muted)] truncate">
          {secondary}
        </span>
      </button>
    </li>
  );
}
