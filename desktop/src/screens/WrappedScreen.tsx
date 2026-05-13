import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import { fetchWrapped, type WrappedPayload } from "../lib/api";

type Props = {
  ready: boolean;
  // Year defaults to current UTC year on the backend; the picker here
  // lets the user step back a year at a time if they have older data.
  initialYear?: number;
  onBack: () => void;
};

function formatMs(ms: number): string {
  if (ms <= 0) return "0m";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const remM = minutes - hours * 60;
  if (hours < 24) return remM > 0 ? `${hours}h ${remM}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remH = hours - days * 24;
  return remH > 0 ? `${days}d ${remH}h` : `${days}d`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
    if (!isFinite(t)) return iso;
    return new Date(t).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

export default function WrappedScreen({ ready, initialYear, onBack }: Props) {
  const currentYear =
    initialYear ?? new Date().getUTCFullYear();
  const [year, setYear] = useState(currentYear);
  const [data, setData] = useState<WrappedPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const payload = await fetchWrapped(year);
        if (!cancelled) setData(payload);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready, year]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title={`Wrapped ${year}`} />
      <div className="titlebar-nodrag flex items-center justify-between px-6 py-2 border-b border-[var(--color-border)] text-sm">
        <button
          onClick={onBack}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          ← Back
        </button>
        <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
          <button
            onClick={() => setYear((y) => y - 1)}
            className="px-2 py-1 rounded hover:bg-[var(--color-surface)] hover:text-[var(--color-text)]"
            aria-label="Previous year"
          >
            ‹
          </button>
          <span className="tabular-nums text-[var(--color-text)] font-medium">
            {year}
          </span>
          <button
            onClick={() =>
              setYear((y) => Math.min(y + 1, new Date().getUTCFullYear()))
            }
            disabled={year >= new Date().getUTCFullYear()}
            className="px-2 py-1 rounded hover:bg-[var(--color-surface)] hover:text-[var(--color-text)] disabled:opacity-40"
            aria-label="Next year"
          >
            ›
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-8 py-8">
        {error ? (
          <p className="text-sm text-[var(--color-accent)]">{error}</p>
        ) : loading || !data ? (
          <p className="text-sm text-[var(--color-text-muted)]">Loading…</p>
        ) : data.total_plays === 0 ? (
          <div className="py-16 text-center">
            <h2 className="text-2xl font-semibold mb-2">
              Nothing yet for {year}
            </h2>
            <p className="text-sm text-[var(--color-text-muted)]">
              Once you've played a few tracks this year your recap will show up
              here.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-6 max-w-4xl mx-auto">
            <Hero data={data} />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <TopCard
                label="Top track"
                primary={data.top_track?.title ?? "—"}
                secondary={data.top_track?.artist ?? ""}
                tertiary={
                  data.top_track
                    ? `${data.top_track.plays} ${data.top_track.plays === 1 ? "play" : "plays"}`
                    : undefined
                }
              />
              <TopCard
                label="Top artist"
                primary={data.top_artist?.artist ?? "—"}
                secondary={
                  data.top_artist
                    ? `${data.top_artist.distinct_tracks} ${data.top_artist.distinct_tracks === 1 ? "track" : "tracks"}`
                    : ""
                }
                tertiary={
                  data.top_artist
                    ? `${data.top_artist.plays} ${data.top_artist.plays === 1 ? "play" : "plays"}`
                    : undefined
                }
              />
              <TopCard
                label="Top album"
                primary={data.top_album?.album ?? "—"}
                secondary={data.top_album?.artist ?? ""}
                tertiary={
                  data.top_album
                    ? `${data.top_album.plays} ${data.top_album.plays === 1 ? "play" : "plays"}`
                    : undefined
                }
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <FactCard
                label="Busiest day"
                value={formatDate(data.busiest_day?.date)}
                hint={
                  data.busiest_day
                    ? `${data.busiest_day.plays} plays`
                    : undefined
                }
              />
              <FactCard
                label="Top hour"
                value={
                  data.top_hour !== null
                    ? `${String(data.top_hour).padStart(2, "0")}:00`
                    : "—"
                }
                hint="UTC"
              />
              <FactCard
                label="Longest streak"
                value={`${data.longest_streak_days}d`}
                hint="consecutive days listening"
              />
              <FactCard
                label="First play"
                value={
                  data.first_play
                    ? data.first_play.title ?? "(unknown)"
                    : "—"
                }
                hint={
                  data.first_play
                    ? formatDate(data.first_play.played_at)
                    : undefined
                }
              />
            </div>
            {data.has_legacy_rows && (
              <p className="text-xs text-[var(--color-text-muted)]">
                Some plays were recorded before listening time was tracked, so
                the headline hours total may understate the real figure.
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Hero({ data }: { data: WrappedPayload }) {
  return (
    <div className="rounded-xl bg-gradient-to-br from-[var(--color-accent)]/30 via-[var(--color-surface)] to-[var(--color-bg-elevated)] p-8 text-center">
      <p className="text-xs uppercase tracking-widest text-[var(--color-text-muted)]">
        Your {data.year}
      </p>
      <p className="mt-2 text-5xl font-bold tabular-nums">
        {formatMs(data.total_listening_time_ms)}
      </p>
      <p className="mt-1 text-sm text-[var(--color-text-muted)]">
        across{" "}
        <span className="text-[var(--color-text)] font-medium">
          {data.total_plays.toLocaleString()}
        </span>{" "}
        plays
      </p>
    </div>
  );
}

function TopCard({
  label,
  primary,
  secondary,
  tertiary,
}: {
  label: string;
  primary: string;
  secondary: string;
  tertiary?: string;
}) {
  return (
    <div className="rounded-md bg-[var(--color-surface)]/40 border border-[var(--color-border)]/40 p-5">
      <p className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </p>
      <p className="mt-2 text-xl font-semibold truncate" title={primary}>
        {primary}
      </p>
      {secondary && (
        <p
          className="mt-0.5 text-sm text-[var(--color-text-muted)] truncate"
          title={secondary}
        >
          {secondary}
        </p>
      )}
      {tertiary && (
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">
          {tertiary}
        </p>
      )}
    </div>
  );
}

function FactCard({
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
      <p className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </p>
      <p className="mt-1 text-lg font-semibold truncate" title={value}>
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
          {hint}
        </p>
      )}
    </div>
  );
}
