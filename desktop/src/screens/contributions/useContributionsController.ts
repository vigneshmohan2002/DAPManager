import { useCallback, useEffect, useRef, useState } from "react";
import {
  contributeAllLocalTracks,
  fetchConfig,
  fetchContributions,
  fetchOutgoingContributions,
  fetchStatus,
  type BackendStatus,
  type Contribution,
} from "../../lib/api";
import {
  contributionCounts,
  parseDeviceContext,
  type DeviceContext,
} from "./model";

type ToastVariant = "ok" | "err";
type ShowToast = (message: string, variant?: ToastVariant) => void;

type Options = {
  ready: boolean;
  showToast: ShowToast;
};

export type ContributionsController = {
  items: Contribution[] | null;
  context: DeviceContext | null;
  status: BackendStatus | null;
  error: string | null;
  configError: string | null;
  refreshing: boolean;
  starting: boolean;
  running: boolean;
  isMaster: boolean;
  hasMaster: boolean;
  canContribute: boolean;
  activityItems: Contribution[] | null;
  pendingCount: number;
  completeCount: number;
  subtitle: string;
  refresh: () => Promise<void>;
  contributeAll: () => Promise<void>;
};

export function useContributionsController({
  ready,
  showToast,
}: Options): ContributionsController {
  const [items, setItems] = useState<Contribution[] | null>(null);
  const [outgoingItems, setOutgoingItems] = useState<Contribution[] | null>(
    null,
  );
  const [context, setContext] = useState<DeviceContext | null>(null);
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState(false);
  const wasRunning = useRef(false);

  const loadContributions = useCallback(async (showActivity = false) => {
    if (showActivity) setRefreshing(true);
    try {
      const [incoming, outgoing] = await Promise.all([
        fetchContributions(),
        fetchOutgoingContributions(),
      ]);
      setItems(incoming);
      setOutgoingItems(outgoing);
      setError(null);
    } catch (loadError) {
      setError(String(loadError));
    } finally {
      if (showActivity) setRefreshing(false);
    }
  }, []);

  const loadContext = useCallback(async () => {
    try {
      const payload = await fetchConfig();
      setContext(parseDeviceContext(payload.config));
      setConfigError(null);
    } catch (loadError) {
      setConfigError(String(loadError));
    }
  }, []);

  // Received offers can change while a satellite is polling or uploading.
  // Refresh periodically and immediately when work crosses running -> idle.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;

    const tickStatus = async () => {
      try {
        const next = await fetchStatus();
        if (cancelled) return;
        setStatus(next);
        if (wasRunning.current && !next.running) loadContributions();
        wasRunning.current = next.running;
      } catch {
        // Transient backend startup/network errors retry on the next tick.
      }
    };

    loadContext();
    loadContributions();
    tickStatus();
    const statusId = window.setInterval(tickStatus, 2000);
    const listId = window.setInterval(() => loadContributions(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(statusId);
      window.clearInterval(listId);
    };
  }, [ready, loadContext, loadContributions]);

  const refresh = useCallback(async () => {
    await Promise.all([
      loadContributions(true),
      loadContext(),
      fetchStatus().then(setStatus).catch(() => undefined),
    ]);
  }, [loadContext, loadContributions]);

  const contributeAll = useCallback(async () => {
    if (starting) return;
    setStarting(true);
    try {
      const result = await contributeAllLocalTracks();
      if (!result.success) {
        showToast(result.message || "Contribution could not start", "err");
        return;
      }
      showToast(result.message || "Contribution started.");
      try {
        setStatus(await fetchStatus());
      } catch {
        // The status poll will catch up in at most two seconds.
      }
    } catch (startError) {
      showToast(`Contribution could not start: ${String(startError)}`, "err");
    } finally {
      setStarting(false);
    }
  }, [showToast, starting]);

  const running = Boolean(status?.running);
  const isMaster =
    context?.role === "master" || context?.role === "standalone";
  const hasMaster = Boolean(context?.masterUrl);
  const canContribute =
    ready && !starting && !running && context !== null && !isMaster && hasMaster;
  const activityItems = isMaster ? items : outgoingItems;
  const counts = contributionCounts(activityItems);
  const subtitle = running
    ? status?.message ?? `${status?.task ?? "Task"} running…`
    : activityItems === null
      ? "Loading…"
      : `${activityItems.length} recent offer${activityItems.length === 1 ? "" : "s"}`;

  return {
    items,
    context,
    status,
    error,
    configError,
    refreshing,
    starting,
    running,
    isMaster,
    hasMaster,
    canContribute,
    activityItems,
    pendingCount: counts.pending,
    completeCount: counts.complete,
    subtitle,
    refresh,
    contributeAll,
  };
}
