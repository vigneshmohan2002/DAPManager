import { useEffect, useRef, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchConfig,
  fetchStatus,
  regenerateDailyMixes,
  saveConfig,
  startTagBackfill,
  type BackendStatus,
  type ConfigPayload,
  type ConfigValue,
} from "../lib/api";

type Props = {
  ready: boolean;
  // When set, scroll the row owning this config key into view + flash
  // it. Used by "Identify & Tag" when acoustid_api_key is missing:
  // the caller routes to Settings and passes the key.
  focusKey?: string | null;
  onConsumedFocusKey?: () => void;
};

// Coerce a form string back to the type the config currently stores.
// If the value we loaded was a number, submit as a number so the
// backend's "!=" check doesn't see a pure type flip as a change.
// Leaves strings alone; booleans are handled separately as checkboxes.
function coerceOnSave(
  input: string,
  original: ConfigValue | undefined,
): ConfigValue {
  if (typeof original === "number" && input.trim() !== "") {
    const n = Number(input);
    if (!isNaN(n)) return n;
  }
  return input;
}

export default function SettingsScreen({
  ready,
  focusKey,
  onConsumedFocusKey,
}: Props) {
  const [payload, setPayload] = useState<ConfigPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, ConfigValue>>({});
  const [saving, setSaving] = useState(false);
  const toast = useToast();
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [flashKey, setFlashKey] = useState<string | null>(null);

  const load = async () => {
    if (!ready) return;
    setLoadError(null);
    try {
      const p = await fetchConfig();
      setPayload(p);
      setDraft({});
    } catch (e) {
      setLoadError(String(e));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // Scroll + flash when the caller hands us a focusKey. Runs after
  // the payload lands so the target row actually exists.
  useEffect(() => {
    if (!focusKey || !payload) return;
    const el = rowRefs.current[focusKey];
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setFlashKey(focusKey);
      const t = window.setTimeout(() => setFlashKey(null), 2000);
      onConsumedFocusKey?.();
      return () => window.clearTimeout(t);
    }
  }, [focusKey, payload, onConsumedFocusKey]);

  const secretSet = new Set(payload?.secret_keys ?? []);
  const boolSet = new Set(payload?.bool_keys ?? []);

  const effective = (key: string): ConfigValue => {
    if (key in draft) return draft[key];
    return payload?.config[key] ?? (boolSet.has(key) ? false : "");
  };

  const onChange = (key: string, value: ConfigValue) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  const dirty = Object.keys(draft).length > 0;

  const handleSave = async () => {
    if (!payload) return;
    setSaving(true);
    // Build patch: coerce numeric-typed values back to numbers; leave
    // blanks in secret fields as '' so the backend treats them as
    // "don't change".
    const patch: Record<string, ConfigValue> = {};
    for (const [key, raw] of Object.entries(draft)) {
      if (boolSet.has(key) || typeof raw !== "string") {
        patch[key] = raw;
      } else {
        patch[key] = coerceOnSave(raw, payload.config[key]);
      }
    }
    try {
      const result = await saveConfig(patch);
      if (!result.success) {
        toast.show(result.message || "Save failed", "err");
        return;
      }
      const n = result.changed.length;
      toast.show(
        n === 0
          ? "No changes."
          : `Saved ${n} change${n === 1 ? "" : "s"}: ${result.changed.join(", ")}.`,
      );
      await load();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Settings"
        subtitle={
          !ready
            ? "Loading…"
            : loadError
              ? "Failed to load"
              : dirty
                ? "Unsaved changes"
                : "config.json"
        }
      />
      <div className="flex-1 overflow-y-auto px-8 py-6">
        {loadError ? (
          <div className="text-sm text-[var(--color-accent)] mb-4">
            {loadError}
          </div>
        ) : null}

        {!payload ? (
          <div className="text-sm text-[var(--color-text-muted)]">Loading…</div>
        ) : (
          <div className="max-w-3xl space-y-6">
            <LibraryTools ready={ready} />
            {payload.groups.map((group) => (
              <fieldset
                key={group.label}
                className="border border-[var(--color-border)] rounded-md px-4 pt-3 pb-4"
              >
                <legend className="px-2 text-xs uppercase tracking-wider text-[var(--color-text-muted)]">
                  {group.label}
                </legend>
                <div className="space-y-2 mt-2">
                  {group.keys.map((key) => {
                    const value = effective(key);
                    const isSecret = secretSet.has(key);
                    const isBool = boolSet.has(key);
                    const flashed = flashKey === key;
                    return (
                      <div
                        key={key}
                        ref={(el) => {
                          rowRefs.current[key] = el;
                        }}
                        className={`flex items-center gap-3 px-2 py-1.5 rounded-md transition-colors ${
                          flashed
                            ? "bg-[var(--color-accent)]/20 ring-1 ring-[var(--color-accent)]"
                            : ""
                        }`}
                      >
                        <label
                          htmlFor={`cfg-${key}`}
                          className="w-56 shrink-0 text-xs font-mono text-[var(--color-text-muted)] truncate"
                          title={key}
                        >
                          {key}
                        </label>
                        {isBool ? (
                          <input
                            id={`cfg-${key}`}
                            type="checkbox"
                            checked={Boolean(value)}
                            onChange={(e) => onChange(key, e.target.checked)}
                          />
                        ) : (
                          <input
                            id={`cfg-${key}`}
                            type={isSecret ? "password" : "text"}
                            value={value == null ? "" : String(value)}
                            onChange={(e) => onChange(key, e.target.value)}
                            placeholder={
                              isSecret ? "(leave blank to keep current)" : ""
                            }
                            className="flex-1 bg-[var(--color-surface)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] rounded-md px-3 py-1.5 outline-none focus:ring-1 focus:ring-[var(--color-accent)] border border-[var(--color-border)]"
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </fieldset>
            ))}
            <div className="flex items-center gap-3 pt-2">
              <button
                onClick={handleSave}
                disabled={!dirty || saving}
                className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                onClick={load}
                disabled={saving}
                className="px-4 py-2 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 hover:bg-[var(--color-surface)]/70"
              >
                Reload
              </button>
              <span className="text-xs text-[var(--color-text-muted)]">
                Secret fields (passwords / API keys) are masked — leave blank
                to keep the current value.
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// --- Library tools --------------------------------------------------------

// Polls /api/status while a job is running so the button shows the
// task's progress detail without forcing the user to re-click. The
// poll stops as soon as the job goes idle to avoid burning the
// network for the rest of the session.
function LibraryTools({ ready }: { ready: boolean }) {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [kicking, setKicking] = useState(false);
  const toast = useToast();

  // Backfill is the only TaskManager-driven job this card cares
  // about — narrow the running indicator to that task name so a
  // Library Scan running in another tab doesn't paint this row
  // "in progress".
  const backfilling = Boolean(
    status?.running && status.task === "Genre tag backfill",
  );

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      try {
        const s = await fetchStatus();
        if (cancelled) return;
        setStatus(s);
        // Fast poll while running, slow heartbeat while idle — the
        // user is only on this screen briefly so 5s idle is fine.
        timer = setTimeout(tick, s.running ? 1500 : 5000);
      } catch {
        if (!cancelled) timer = setTimeout(tick, 5000);
      }
    };
    tick();
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
      } else {
        toast.show("Tag backfill started");
        // Force an immediate poll so the button flips to "Running"
        // without waiting for the next 5s idle tick.
        fetchStatus().then(setStatus).catch(() => {});
      }
    } finally {
      setKicking(false);
    }
  };

  const [regenerating, setRegenerating] = useState(false);

  const handleRegenMixes = async () => {
    setRegenerating(true);
    try {
      const result = await regenerateDailyMixes();
      if (!result.success) {
        toast.show(result.message ?? "Couldn't regenerate", "err");
        return;
      }
      if (result.mixes === 0) {
        const why =
          result.reason === "cold_start"
            ? "Play more tracks first — we need more listening history."
            : result.reason === "no_tags"
              ? "Run the genre tag backfill first."
              : "No mixes generated.";
        toast.show(why);
      } else {
        toast.show(`Regenerated ${result.mixes} Daily Mix${result.mixes === 1 ? "" : "es"}.`);
      }
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
            Walks your library and asks MusicBrainz for each artist's
            top tags. Powers genre filters on smart playlists and the
            Artist Radio feature. Rate-limited to ~1 request/sec, so a
            500-artist library takes about 15 minutes. Incremental —
            re-running skips artists already tagged in the last 30 days.
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
            Clusters your top artists into 4–6 themed mixes shown on
            Home. Needs at least eight artists with three or more
            plays in the last 90 days, plus a finished tag backfill.
            Pure-SQL — runs in milliseconds.
          </p>
        </div>
        <button
          onClick={handleRegenMixes}
          disabled={!ready || regenerating}
          className="shrink-0 px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm border border-[var(--color-border)] hover:bg-[var(--color-surface)]/70 disabled:opacity-50"
        >
          {regenerating ? "…" : "Regenerate"}
        </button>
      </div>
    </fieldset>
  );
}
