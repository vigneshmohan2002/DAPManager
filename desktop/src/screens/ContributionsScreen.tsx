import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import { relativeTime } from "../lib/time";
import {
  audioQualityLabel,
  contributionStatusMeta,
} from "./contributions/model";
import { useContributionsController } from "./contributions/useContributionsController";

type Props = {
  ready: boolean;
  onOpenSettings: (focusKey?: string) => void;
};

export default function ContributionsScreen({
  ready,
  onOpenSettings,
}: Props) {
  const toast = useToast();
  const {
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
    pendingCount,
    completeCount,
    subtitle,
    refresh: handleRefresh,
    contributeAll: handleContributeAll,
  } = useContributionsController({ ready, showToast: toast.show });

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Contributions" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        <section className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-2xl">
              <div className="flex items-center gap-2 mb-1">
                <h2 className="text-sm font-semibold">Contribute local music</h2>
                {context ? (
                  <span className="rounded-full bg-[var(--color-surface)] px-2 py-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                    {context.role}
                  </span>
                ) : null}
              </div>
              <p className="text-sm text-[var(--color-text-muted)]">
                Offer this device's local tracks to the master. The master first
                tries to acquire an equal-or-better copy, then requests an upload
                only when necessary.
              </p>
              {context && !isMaster && hasMaster ? (
                <div className="mt-2 text-xs text-[var(--color-text-muted)]">
                  Destination: {context.masterUrl}
                  {!context.automatic ? (
                    <span className="text-amber-300">
                      {" "}· automatic contribution is disabled
                    </span>
                  ) : null}
                </div>
              ) : null}
              {isMaster ? (
                <div className="mt-2 text-xs text-[var(--color-text-muted)]">
                  {context?.role === "standalone"
                    ? "This is a local-only device with no upstream master."
                    : "This device receives contributions. Run this action from a satellite instead."}
                </div>
              ) : null}
              {context && !isMaster && !hasMaster ? (
                <div className="mt-2 flex items-center gap-3 text-xs text-amber-300">
                  <span>Set master_url before contributing.</span>
                  <button
                    onClick={() => onOpenSettings("master_url")}
                    className="text-[var(--color-accent)] hover:underline"
                  >
                    Open Settings
                  </button>
                </div>
              ) : null}
              {configError ? (
                <div className="mt-2 text-xs text-[var(--color-accent)]">
                  {configError}
                </div>
              ) : null}
            </div>
            <button
              onClick={handleContributeAll}
              disabled={!canContribute}
              className="shrink-0 px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
            >
              {starting
                ? "Starting…"
                : running
                  ? "Task running…"
                  : "Contribute all local tracks"}
            </button>
          </div>
        </section>

        {running && (status?.message || status?.detail) ? (
          <section className="rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-3">
            <div className="text-xs uppercase tracking-wider text-[var(--color-accent)] mb-1">
              {status.task ?? "Running"}
            </div>
            {status.message ? (
              <div className="text-sm text-[var(--color-text)]">
                {status.message}
              </div>
            ) : null}
            {status.detail ? (
              <div className="text-xs text-[var(--color-text-muted)] mt-1 font-mono whitespace-pre-wrap">
                {status.detail}
              </div>
            ) : null}
          </section>
        ) : null}

        <section>
          <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
            <div>
              <div className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                {isMaster
                  ? "Offers received by this device"
                  : "Offers sent by this device"}
              </div>
              {activityItems ? (
                <div className="text-xs text-[var(--color-text-muted)] mt-1">
                  {pendingCount} active · {completeCount} complete
                </div>
              ) : null}
            </div>
            <button
              onClick={handleRefresh}
              disabled={!ready || refreshing}
              className="px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--color-surface)]/70"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </div>

          {error ? (
            <div className="text-sm text-[var(--color-accent)]">{error}</div>
          ) : activityItems === null ? (
            <div className="text-sm text-[var(--color-text-muted)] italic">
              Loading…
            </div>
          ) : activityItems.length === 0 ? (
            <div className="rounded-md border border-[var(--color-border)] px-4 py-6 text-sm text-[var(--color-text-muted)]">
              {isMaster
                ? "No satellites have offered tracks yet."
                : "This device has not offered any tracks yet."}
            </div>
          ) : (
            <div className="border border-[var(--color-border)] rounded-md overflow-x-auto">
              <table className={`w-full text-sm ${isMaster ? "min-w-[900px]" : "min-w-[620px]"}`}>
                <thead className="bg-[var(--color-bg-elevated)] text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
                  <tr>
                    <th className="text-left font-normal px-4 py-2">
                      {isMaster ? "Device" : "Offer ID"}
                    </th>
                    <th className="text-left font-normal px-4 py-2">Track</th>
                    <th className="text-left font-normal px-4 py-2">Status</th>
                    {isMaster ? (
                      <>
                        <th className="text-left font-normal px-4 py-2">Promised</th>
                        <th className="text-left font-normal px-4 py-2">Master has</th>
                      </>
                    ) : null}
                    <th className="text-left font-normal px-4 py-2">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {activityItems.map((item) => {
                    const meta = contributionStatusMeta(item.status);
                    return (
                      <tr
                        key={item.id}
                        className="border-b border-[var(--color-border)] last:border-b-0"
                      >
                        <td className="px-4 py-3 text-[var(--color-text-muted)] whitespace-nowrap">
                          {isMaster
                            ? item.device_id || "—"
                            : item.contribution_id
                              ? `#${item.contribution_id}`
                              : "Pending"}
                        </td>
                        <td className="px-4 py-3 max-w-xs">
                          <div className="truncate">
                            {item.artist || "Unknown artist"} —{" "}
                            {item.title || "Unknown title"}
                          </div>
                          {item.album ? (
                            <div className="text-xs text-[var(--color-text-muted)] truncate mt-0.5">
                              {item.album}
                            </div>
                          ) : null}
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap">
                          <span
                            className={`inline-block rounded-full px-2 py-0.5 text-[11px] ${meta.className}`}
                          >
                            {meta.label}
                          </span>
                        </td>
                        {isMaster ? (
                          <>
                            <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] whitespace-nowrap">
                              {audioQualityLabel(item.target_quality)}
                            </td>
                            <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] whitespace-nowrap">
                              {audioQualityLabel(item.acquired_quality)}
                            </td>
                          </>
                        ) : null}
                        <td
                          className="px-4 py-3 text-xs text-[var(--color-text-muted)] whitespace-nowrap"
                          title={item.updated_at ?? undefined}
                        >
                          {relativeTime(item.updated_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
