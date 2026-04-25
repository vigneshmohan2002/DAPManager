import { useEffect, useRef, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchAuditResults,
  fetchStatus,
  runCompleteAlbums,
  type BackendStatus,
  type IncompleteAlbum,
} from "../lib/api";

type Props = {
  ready: boolean;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; results: IncompleteAlbum[] }
  | { kind: "error"; message: string };

export default function AuditScreen({ ready }: Props) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [runDownloads, setRunDownloads] = useState(false);
  const wasRunning = useRef(false);
  const toast = useToast();

  const loadResults = async () => {
    try {
      const results = await fetchAuditResults();
      setLoad({ kind: "loaded", results });
    } catch (e) {
      setLoad({ kind: "error", message: String(e) });
    }
  };

  useEffect(() => {
    if (ready) loadResults();
  }, [ready]);

  // Mirrors SyncScreen: poll /api/status while mounted; the
  // running→idle edge re-runs the audit query so the list reflects
  // whatever Complete Albums just queued/downloaded.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchStatus();
        if (cancelled) return;
        setStatus(s);
        if (wasRunning.current && !s.running) {
          loadResults();
        }
        wasRunning.current = s.running;
      } catch {
        // transient; next tick retries
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ready]);

  const running = Boolean(status?.running);

  const handleComplete = async () => {
    const msg = runDownloads
      ? "Discover missing tracks for incomplete albums, queue them, then run the downloader and rescan?"
      : "Discover missing tracks for incomplete albums and queue them for download?";
    if (!window.confirm(msg)) return;
    const result = await runCompleteAlbums(runDownloads);
    if (!result.success) {
      toast.show(result.message || "Complete Albums failed", "err");
      return;
    }
    toast.show(result.message || "Album completion started.");
  };

  const subtitle =
    running && status?.message
      ? status.message
      : load.kind === "loaded"
      ? load.results.length === 0
        ? "All identified albums appear to be complete"
        : `${load.results.length} incomplete album${
            load.results.length === 1 ? "" : "s"
          }`
      : load.kind === "loading"
      ? "Loading…"
      : "Failed to load";

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Audit" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        <section className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleComplete}
            disabled={!ready || running}
            className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            Complete Albums
          </button>
          <label className="flex items-center gap-2 text-sm text-[var(--color-text-muted)] select-none">
            <input
              type="checkbox"
              checked={runDownloads}
              onChange={(e) => setRunDownloads(e.target.checked)}
              disabled={running}
              className="accent-[var(--color-accent)]"
            />
            Also run downloader & rescan
          </label>
          <button
            onClick={loadResults}
            disabled={!ready}
            className="ml-auto px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] disabled:opacity-50 hover:bg-[var(--color-surface)]/70 border border-[var(--color-border)]"
          >
            Refresh
          </button>
        </section>

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

        <ResultsBody load={load} />
      </div>
    </div>
  );
}

function ResultsBody({ load }: { load: LoadState }) {
  if (load.kind === "loading") {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Scanning library…
      </div>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="text-sm text-[var(--color-accent)]">{load.message}</div>
    );
  }
  if (load.results.length === 0) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Nothing to fix — every identified album has all of its tracks.
      </div>
    );
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
      {load.results.map((row) => (
        <AlbumCard key={row.mbid} row={row} />
      ))}
    </div>
  );
}

function AlbumCard({ row }: { row: IncompleteAlbum }) {
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]/40 overflow-hidden flex flex-col">
      <div className="aspect-square bg-[var(--color-bg)] relative">
        {!imgFailed ? (
          <img
            src={row.cover_art}
            alt=""
            loading="lazy"
            onError={() => setImgFailed(true)}
            className="absolute inset-0 w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-[var(--color-text-muted)]">
            no cover
          </div>
        )}
        <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded bg-[var(--color-accent)]/90 text-[var(--color-bg)] text-xs font-semibold">
          {row.missing} missing
        </div>
      </div>
      <div className="p-3 flex-1 flex flex-col gap-0.5 min-w-0">
        <div
          className="text-sm text-[var(--color-text)] truncate"
          title={row.album}
        >
          {row.album}
        </div>
        <div
          className="text-xs text-[var(--color-text-muted)] truncate"
          title={row.artist}
        >
          {row.artist}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-1">
          {row.have} / {row.total} tracks
        </div>
      </div>
    </div>
  );
}
