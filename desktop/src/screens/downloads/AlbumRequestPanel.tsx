import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useToast } from "../../components/Toast";
import {
  fetchAlbumDownloadRequest,
  fetchAlbumDownloadRequests,
  fetchConfig,
  postAction,
  requestAlbumDownload,
  searchAlbumReleases,
  type AlbumDownloadRequest,
  type AlbumDownloadStage,
  type AlbumReleaseCandidate,
} from "../../lib/api";

const STORAGE_KEY_PREFIX = "dapmanager.desktop.albumRequestIds.v1";
const MAX_TRACKED_REQUESTS = 50;

type Props = {
  ready: boolean;
  onQueueChanged: () => void;
};

type SearchState = "idle" | "loading" | "ready" | "empty" | "error";

function loadTrackedIds(storageKey: string): number[] {
  try {
    const raw: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
    if (!Array.isArray(raw)) return [];
    return [...new Set(raw)]
      .map((value) => Number(value))
      .filter((value) => Number.isSafeInteger(value) && value > 0)
      .slice(0, MAX_TRACKED_REQUESTS);
  } catch {
    return [];
  }
}

function saveTrackedIds(storageKey: string | null, ids: readonly number[]): void {
  if (!storageKey) return;
  try {
    window.localStorage.setItem(
      storageKey,
      JSON.stringify(ids.slice(0, MAX_TRACKED_REQUESTS)),
    );
  } catch {
    // The in-memory tracker still works when storage is unavailable.
  }
}

function dismissedStorageKey(storageKey: string): string {
  return `${storageKey}:dismissed`;
}

function albumRequestAuthorityScope(config: Record<string, unknown>): string | null {
  const role = String(config.device_role ?? "").trim().toLowerCase();
  if (role === "satellite") {
    const rawMasterUrl = String(config.master_url ?? "").trim();
    if (!rawMasterUrl) return null;
    try {
      const parsed = new URL(rawMasterUrl);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
      if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
      const pathname = parsed.pathname.replace(/\/+$/, "");
      return `master:${parsed.origin}${pathname}`;
    } catch {
      return null;
    }
  }
  if (role === "master" || role === "standalone") return `role:${role}`;
  return null;
}

function isFinished(stage: AlbumDownloadStage): boolean {
  return stage === "success" || stage === "failed";
}

function candidateSummary(candidate: AlbumReleaseCandidate): string {
  return [
    candidate.date,
    candidate.country,
    candidate.format,
    `${candidate.track_count} track${candidate.track_count === 1 ? "" : "s"}`,
    candidate.status,
  ]
    .filter(Boolean)
    .join(" · ");
}

