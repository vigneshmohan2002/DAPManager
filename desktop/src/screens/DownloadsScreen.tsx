import { useEffect, useMemo, useRef, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  clearCompletedDownloads,
  deleteDownload,
  fetchDownloads,
  fetchStatus,
  postAction,
  retryDownload,
  type BackendStatus,
  type DownloadQueueItem,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = { ready: boolean };

// Buckets the queue by status. The schema's "success" state shows up
// as "Completed" in the UI — keep DB names internal and labels
// user-facing only at render time.
type Bucket = {
  key: "pending" | "failed" | "success";
  label: string;
  empty: string;
};

const BUCKETS: Bucket[] = [
  { key: "pending", label: "Pending", empty: "Nothing queued." },
  { key: "failed", label: "Failed", empty: "No failed downloads." },
  { key: "success", label: "Completed", empty: "No completed downloads yet." },
];

export default function DownloadsScreen({ ready }: Props) {
  const [items, setItems] = useState<DownloadQueueItem[] | null>(null);
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Per-row in-flight flag so the buttons can disable while the action
  // is on the wire — prevents double-retry / double-remove.
  const [busyId, setBusyId] = useState<number | null>(null);
  const wasRunning = useRef(false);
  const toast = useToast();

  const load = async () => {
    try {
      setItems(await fetchDownloads());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  // Initial load + reload on running→idle edge so completed/failed
  // rows surface without the user touching anything. Same edge-trigger
  // pattern as SyncScreen.
  useEffect(() => {
    if (!ready) return;
    load();
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchStatus();
        if (cancelled) return;
        setStatus(s);
        if (wasRunning.current && !s.running) load();
        wasRunning.current = s.running;
      } catch {
        // ignored — next tick retries
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ready]);

  const buckets = useMemo(() => {
    const groups: Record<Bucket["key"], DownloadQueueItem[]> = {
      pending: [],
      failed: [],
      success: [],
    };
    for (const it of items ?? []) {
      if (it.status === "pending" || it.status === "failed" || it.status === "success") {
        groups[it.status].push(it);
      }
    }
    return groups;
  }, [items]);

  const running = Boolean(status?.running);
  const completedCount = buckets.success.length;
  const queueIsBusy = running;

  const onRunDownloader = async () => {
    const result = await postAction("/api/download");
    if (!result.success) {
      toast.show(result.message || "Run failed", "err");
      return;
    }
    toast.show("Downloader started.", "ok");
  };

  const onRetry = async (id: number) => {
    setBusyId(id);
    const result = await retryDownload(id);
    setBusyId(null);
    if (!result.success) {
      toast.show(result.message || "Retry failed", "err");
      return;
    }
    toast.show("Queued for retry.", "ok");
    load();
  };

  const onRemove = async (id: number) => {
    if (!window.confirm("Remove this row from the queue?")) return;
    setBusyId(id);
    const result = await deleteDownload(id);
    setBusyId(null);
    if (!result.success) {
      toast.show(result.message || "Remove failed", "err");
      return;
    }
    load();
  };

  const onClearCompleted = async () => {
    if (completedCount === 0) {
      toast.show("Nothing to clear.", "ok");
      return;
    }
    if (
      !window.confirm(
        `Remove ${completedCount} completed row${completedCount === 1 ? "" : "s"}?`,
      )
    )
      return;
    const result = await clearCompletedDownloads();
    if (!result.success) {
      toast.show(result.message || "Clear failed", "err");
      return;
    }
    toast.show(
      `Cleared ${result.removed} row${result.removed === 1 ? "" : "s"}.`,
      "ok",
    );
    load();
  };

  const subtitle =
    error !== null
      ? "Failed to load"
      : items === null
      ? "Loading…"
      : running
      ? status?.message ?? "Downloader running…"
      : `${buckets.pending.length} pending · ${buckets.failed.length} failed · ${buckets.success.length} completed`;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Downloads" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        <section className="flex items-center gap-3">
          <button
            onClick={onRunDownloader}
            disabled={!ready || queueIsBusy}
            className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            Run Downloader
          </button>
          <button
            onClick={onClearCompleted}
            disabled={!ready || completedCount === 0}
            className="px-3 py-2 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--color-surface)]/70"
          >
            Clear completed
          </button>
        </section>

        {error ? (
          <div className="text-sm text-[var(--color-accent)]">{error}</div>
        ) : (
          BUCKETS.map((b) => (
            <Section
              key={b.key}
              label={b.label}
              empty={b.empty}
              rows={buckets[b.key]}
              showRetry={b.key === "failed"}
              busyId={busyId}
              onRetry={onRetry}
              onRemove={onRemove}
              loading={items === null}
            />
          ))
        )}

        {running && (status?.message || status?.detail) ? (
          <section className="rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-3">
            <div className="text-xs uppercase tracking-wider text-[var(--color-accent)] mb-1">
              {status?.task ?? "Running"}
            </div>
            <div className="text-sm text-[var(--color-text)]">
              {status?.message}
            </div>
            {status?.detail ? (
              <div className="text-xs text-[var(--color-text-muted)] mt-1 font-mono whitespace-pre-wrap">
                {status.detail}
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </div>
  );
}

function Section({
  label,
  empty,
  rows,
  showRetry,
  busyId,
  onRetry,
  onRemove,
  loading,
}: {
  label: string;
  empty: string;
  rows: DownloadQueueItem[];
  showRetry: boolean;
  busyId: number | null;
  onRetry: (id: number) => void;
  onRemove: (id: number) => void;
  loading: boolean;
}) {
  return (
    <section>
      <div className="flex items-baseline justify-between mb-2">
        <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
          {label}
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          {rows.length}
        </div>
      </div>
      {loading ? (
        <div className="text-sm text-[var(--color-text-muted)] italic">
          Loading…
        </div>
      ) : rows.length === 0 ? (
        <div className="text-sm text-[var(--color-text-muted)] italic">
          {empty}
        </div>
      ) : (
        <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
                <th className="px-3 py-2">Query</th>
                <th className="px-3 py-2 whitespace-nowrap">Last attempt</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr
                  key={r.id}
                  className={
                    i > 0 ? "border-t border-[var(--color-border)]" : ""
                  }
                >
                  <td className="px-3 py-2 text-[var(--color-text)] truncate max-w-[480px]">
                    {r.query}
                  </td>
                  <td className="px-3 py-2 text-[var(--color-text-muted)] whitespace-nowrap">
                    {relativeTime(r.last_attempt)}
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    {showRetry ? (
                      <button
                        onClick={() => onRetry(r.id)}
                        disabled={busyId === r.id}
                        className="text-xs px-2 py-1 rounded mr-2 bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface)]/70 disabled:opacity-50"
                      >
                        Retry
                      </button>
                    ) : null}
                    <button
                      onClick={() => onRemove(r.id)}
                      disabled={busyId === r.id}
                      className="text-xs px-2 py-1 rounded bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-50"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
