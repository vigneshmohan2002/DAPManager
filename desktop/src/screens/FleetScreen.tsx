import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import {
  fetchFleetSummary,
  searchFleet,
  type FleetDevice,
  type FleetSearchResult,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = {
  ready: boolean;
};

export default function FleetScreen({ ready }: Props) {
  const [devices, setDevices] = useState<FleetDevice[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<FleetSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const d = await fetchFleetSummary();
        if (!cancelled) setDevices(d);
      } catch (e) {
        if (!cancelled) setLoadError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  const runSearch = async () => {
    const q = query.trim();
    if (!q) {
      setResults(null);
      setSearchError(null);
      return;
    }
    setSearching(true);
    setSearchError(null);
    try {
      const r = await searchFleet(q);
      setResults(r);
    } catch (e) {
      setSearchError(String(e));
      setResults(null);
    } finally {
      setSearching(false);
    }
  };

  const totalTracks =
    devices?.reduce((sum, d) => sum + d.track_count, 0) ?? 0;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Fleet"
        subtitle={
          devices === null
            ? "Loading…"
            : devices.length === 0
              ? "No devices reporting"
              : `${devices.length} device${devices.length === 1 ? "" : "s"} · ${totalTracks} tracks total`
        }
      />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-8">
        <section>
          <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Devices
          </div>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl mb-4">
            Per-device inventory reported via{" "}
            <code className="text-[var(--color-text)]">/api/inventory</code>.
            A device shows up here after it runs Report Inventory against
            this DAPManager.
          </p>
          {loadError ? (
            <div className="text-sm text-[var(--color-accent)]">{loadError}</div>
          ) : devices === null ? (
            <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
          ) : devices.length === 0 ? (
            <div className="text-sm text-[var(--color-text-muted)] italic">
              No devices have reported inventory yet. Run "Report Inventory"
              on a device to populate this view.
            </div>
          ) : (
            <table className="w-full text-sm border border-[var(--color-border)] rounded-md overflow-hidden">
              <thead className="bg-[var(--color-surface)]/50 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                <tr>
                  <th className="text-left font-medium px-3 py-2">Device</th>
                  <th className="text-right font-medium px-3 py-2 w-24">
                    Tracks
                  </th>
                  <th className="text-left font-medium px-3 py-2 w-48">
                    Last reported
                  </th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr
                    key={d.device_id}
                    className="border-t border-[var(--color-border)]/60"
                  >
                    <td className="px-3 py-2 font-medium text-[var(--color-text)]">
                      {d.device_id}
                    </td>
                    <td className="px-3 py-2 text-right text-[var(--color-text-muted)]">
                      {d.track_count.toLocaleString()}
                    </td>
                    <td
                      className="px-3 py-2 text-[var(--color-text-muted)]"
                      title={d.last_reported_at ?? ""}
                    >
                      {relativeTime(d.last_reported_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Find a track across the fleet
          </div>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl mb-4">
            Search artist / album / title to see which devices hold each
            match.
          </p>
          <div className="flex gap-2 mb-4">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") runSearch();
              }}
              placeholder="Search artist / album / title…"
              className="flex-1 bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] rounded-md px-3 py-1.5 outline-none focus:ring-1 focus:ring-[var(--color-accent)] border border-[var(--color-border)]"
            />
            <button
              onClick={runSearch}
              disabled={!ready || searching || !query.trim()}
              className="px-4 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--color-surface)]/70"
            >
              {searching ? "Searching…" : "Search"}
            </button>
          </div>
          {searchError ? (
            <div className="text-sm text-[var(--color-accent)]">
              {searchError}
            </div>
          ) : results === null ? (
            <div className="text-sm text-[var(--color-text-muted)] italic">
              Type a query above to see which devices hold each match.
            </div>
          ) : results.length === 0 ? (
            <div className="text-sm text-[var(--color-text-muted)] italic">
              No matching tracks.
            </div>
          ) : (
            <table className="w-full text-sm border border-[var(--color-border)] rounded-md overflow-hidden">
              <thead className="bg-[var(--color-surface)]/50 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                <tr>
                  <th className="text-left font-medium px-3 py-2">Artist</th>
                  <th className="text-left font-medium px-3 py-2">Title</th>
                  <th className="text-left font-medium px-3 py-2">Album</th>
                  <th className="text-left font-medium px-3 py-2">Held by</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr
                    key={r.mbid}
                    className="border-t border-[var(--color-border)]/60 align-top"
                  >
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">
                      {r.artist}
                    </td>
                    <td className="px-3 py-2 text-[var(--color-text)]">
                      {r.title}
                    </td>
                    <td className="px-3 py-2 text-[var(--color-text-muted)]">
                      {r.album ?? ""}
                    </td>
                    <td className="px-3 py-2">
                      {r.holders.length === 0 ? (
                        <span className="text-xs text-[var(--color-text-muted)] italic">
                          no device
                        </span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {r.holders.map((h) => (
                            <span
                              key={`${r.mbid}:${h.device_id}`}
                              title={h.local_path ?? ""}
                              className="inline-block rounded-full bg-sky-900/40 text-sky-200 text-[11px] px-2 py-0.5"
                            >
                              {h.device_id}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  );
}
