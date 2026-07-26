import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  fetchAlbums,
  fetchArtists,
  searchTracks,
  type Album,
  type Artist,
  type SearchTrackResult,
} from "../lib/api";
import { usePlayer } from "../player/PlayerContext";
import Icon, { type IconName } from "./Icon";

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
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
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
    if (!open) return;
    previousFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setQuery("");
    setTracks([]);
    // Focus after paint so the overlay is in the DOM.
    queueMicrotask(() => inputRef.current?.focus());
    return () => {
      const previous = previousFocusRef.current;
      previousFocusRef.current = null;
      previous?.focus();
    };
  }, [open]);

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

  const handleDialogKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(
      dialog.querySelectorAll<HTMLElement>("*"),
    ).filter((element) =>
      element.matches(
        'button:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active =
      event.target instanceof HTMLElement
        ? event.target
        : document.activeElement;

    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
      return;
    }
    if (!event.shiftKey && (active === last || !dialog.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-[12vh] backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Search your library"
        onKeyDownCapture={handleDialogKeyDown}
        className="w-[620px] max-w-[90vw] overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-elevated)] shadow-[var(--shadow-window)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex h-12 items-center gap-3 border-b border-[var(--color-border)] px-4">
          <Icon
            name="search"
            size={18}
            className="shrink-0 text-[var(--color-text-muted)]"
          />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search artists, albums and songs"
            aria-label="Search your library"
            className="min-w-0 flex-1 bg-transparent text-[14px] text-[var(--color-text)] outline-none placeholder:text-[var(--color-text-muted)]"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close search"
            className="doppler-control rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[9px] font-medium tracking-wide"
          >
            ESC
          </button>
        </div>
        <div className="max-h-[64vh] min-h-24 overflow-y-auto p-2">
          {!query.trim() ? (
            <div className="grid min-h-20 place-items-center px-4 text-[12px] text-[var(--color-text-muted)]">
              Type to search across artists, albums and songs.
            </div>
          ) : !hasAny ? (
            <div className="grid min-h-20 place-items-center px-4 text-[12px] text-[var(--color-text-muted)]">
              No matches.
            </div>
          ) : (
            <>
              {matchedArtists.length > 0 && (
                <Section title="Artists" icon="artists">
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
                <Section title="Albums" icon="albums">
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
                <Section title="Songs" icon="songs">
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
  icon,
  children,
}: {
  title: string;
  icon: IconName;
  children: ReactNode;
}) {
  return (
    <section className="py-1">
      <div className="flex items-center gap-1.5 px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--color-text-muted)]">
        <Icon name={icon} size={12} />
        <span>{title}</span>
      </div>
      <ul className="space-y-px">{children}</ul>
    </section>
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
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="flex w-full flex-col rounded-md px-2.5 py-1.5 text-left hover:bg-[var(--color-surface)] disabled:cursor-not-allowed disabled:opacity-40"
      >
        <span className="w-full truncate text-[13px] font-medium">
          {primary}
        </span>
        <span className="w-full truncate text-[11px] text-[var(--color-text-muted)]">
          {secondary}
        </span>
      </button>
    </li>
  );
}
