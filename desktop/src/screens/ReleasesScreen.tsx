import { useEffect, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchWantedReleases,
  queueWantedRelease,
  type WantedRelease,
  type WantedReleasesResult,
} from "../lib/api";
import { relativeTime } from "../lib/time";

type Props = {
  ready: boolean;
  // Reuses Stage 7a's focus-key router so the disabled state can land
  // the user on the relevant Settings field instead of just telling
  // them what's wrong.
  onOpenSettings: (focusKey?: string) => void;
};

export default function ReleasesScreen({ ready, onOpenSettings }: Props) {
  const [data, setData] = useState<WantedReleasesResult | null>(null);
  const [pendingMbid, setPendingMbid] = useState<string | null>(null);
  const toast = useToast();

  const load = async () => {
    setData(await fetchWantedReleases());
  };

  useEffect(() => {
    if (ready) load();
  }, [ready]);

  const onQueue = async (rel: WantedRelease) => {
    setPendingMbid(rel.mbid);
    const result = await queueWantedRelease(rel);
    setPendingMbid(null);
    if (!result.success) {
      toast.show(result.message || "Queue failed", "err");
      return;
    }
    toast.show(result.message || "Queued.", "ok");
    // Re-fetch so the pill flips from Wanted → Queued without
    // waiting for the next watcher tick.
    load();
  };

  let body: React.ReactNode;
  let subtitle = "";

  if (data === null) {
    subtitle = "Loading…";
    body = <Loading />;
  } else if (data.kind === "disabled") {
    subtitle = "Lidarr not configured";
    body = (
      <DisabledState
        title="Lidarr is not enabled"
        description="The release watcher polls Lidarr's wanted/missing list. Enable lidarr_watch_enabled in Settings and set lidarr_url + lidarr_api_key."
        cta="Configure Lidarr"
        onCta={() => onOpenSettings("lidarr_watch_enabled")}
      />
    );
  } else if (data.kind === "unavailable") {
    subtitle = "Lidarr unreachable";
    body = (
      <DisabledState
        title="Lidarr is configured but unavailable"
        description="Couldn't reach Lidarr at the configured URL. Check that it's running and that lidarr_url + lidarr_api_key are correct."
        cta="Open Settings"
        onCta={() => onOpenSettings("lidarr_url")}
      />
    );
  } else if (data.kind === "error") {
    subtitle = "Lidarr error";
    body = (
      <div className="text-sm text-[var(--color-accent)]">{data.message}</div>
    );
  } else {
    const wanted = data.items.filter((r) => !r.queued && !r.downloaded).length;
    subtitle = `${data.items.length} albums · ${wanted} wanted`;
    body =
      data.items.length === 0 ? (
        <div className="text-sm text-[var(--color-text-muted)] italic">
          Lidarr has nothing wanted right now — your library is caught up.
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
          {data.items.map((rel) => (
            <ReleaseCard
              key={rel.mbid}
              rel={rel}
              busy={pendingMbid === rel.mbid}
              onQueue={() => onQueue(rel)}
            />
          ))}
        </div>
      );
  }

  const lastTick =
    data && data.kind === "ok"
      ? `Last poll: ${relativeTime(data.last_tick)}`
      : "";

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="New Releases" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="flex items-center gap-3 mb-4">
          <button
            onClick={load}
            disabled={!ready || data === null}
            className="px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--color-surface)]/70"
          >
            Refresh
          </button>
          {lastTick ? (
            <span className="text-xs text-[var(--color-text-muted)]">
              {lastTick}
            </span>
          ) : null}
        </div>
        {body}
      </div>
    </div>
  );
}

function Loading() {
  return (
    <div className="text-sm text-[var(--color-text-muted)] italic">Loading…</div>
  );
}

function DisabledState({
  title,
  description,
  cta,
  onCta,
}: {
  title: string;
  description: string;
  cta: string;
  onCta: () => void;
}) {
  return (
    <div className="max-w-xl rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]/40 p-6 space-y-3">
      <div className="text-sm font-semibold text-[var(--color-text)]">
        {title}
      </div>
      <div className="text-sm text-[var(--color-text-muted)]">
        {description}
      </div>
      <button
        onClick={onCta}
        className="px-3 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold hover:brightness-110"
      >
        {cta}
      </button>
    </div>
  );
}

function ReleaseCard({
  rel,
  busy,
  onQueue,
}: {
  rel: WantedRelease;
  busy: boolean;
  onQueue: () => void;
}) {
  const [imgFailed, setImgFailed] = useState(false);
  // Three mutually-exclusive states:
  //   downloaded > queued > wanted (plain).
  // downloaded wins because once a row exists in tracks, the queued
  // bool is just historical noise.
  const pill = rel.downloaded
    ? { label: "In library", className: "bg-emerald-500/20 text-emerald-300" }
    : rel.queued
    ? { label: "Queued", className: "bg-amber-500/20 text-amber-300" }
    : { label: "Wanted", className: "bg-[var(--color-accent)]/20 text-[var(--color-accent)]" };

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]/40 overflow-hidden flex flex-col">
      <div className="aspect-square bg-[var(--color-bg)] relative">
        {!imgFailed && rel.cover_url ? (
          <img
            src={rel.cover_url}
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
        <div
          className={`absolute top-2 right-2 px-1.5 py-0.5 rounded text-[11px] font-semibold ${pill.className}`}
        >
          {pill.label}
        </div>
      </div>
      <div className="p-3 flex-1 flex flex-col gap-0.5 min-w-0">
        <div
          className="text-sm text-[var(--color-text)] truncate"
          title={rel.title}
        >
          {rel.title}
        </div>
        <div
          className="text-xs text-[var(--color-text-muted)] truncate"
          title={rel.artist}
        >
          {rel.artist}
        </div>
        {rel.release_date ? (
          <div className="text-xs text-[var(--color-text-muted)] mt-1">
            {rel.release_date.slice(0, 10)}
          </div>
        ) : null}
        {!rel.downloaded && !rel.queued ? (
          <button
            onClick={onQueue}
            disabled={busy}
            className="mt-2 px-2 py-1 text-xs rounded bg-[var(--color-accent)] text-[var(--color-bg)] font-semibold disabled:opacity-50 hover:brightness-110"
          >
            {busy ? "Queueing…" : "Queue Now"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
