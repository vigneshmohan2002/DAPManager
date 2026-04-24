import { useEffect, useRef, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchStatus,
  fetchSyncState,
  postAction,
  type BackendStatus,
  type SyncState,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = {
  ready: boolean;
};

// Each row represents one step in the Multi-Device Sync card on the
// web dashboard. `cursorKey` is the field in /api/sync/state whose
// timestamp we show; Link Local Files has no cursor.
type StepKey =
  | "catalog_pull"
  | "playlist_pull"
  | "playlist_push"
  | "inventory_report"
  | "link_local";

type Step = {
  key: StepKey;
  label: string;
  endpoint: string;
  runningMessage: string;
  cursorKey: keyof SyncState | null;
  hint?: string;
};

const STEPS: Step[] = [
  {
    key: "catalog_pull",
    label: "Pull Catalog",
    endpoint: "/api/catalog/pull",
    runningMessage: "Pulling catalog delta…",
    cursorKey: "last_catalog_sync",
    hint: "Delta fetch of the master's tracks. Run this first so playlist membership resolves.",
  },
  {
    key: "playlist_pull",
    label: "Pull Playlists",
    endpoint: "/api/playlists/pull",
    runningMessage: "Pulling playlists…",
    cursorKey: "last_playlist_sync",
  },
  {
    key: "playlist_push",
    label: "Push Playlists",
    endpoint: "/api/playlists/push",
    runningMessage: "Pushing playlists…",
    cursorKey: "last_playlist_push",
  },
  {
    key: "inventory_report",
    label: "Report Inventory",
    endpoint: "/api/inventory/report",
    runningMessage: "Reporting inventory…",
    cursorKey: "last_inventory_report",
    hint: "Publishes this device's MBID→path map. Requires report_inventory_to_host in config.",
  },
  {
    key: "link_local",
    label: "Link Local Files",
    endpoint: "/api/catalog/link-local",
    runningMessage: "Scanning library to link catalog rows…",
    cursorKey: null,
    hint: "Walks music_library_path and binds unlinked catalog rows to on-disk files by MBID / ISRC / artist+title. Safe to re-run.",
  },
];

export default function SyncScreen({ ready }: Props) {
  const [state, setState] = useState<SyncState | null>(null);
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const wasRunning = useRef(false);
  const toast = useToast();

  // /api/status poll runs continuously while the screen is mounted
  // (cheap — single row from a mutex-guarded dict). When a run
  // finishes, refresh /api/sync/state so the cursors advance without
  // the user touching anything.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await fetchStatus();
        if (cancelled) return;
        setStatus(s);
        if (wasRunning.current && !s.running) {
          loadState();
        }
        wasRunning.current = s.running;
      } catch {
        // transient — ignore, next tick retries
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ready]);

  const loadState = async () => {
    try {
      const s = await fetchSyncState();
      setState(s);
    } catch (e) {
      toast.show(String(e), "err");
    }
  };

  useEffect(() => {
    if (ready) loadState();
  }, [ready]);

  const running = Boolean(status?.running);

  const trigger = async (endpoint: string) => {
    const result = await postAction(endpoint);
    // Non-success responses carry a config-gated reason ("master_url
    // not configured", "report_inventory_to_host disabled", etc).
    // Surface them verbatim — the user needs to know exactly which
    // config key to set.
    if (!result.success) {
      toast.show(result.message || "Request failed", "err");
      return;
    }
    // Bump cursors a moment after the task kicks off so the user
    // sees fresh timestamps without waiting for the next 30s tick.
    // `wasRunning` handles the running→idle edge; this covers the
    // immediately-succeeded case too.
    window.setTimeout(loadState, 2000);
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Sync"
        subtitle={running ? status?.message ?? "Running…" : "Idle"}
      />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        <section>
          <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Multi-Device Sync
          </div>
          <p className="text-sm text-[var(--color-text-muted)] max-w-2xl mb-4">
            Keep this device's catalog in sync with the master DAPManager.
            Pull the catalog first so playlist membership resolves. Requires{" "}
            <code className="text-[var(--color-text)]">master_url</code> in
            config.
          </p>
          <button
            onClick={() => trigger("/api/sync/all")}
            disabled={!ready || running}
            className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            Sync All
          </button>
        </section>

        <section>
          <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Individual Steps
          </div>
          <div className="border border-[var(--color-border)] rounded-md overflow-hidden">
            {STEPS.map((step, i) => {
              const stamp = step.cursorKey ? state?.[step.cursorKey] : null;
              return (
                <div
                  key={step.key}
                  className={`flex items-start gap-4 px-4 py-3 ${
                    i > 0 ? "border-t border-[var(--color-border)]" : ""
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-[var(--color-text)]">
                      {step.label}
                    </div>
                    {step.hint ? (
                      <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                        {step.hint}
                      </div>
                    ) : null}
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)] whitespace-nowrap pt-1">
                    {step.cursorKey ? `Last: ${relativeTime(stamp ?? null)}` : null}
                  </div>
                  <button
                    onClick={() => trigger(step.endpoint)}
                    disabled={!ready || running}
                    className="px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--color-surface)]/70 border border-[var(--color-border)]"
                  >
                    Run
                  </button>
                </div>
              );
            })}
          </div>
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
      </div>
    </div>
  );
}
