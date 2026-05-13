import { useEffect, useMemo, useState } from "react";
import TopBar from "../components/TopBar";
import {
  fetchPlayStats,
  type PlayStats,
  type PlayStatsArtist,
  type PlayStatsRecent,
  type PlayStatsTrack,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = {
  ready: boolean;
};

type WindowKey = "30d" | "90d" | "all";

const WINDOWS: Array<{ key: WindowKey; label: string; days: number | null }> = [
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
  { key: "all", label: "All time", days: null },
];

// Compute a SQLite-comparable cutoff string for the selected window.
// The DB stores ``CURRENT_TIMESTAMP`` as ``YYYY-MM-DD HH:MM:SS`` in UTC,
// which sorts lexically with ISO-8601 — emit the same shape so the
// "since" param compares correctly even though sqlite is type-loose.
function cutoffFor(window: WindowKey): string | undefined {
  const w = WINDOWS.find((x) => x.key === window);
  if (!w || w.days === null) return undefined;
  const ms = Date.now() - w.days * 24 * 60 * 60 * 1000;
  return new Date(ms).toISOString().replace("T", " ").slice(0, 19);
}

export default function StatsScreen({ ready }: Props) {
  const [windowKey, setWindowKey] = useState<WindowKey>("30d");
  const [stats, setStats] = useState<PlayStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const data = await fetchPlayStats({
          since: cutoffFor(windowKey),
          limit: 25,
        });
        if (!cancelled) setStats(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, windowKey]);

  const subtitle = useMemo(() => {
    if (loading) return "Loading…";
    if (error) return "Failed to load";
    if (!stats) return "";
    const win = WINDOWS.find((w) => w.key === windowKey);
    return `${stats.total} play${stats.total === 1 ? "" : "s"} · ${win?.label.toLowerCase()}`;
  }, [loading, error, stats, windowKey]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Listening" subtitle={subtitle} />
      <div className="titlebar-nodrag flex items-center gap-2 px-6 py-2 border-b border-[var(--color-border)] text-sm">
        {WINDOWS.map((w) => {
          const active = w.key === windowKey;
          return (
            <button
              key={w.key}
              onClick={() => setWindowKey(w.key)}
              className={`px-3 py-1 rounded-md transition-colors ${
                active
                  ? "bg-[var(--color-accent)] text-[var(--color-bg)] font-semibold"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface)]/60"
              }`}
            >
              {w.label}
            </button>
          );
        })}
      </div>
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {error ? (
          <div className="text-sm text-[var(--color-accent)]">{error}</div>
        ) : loading || !stats ? (
          <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
        ) : stats.total === 0 ? (
          <EmptyState />
        ) : (
          <div className="flex flex-col gap-8">
            <SummaryCards stats={stats} />
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              <TopTracksSection tracks={stats.top_tracks} />
              <TopArtistsSection artists={stats.top_artists} />
              <RecentSection recent={stats.recent} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Human-readable "Xh Ym" for a millisecond count. Trims to bare
// minutes under one hour and to bare days past a week so the headline
// number stays readable instead of "2 days 13 hours 4 minutes".
function formatListeningTime(ms: number): string {
  if (ms <= 0) return "0m";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remM = minutes - hours * 60;
  if (hours < 24) {
    return remM > 0 ? `${hours}h ${remM}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const remH = hours - days * 24;
  return remH > 0 ? `${days}d ${remH}h` : `${days}d`;
}

function SummaryCards({ stats }: { stats: PlayStats }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
      <Card label="Plays" value={String(stats.total)} />
      <Card
        label="Listening time"
        value={formatListeningTime(stats.listening_time_ms)}
        hint={
          stats.listening_time_ms === 0 && stats.total > 0
            ? "Plays before this version don't carry duration — newer plays will."
            : undefined
        }
      />
      <Card
        label="Top artist"
        value={stats.top_artists[0]?.artist ?? "—"}
        hint={
          stats.top_artists[0]
            ? `${stats.top_artists[0].plays} ${stats.top_artists[0].plays === 1 ? "play" : "plays"}`
            : undefined
        }
      />
    </div>
  );
}

function Card({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-md bg-[var(--color-surface)]/40 border border-[var(--color-border)]/40 p-4">
      <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold truncate" title={value}>
        {value}
      </div>
      {hint && (
        <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
          {hint}
        </div>
      )}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="max-w-md text-sm text-[var(--color-text-muted)] space-y-2">
      <p>
        No plays recorded for this window yet. Play events are recorded
        once a track has played for at least 30 seconds, or half its
        duration — whichever comes first.
      </p>
      <p>
        Plays from the desktop player are tagged{" "}
        <code className="text-[var(--color-text)]">desktop</code>. Future
        clients can attribute differently via the{" "}
        <code className="text-[var(--color-text)]">source</code> field.
      </p>
    </div>
  );
}

function TopTracksSection({ tracks }: { tracks: PlayStatsTrack[] }) {
  return (
    <section>
      <SectionTitle>Top tracks</SectionTitle>
      {tracks.length === 0 ? (
        <Empty>No tracks for this window.</Empty>
      ) : (
        <ol className="space-y-1">
          {tracks.map((t, i) => (
            <li
              key={t.mbid}
              className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-[var(--color-surface)]/40"
            >
              <span className="w-6 shrink-0 text-right text-xs text-[var(--color-text-muted)]">
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm">
                  {t.title ?? (
                    <span className="italic text-[var(--color-text-muted)]">
                      (unknown track)
                    </span>
                  )}
                </div>
                <div className="truncate text-xs text-[var(--color-text-muted)]">
                  {t.artist ?? "—"}
                  {t.album ? ` · ${t.album}` : ""}
                </div>
              </div>
              <div className="shrink-0 text-sm tabular-nums text-[var(--color-text-muted)]">
                {t.plays}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function TopArtistsSection({ artists }: { artists: PlayStatsArtist[] }) {
  return (
    <section>
      <SectionTitle>Top artists</SectionTitle>
      {artists.length === 0 ? (
        <Empty>No artists for this window.</Empty>
      ) : (
        <ol className="space-y-1">
          {artists.map((a, i) => (
            <li
              key={a.artist}
              className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-[var(--color-surface)]/40"
            >
              <span className="w-6 shrink-0 text-right text-xs text-[var(--color-text-muted)]">
                {i + 1}
              </span>
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm">{a.artist}</div>
                <div className="truncate text-xs text-[var(--color-text-muted)]">
                  {a.distinct_tracks} distinct track
                  {a.distinct_tracks === 1 ? "" : "s"}
                </div>
              </div>
              <div className="shrink-0 text-sm tabular-nums text-[var(--color-text-muted)]">
                {a.plays}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function RecentSection({ recent }: { recent: PlayStatsRecent[] }) {
  return (
    <section className="lg:col-span-2">
      <SectionTitle>Recent plays</SectionTitle>
      {recent.length === 0 ? (
        <Empty>No recent plays.</Empty>
      ) : (
        <ul className="space-y-1">
          {recent.map((r) => (
            <li
              key={r.id}
              className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-[var(--color-surface)]/40"
            >
              <div className="flex-1 min-w-0">
                <div className="truncate text-sm">
                  {r.title ?? (
                    <span className="italic text-[var(--color-text-muted)]">
                      (unknown track)
                    </span>
                  )}
                </div>
                <div className="truncate text-xs text-[var(--color-text-muted)]">
                  {r.artist ?? "—"}
                  {r.album ? ` · ${r.album}` : ""}
                </div>
              </div>
              <div
                className="shrink-0 text-xs text-[var(--color-text-muted)]"
                title={r.played_at}
              >
                {relativeTime(r.played_at)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
      {children}
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-sm text-[var(--color-text-muted)] italic">
      {children}
    </div>
  );
}
