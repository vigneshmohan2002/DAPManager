import { useEffect, useMemo, useState, type ReactNode } from "react";
import Artwork from "../components/Artwork";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import {
  albumCoverUrl,
  backendUrl,
  fetchAlbums,
  fetchArtists,
  type Album,
  type Artist,
} from "../lib/api";
import ArtistDetailScreen from "./ArtistDetailScreen";

type Props = {
  ready: boolean;
  selectedArtist?: Artist | null;
  onOpen: (artist: Artist) => void;
  onOpenAlbum?: (album: Album) => void;
  onBack?: () => void;
};

export default function ArtistsScreen({
  ready,
  selectedArtist = null,
  onOpen,
  onOpenAlbum,
  onBack,
}: Props) {
  const [artists, setArtists] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [catalogAlbums, setCatalogAlbums] = useState<Album[]>([]);
  const [catalogBase, setCatalogBase] = useState("");
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setCatalogLoading(true);
    setCatalogError(null);

    void (async () => {
      try {
        const data = await fetchArtists();
        if (!cancelled) setArtists(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    void (async () => {
      try {
        const [base, albums] = await Promise.all([
          backendUrl(),
          fetchAlbums(),
        ]);
        if (cancelled) return;
        setCatalogBase(base);
        setCatalogAlbums(albums);
      } catch (e) {
        if (!cancelled) {
          setCatalogAlbums([]);
          setCatalogError(String(e));
        }
      } finally {
        if (!cancelled) setCatalogLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ready]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return artists;
    return artists.filter((a) => a.name.toLowerCase().includes(q));
  }, [artists, search]);
  const ordered = useMemo(
    () =>
      [...filtered].sort((left, right) =>
        left.name.localeCompare(right.name, undefined, {
          sensitivity: "base",
        }),
      ),
    [filtered],
  );
  const artistCovers = useMemo(() => {
    if (!catalogBase) return {};
    const covers: Record<string, string> = {};
    for (const album of catalogAlbums) {
      if (!covers[album.artist]) {
        covers[album.artist] = albumCoverUrl(catalogBase, album.id);
      }
    }
    return covers;
  }, [catalogAlbums, catalogBase]);
  const selectedAlbums = useMemo(
    () =>
      selectedArtist
        ? catalogAlbums.filter((album) => album.artist === selectedArtist.name)
        : [],
    [catalogAlbums, selectedArtist],
  );

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Artists"
        subtitle={
          loading
            ? undefined
            : error
              ? "Unavailable"
              : search.trim()
                ? `${filtered.length} of ${artists.length} artists`
                : `${artists.length} ${artists.length === 1 ? "artist" : "artists"}`
        }
        search={search}
        onSearch={setSearch}
        onBack={selectedArtist ? onBack : undefined}
      />
      <div className="flex min-h-0 flex-1">
        <aside
          aria-label="Artist browser"
          className="doppler-artist-rail w-[202px] shrink-0 overflow-y-auto border-r border-[var(--color-border)]"
        >
          {!ready || loading ? (
            <RailState>
              <span role="status">Loading…</span>
            </RailState>
          ) : error ? (
            <RailState>
              <span role="alert" className="text-[var(--color-danger)]">
                {error}
              </span>
            </RailState>
          ) : filtered.length === 0 ? (
            <RailState>
              <Icon name="artists" size={22} className="mb-2 opacity-40" />
              <span>
                {search.trim() ? "No matching artists." : "No artists yet."}
              </span>
            </RailState>
          ) : (
            <ul aria-label="Artists" className="w-full py-1">
              {ordered.map((artist) => {
                const selected = selectedArtist?.name === artist.name;
                return (
                  <li
                    key={artist.name}
                    className="relative after:absolute after:bottom-0 after:left-[46px] after:right-3 after:h-px after:bg-[var(--color-border)]"
                  >
                    <button
                      type="button"
                      onClick={() => {
                        if (!selected) onOpen(artist);
                      }}
                      aria-label={`Open artist ${artist.name}`}
                      aria-current={selected ? "page" : undefined}
                      className={`group mx-2 flex h-[54px] w-[calc(100%-1rem)] items-center gap-2.5 rounded-md px-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent)] ${
                        selected
                          ? "doppler-selection"
                          : "hover:bg-[var(--color-surface)]/45"
                      }`}
                    >
                      <Artwork
                        src={artistCovers[artist.name]}
                        alt=""
                        fallbackLabel={(artist.name[0] ?? "?").toLocaleUpperCase()}
                        loading="lazy"
                        className={`h-8 w-8 shrink-0 rounded-full text-[10px] font-semibold shadow-sm ${
                          selected
                            ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                            : "bg-[var(--color-surface)] text-[var(--color-text-muted)]"
                        }`}
                      />
                      <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
                        {artist.name}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          {selectedArtist ? (
            <ArtistDetailScreen
              artist={selectedArtist}
              embedded
              onBack={onBack ?? (() => {})}
              onOpenAlbum={onOpenAlbum ?? (() => {})}
              preloadedAlbums={selectedAlbums}
              preloadedBaseUrl={catalogBase}
              preloadedLoading={catalogLoading}
              preloadedError={catalogError}
            />
          ) : (
            <ArtistState>
              <Icon name="artists" size={30} className="mb-2 opacity-35" />
              <span className="font-medium text-[var(--color-text)]">
                Choose an artist
              </span>
              <span className="mt-1 max-w-52">
                Select someone from the list to browse their albums.
              </span>
            </ArtistState>
          )}
        </div>
      </div>
    </div>
  );
}

function ArtistState({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-full min-h-48 place-items-center text-center text-[11px] text-[var(--color-text-muted)]">
      <div className="flex flex-col items-center">{children}</div>
    </div>
  );
}

function RailState({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-36 place-items-center px-4 text-center text-[9px] text-[var(--color-text-muted)]">
      <div className="flex flex-col items-center">{children}</div>
    </div>
  );
}
