import { useEffect, useState } from "react";

import { useToast } from "../../components/Toast";
import {
  fetchStatus,
  regenerateDailyMixes,
  startTagBackfill,
  type BackendStatus,
} from "../../lib/api";

type Props = {
  ready: boolean;
};

export default function LibraryTools({ ready }: Props) {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [kicking, setKicking] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  const toast = useToast();

  const backfilling = Boolean(
    status?.running && status.task === "Genre tag backfill",
  );

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const nextStatus = await fetchStatus();
        if (cancelled) return;
        setStatus(nextStatus);
        timer = setTimeout(tick, nextStatus.running ? 1500 : 5000);
      } catch {
        if (!cancelled) timer = setTimeout(tick, 5000);
      }
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [ready]);

  const handleBackfill = async () => {
    setKicking(true);
    try {
      const result = await startTagBackfill(true);
      if (!result.success) {
        toast.show(result.message ?? "Couldn't start backfill", "err");
        return;
      }
      toast.show("Tag backfill started");
      fetchStatus().then(setStatus).catch(() => {});
    } finally {
      setKicking(false);
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      const result = await regenerateDailyMixes();
      if (!result.success) {
        toast.show(result.message ?? "Couldn't regenerate", "err");
        return;
      }
      if (result.mixes === 0) {
        const reason =
          result.reason === "cold_start"
            ? "Play more tracks first — we need more listening history."
            : result.reason === "no_tags"
              ? "Run the genre tag backfill first."
              : "No mixes generated.";
        toast.show(reason);
        return;
      }
      toast.show(
        `Regenerated ${result.mixes} Daily Mix${result.mixes === 1 ? "" : "es"}.`,
      );
    } finally {
      setRegenerating(false);
    }
  };

  return (
    <fieldset className="border border-[var(--color-border)] rounded-md px-4 pt-3 pb-4">
      <legend className="px-2 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
        Library tools
      </legend>
      <div className="mt-2 flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-sm">Backfill genre tags from MusicBrainz</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Walks your library and asks MusicBrainz for each artist's top tags.
            Powers genre filters on smart playlists and the Artist Radio
            feature. Rate-limited to ~1 request/sec, so a 500-artist library
            takes about 15 minutes. Incremental — re-running skips artists
            already tagged in the last 30 days.
          </p>
          {backfilling && status?.detail && (
            <p className="mt-2 text-xs text-[var(--color-text)] font-mono">
              {status.detail}
            </p>
          )}
        </div>
        <button
          onClick={handleBackfill}
          disabled={!ready || kicking || backfilling}
          className="shrink-0 px-3 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-medium disabled:opacity-50"
        >
          {backfilling ? "Running…" : kicking ? "Starting…" : "Backfill"}
        </button>
      </div>
      <div className="mt-4 pt-4 border-t border-[var(--color-border)]/40 flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-sm">Regenerate Daily Mixes</p>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Clusters your top artists into 4–6 themed mixes shown on Home.
            Needs at least eight artists with three or more plays in the last
            90 days, plus a finished tag backfill. Pure-SQL — runs in
            milliseconds.
          </p>
        </div>
        <button
          onClick={handleRegenerate}
          disabled={!ready || regenerating}
          className="shrink-0 px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm border border-[var(--color-border)] hover:bg-[var(--color-surface)]/70 disabled:opacity-50"
        >
          {regenerating ? "…" : "Regenerate"}
        </button>
      </div>
    </fieldset>
  );
}
