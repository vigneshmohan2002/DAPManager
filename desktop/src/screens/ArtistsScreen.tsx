import { useEffect, useMemo, useState } from "react";
import Icon from "../components/Icon";
import TopBar from "../components/TopBar";
import { fetchArtists, type Artist } from "../lib/api";

type Props = {
  ready: boolean;
  onOpen: (artist: Artist) => void;
};

export default function ArtistsScreen({ ready, onOpen }: Props) {
  const [artists, setArtists] = useState<Artist[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetchArtists();
        if (!cancelled) setArtists(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
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

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Artists"
        subtitle={
          search.trim()
            ? `${filtered.length} of ${artists.length} artists`
            : `${artists.length} ${artists.length === 1 ? "artist" : "artists"}`
        }
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto px-5 pb-10">
        {!ready || loading ? (
          <div className="py-6 text-sm text-[var(--color-text-muted)]">Loading…</div>
        ) : error ? (
          <div className="py-6 text-sm text-[var(--color-danger)]">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="grid min-h-48 place-items-center text-center text-[11px] text-[var(--color-text-muted)]">
            <div>
              <Icon
                name="artists"
                size={28}
                className="mx-auto mb-2 opacity-45"
              />
              {search.trim() ? "No matching artists." : "No artists yet."}
            </div>
          </div>
        ) : (
          <ul className="mx-auto mt-5 w-full max-w-[940px] overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-elevated)]">
            {filtered.map((a) => (
              <li key={a.name}>
                <button
                  type="button"
                  onClick={() => onOpen(a)}
                  aria-label={`Open artist ${a.name}`}
                  className="group flex w-full items-center gap-3 border-b border-[var(--color-border)] px-3 py-2 text-left last:border-b-0 hover:bg-[var(--color-surface)]/55"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[var(--color-surface)] text-[12px] font-semibold text-[var(--color-text-muted)] shadow-sm transition-colors group-hover:text-[var(--color-text)]">
                    {(a.name[0] ?? "?").toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[12px] font-medium">
                      {a.name}
                    </span>
                    <span className="mt-0.5 block text-[10px] tabular-nums text-[var(--color-text-muted)]">
                      {a.album_count} {a.album_count === 1 ? "album" : "albums"} ·{" "}
                      {a.track_count} {a.track_count === 1 ? "track" : "tracks"}
                    </span>
                  </span>
                  <span
                    aria-hidden="true"
                    className="pr-1 text-[15px] text-[var(--color-text-muted)] opacity-45 transition-opacity group-hover:opacity-80"
                  >
                    ›
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