function editionSummary(candidate: AlbumReleaseCandidate): string {
  return [
    candidate.disambiguation,
    candidate.label,
    candidate.catalog_number,
    candidate.barcode ? `Barcode ${candidate.barcode}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}

function stageLabel(stage: AlbumDownloadStage): string {
  return {
    queued: "Queued",
    downloading: "Downloading",
    importing: "Importing",
    success: "Complete",
    failed: "Failed",
  }[stage];
}

export default function AlbumRequestPanel({ ready, onQueueChanged }: Props) {
  const [query, setQuery] = useState("");
  const [searchState, setSearchState] = useState<SearchState>("idle");
  const [searchError, setSearchError] = useState("");
  const [candidates, setCandidates] = useState<AlbumReleaseCandidate[]>([]);
  const [selectedMbid, setSelectedMbid] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [storageKey, setStorageKey] = useState<string | null>(null);
  const [trackedIds, setTrackedIds] = useState<number[]>([]);
  const [dismissedIds, setDismissedIds] = useState<Set<number>>(new Set());
  const dismissedIdsRef = useRef<Set<number>>(new Set());
  const [requests, setRequests] = useState<Record<number, AlbumDownloadRequest>>({});
  const requestsRef = useRef<Record<number, AlbumDownloadRequest>>({});
  const [pollErrors, setPollErrors] = useState<Record<number, string>>({});
  const reconcileAbortRef = useRef<AbortController | null>(null);
  const toast = useToast();

  const rememberRequest = useCallback((request: AlbumDownloadRequest) => {
    setRequests((current) => {
      const next = { ...current, [request.id]: request };
      requestsRef.current = next;
      return next;
    });
    setPollErrors((current) => {
      if (!(request.id in current)) return current;
      const next = { ...current };
      delete next[request.id];
      return next;
    });
    setTrackedIds((current) => {
      // Keep existing IDs in place. Reordering an ID on every poll changes this
      // effect's dependency and can continuously tear down/restart the polling
      // interval when two requests resolve in a different order.
      if (current.includes(request.id)) {
        saveTrackedIds(storageKey, current);
        return current;
      }
      const next = [request.id, ...current].slice(0, MAX_TRACKED_REQUESTS);
      saveTrackedIds(storageKey, next);
      return next;
    });
  }, [storageKey]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    // A readiness transition can follow a backend/master reconfiguration.
    // Drop the prior authority's numeric IDs before resolving the new scope.
    setStorageKey(null);
    setTrackedIds([]);
    setRequests({});
    requestsRef.current = {};
    setPollErrors({});
    const emptyDismissed = new Set<number>();
    dismissedIdsRef.current = emptyDismissed;
    setDismissedIds(emptyDismissed);
    const resolveStorageScope = async () => {
      try {
        const payload = await fetchConfig();
        const scope = albumRequestAuthorityScope(payload.config);
        if (!scope || cancelled) return;
        const key = `${STORAGE_KEY_PREFIX}:${encodeURIComponent(scope)}`;
        const stored = loadTrackedIds(key);
        const dismissed = new Set(loadTrackedIds(dismissedStorageKey(key)));
        setStorageKey(key);
        dismissedIdsRef.current = dismissed;
        setDismissedIds(dismissed);
        setTrackedIds((current) => {
          const merged = [...new Set([...current, ...stored])].slice(
            0,
            MAX_TRACKED_REQUESTS,
          );
          saveTrackedIds(key, merged);
          return merged;
        });
      } catch {
        // Keep request progress in memory when the authority cannot be proven.
      }
    };
    void resolveStorageScope();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  useEffect(() => {
    const trimmed = query.trim();
    setSelectedMbid(null);
    setCandidates([]);
    setSearchError("");
    if (trimmed.length < 2) {
      setSearchState("idle");
      return;
    }

    setSearchState("loading");
    const controller = new AbortController();
    let cancelled = false;
    let requestTimeout = 0;
    let timedOut = false;
    const timer = window.setTimeout(async () => {
      requestTimeout = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, 20_000);
      try {
        const result = await searchAlbumReleases(trimmed, controller.signal);
        if (cancelled) return;
        setCandidates(result.candidates);
        setSearchState(result.candidates.length ? "ready" : "empty");
      } catch (error) {
        if (cancelled || (controller.signal.aborted && !timedOut)) return;
        setSearchError(error instanceof Error ? error.message : String(error));
        setSearchState("error");
      } finally {
        window.clearTimeout(requestTimeout);
      }
    }, 450);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      window.clearTimeout(requestTimeout);
      controller.abort();
    };
  }, [query]);

  const reconcile = useCallback(async () => {
    if (!ready || reconcileAbortRef.current) return;
    const controller = new AbortController();
    reconcileAbortRef.current = controller;
    try {
      const active = await fetchAlbumDownloadRequests(controller.signal);
      if (controller.signal.aborted) return;
      active.forEach((request) => {
        const isActive = !isFinished(request.stage);
        if (!isActive && dismissedIdsRef.current.has(request.id)) return;
        if (isActive && dismissedIdsRef.current.has(request.id)) {
          const next = new Set(dismissedIdsRef.current);
          next.delete(request.id);
          dismissedIdsRef.current = next;
          setDismissedIds(next);
          if (storageKey) {
            saveTrackedIds(
              dismissedStorageKey(storageKey),
              [...next],
            );
          }
        }
        rememberRequest(request);
      });
    } catch {
      // Per-request polling below retains known progress during an outage.
    } finally {
      if (reconcileAbortRef.current === controller) {
        reconcileAbortRef.current = null;
      }
    }
  }, [ready, rememberRequest, storageKey]);

  useEffect(() => {
    if (!ready) return;
    void reconcile();
    const interval = window.setInterval(() => void reconcile(), 30_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") void reconcile();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibility);
      const controller = reconcileAbortRef.current;
      if (controller) {
        controller.abort();
        if (reconcileAbortRef.current === controller) {
          reconcileAbortRef.current = null;
        }
      }
    };
  }, [ready, reconcile, storageKey]);

  useEffect(() => {
    if (!ready || trackedIds.length === 0) return;
    const controller = new AbortController();
    let inFlight = false;

    const poll = async () => {
      if (inFlight || controller.signal.aborted) return;
      const ids = trackedIds.filter((id) => {
        const request = requestsRef.current[id];
        return !request || !isFinished(request.stage);
      });
      if (ids.length === 0) return;
      inFlight = true;
      try {
        await Promise.all(
          ids.map(async (id) => {
            try {
              const request = await fetchAlbumDownloadRequest(id, controller.signal);
              if (controller.signal.aborted) return;
              if (request) {
                rememberRequest(request);
              } else if (requestsRef.current[id]) {
                rememberRequest({
                  ...requestsRef.current[id],
                  stage: "failed",
                  detail: "This request no longer exists on the configured master.",
                });
              } else {
                setTrackedIds((current) => {
                  const next = current.filter((value) => value !== id);
                  saveTrackedIds(storageKey, next);
                  return next;
                });
              }
            } catch {
              if (controller.signal.aborted) return;
              setPollErrors((current) => ({
                ...current,
                [id]: "Progress is temporarily unavailable; tracking will retry.",
              }));
            }
          }),
        );
      } finally {
        inFlight = false;
      }
    };

    void poll();
    const interval = window.setInterval(() => void poll(), 5_000);
    return () => {
      window.clearInterval(interval);
      controller.abort();
    };
  }, [ready, rememberRequest, storageKey, trackedIds]);

  const selected = useMemo(
    () => candidates.find((candidate) => candidate.release_mbid === selectedMbid) ?? null,
    [candidates, selectedMbid],
  );

  const submitRelease = async (releaseMbid: string) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const result = await requestAlbumDownload(releaseMbid);
      if (!result.success || !result.request) {
        toast.show(result.message || "The master rejected this album request.", "err");
        return;
      }
      if (dismissedIdsRef.current.has(result.request.id)) {
        const next = new Set(dismissedIdsRef.current);
        next.delete(result.request.id);
        dismissedIdsRef.current = next;
        setDismissedIds(next);
        if (storageKey) {
          saveTrackedIds(dismissedStorageKey(storageKey), [...next]);
        }
      }
      rememberRequest(result.request);
      onQueueChanged();

      let started = false;
      if (result.queued && result.request.stage === "queued") {
        const run = await postAction("/api/download");
        started = run.success;
      }

      if (result.request.stage === "success") {
        toast.show("That release is already complete in the master library.", "ok");
      } else if (!result.queued) {
        toast.show(`That release is already ${stageLabel(result.request.stage).toLowerCase()}.`, "ok");
      } else if (started) {
        toast.show("Verified FLAC album request queued and downloader started.", "ok");
      } else {
        toast.show("Verified FLAC album request queued.", "ok");
      }
      setSelectedMbid(null);
      void reconcile();
    } catch (error) {
      toast.show(
        error instanceof Error ? error.message : "Could not request that album.",
        "err",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const clearFinished = () => {
    const finished = trackedIds.filter((id) => {
      const request = requests[id];
      return request ? isFinished(request.stage) : false;
    });
    const next = trackedIds.filter((id) => {
      const request = requests[id];
      return !request || !isFinished(request.stage);
    });
    setTrackedIds(next);
    saveTrackedIds(storageKey, next);
    if (storageKey && finished.length) {
      const dismissed = new Set([...dismissedIds, ...finished]);
      dismissedIdsRef.current = dismissed;
      setDismissedIds(dismissed);
      saveTrackedIds(dismissedStorageKey(storageKey), [...dismissed]);
    }
  };

  const orderedRequests = trackedIds
    .map((id) => requests[id])
    .filter((request): request is AlbumDownloadRequest => Boolean(request));
  const hasFinished = orderedRequests.some((request) => isFinished(request.stage));

  return (
    <section className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]/35 p-4 space-y-4">
      <div>
        <div className="text-sm font-semibold text-[var(--color-text)]">
          Request a verified FLAC album
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-1">
          Choose one specific MusicBrainz release. The master rechecks its release ID and
          recording manifest before it queues Soulseek.
        </div>
      </div>

      <label className="block">
        <span className="sr-only">Search MusicBrainz albums</span>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          disabled={!ready || submitting}
          placeholder="Artist - Album"
          className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2 text-sm text-[var(--color-text)] outline-none focus:border-[var(--color-accent)] disabled:opacity-50"
        />
      </label>

      {query.trim().length === 1 ? (
        <div className="text-xs text-[var(--color-text-muted)]">
          Type at least two characters to check MusicBrainz.
        </div>
      ) : null}
      {searchState === "loading" ? (
        <div className="text-xs text-[var(--color-text-muted)]">Checking MusicBrainz…</div>
      ) : null}
      {searchState === "empty" ? (
        <div className="text-xs text-[var(--color-text-muted)]">
          No album releases matched. Refine the artist or album name.
        </div>
      ) : null}
      {searchState === "error" ? (
        <div role="alert" className="text-xs text-[var(--color-accent)]">
          Could not check MusicBrainz: {searchError}
        </div>
      ) : null}

      {searchState === "ready" ? (
        <div className="space-y-2" aria-label="MusicBrainz album releases">
          {candidates.map((candidate) => {
            const selectedCandidate = candidate.release_mbid === selectedMbid;
            const edition = editionSummary(candidate);
            return (
              <button
                key={candidate.release_mbid}
                type="button"
                aria-pressed={selectedCandidate}
                onClick={() => setSelectedMbid(candidate.release_mbid)}
                className={`w-full rounded-md border px-3 py-3 text-left transition-colors ${
                  selectedCandidate
                    ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10"
                    : "border-[var(--color-border)] bg-[var(--color-bg)] hover:border-[var(--color-text-muted)]"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-[var(--color-text)] truncate">
                      {candidate.title}
                    </div>
                    <div className="text-xs text-[var(--color-text-muted)] truncate">
                      {candidate.artist}
                    </div>
                    <div className="text-xs text-[var(--color-text-muted)] mt-1">
                      {candidateSummary(candidate)}
                    </div>
                    {edition ? (
                      <div className="text-xs text-[var(--color-text-muted)] mt-1">
                        {edition}
                      </div>
                    ) : null}
                    <div className="text-[11px] font-mono text-[var(--color-text-muted)] mt-1 break-all">
                      {candidate.release_mbid}
                    </div>
                  </div>
                  <span className="text-[var(--color-accent)]" aria-hidden="true">
                    {selectedCandidate ? "✓" : ""}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      ) : null}

      {selected ? (
        <button
          type="button"
          disabled={submitting || !ready}
          onClick={() => void submitRelease(selected.release_mbid)}
          className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
        >
          {submitting ? "Queuing verified release…" : `Request “${selected.title}”`}
        </button>
      ) : null}

      {orderedRequests.length ? (
        <div className="pt-2 space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
              Verified album progress
            </div>
            {hasFinished ? (
              <button
                type="button"
                onClick={clearFinished}
                className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Clear finished
              </button>
            ) : null}
          </div>
          {orderedRequests.map((request) => (
            <AlbumProgressCard
              key={request.id}
              request={request}
              pollError={pollErrors[request.id]}
              retrying={submitting}
              onRetry={() => void submitRelease(request.release_mbid)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function AlbumProgressCard({
  request,
  pollError,
  retrying,
  onRetry,
}: {
  request: AlbumDownloadRequest;
  pollError?: string;
  retrying: boolean;
  onRetry: () => void;
}) {
  const completed = Math.min(
    request.track_count,
    Math.max(0, request.completed_tracks),
  );
  const percent =
    request.stage === "success"
      ? 100
      : request.track_count > 0
        ? Math.round((completed / request.track_count) * 100)
        : 0;

  return (
    <article className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--color-text)] truncate">
            {request.title}
          </div>
          <div className="text-xs text-[var(--color-text-muted)]">
            {request.artist} · {completed}/{request.track_count} tracks
          </div>
        </div>
        <span className="text-xs font-semibold text-[var(--color-accent)]">
          {stageLabel(request.stage)}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`${request.title} album progress`}
        aria-valuemin={0}
        aria-valuemax={request.track_count}
        aria-valuenow={completed}
        className="h-1.5 rounded-full bg-[var(--color-border)] overflow-hidden mt-3"
      >
        <div
          className="h-full bg-[var(--color-accent)] transition-[width]"
          style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
        />
      </div>
      <div className="text-xs text-[var(--color-text-muted)] mt-2 whitespace-pre-wrap">
        {pollError || request.detail || "Waiting for the master download queue."}
      </div>
      {request.stage === "failed" ? (
        <button
          type="button"
          disabled={retrying}
          onClick={onRetry}
          className="mt-2 text-xs px-2 py-1 rounded bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-text)] hover:bg-[var(--color-surface)]/70 disabled:opacity-50"
        >
          Retry verified release
        </button>
      ) : null}
    </article>
  );
}
