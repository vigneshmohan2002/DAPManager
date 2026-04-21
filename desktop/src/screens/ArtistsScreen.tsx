import { useEffect, useMemo, useState } from "react";
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
        subtitle={`${filtered.length} of ${artists.length}`}
        search={search}
        onSearch={setSearch}
      />
      <div className="flex-1 overflow-y-auto">
        {!ready || loading ? (
          <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">Loading…</div>
        ) : error ? (
          <div className="px-6 py-6 text-[var(--color-accent)] text-sm">{error}</div>
        ) : filtered.length === 0 ? (
          <div className="px-6 py-6 text-[var(--color-text-muted)] text-sm">
            No artists yet.
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-border)]">
            {filtered.map((a) => (
              <li key={a.name}>
                <button
                  onClick={() => onOpen(a)}
                  className="w-full text-left px-6 py-3 hover:bg-[var(--color-surface)]/60 flex items-baseline gap-4"
                >
                  <span className="flex-1 truncate text-sm">{a.name}</span>
                  <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
                    {a.album_count} {a.album_count === 1 ? "album" : "albums"} ·{" "}
                    {a.track_count} {a.track_count === 1 ? "track" : "tracks"}
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
