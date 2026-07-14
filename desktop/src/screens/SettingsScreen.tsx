import { useEffect, useRef, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import LibraryTools from "./settings/LibraryTools";
import { useSettingsController } from "./settings/useSettingsController";

type Props = {
  ready: boolean;
  // When set, scroll the row owning this config key into view + flash
  // it. Used by "Identify & Tag" when acoustid_api_key is missing:
  // the caller routes to Settings and passes the key.
  focusKey?: string | null;
  onConsumedFocusKey?: () => void;
};

export default function SettingsScreen({
  ready,
  focusKey,
  onConsumedFocusKey,
}: Props) {
  const toast = useToast();
  const {
    payload,
    loadError,
    saving,
    dirty,
    secretKeys,
    booleanKeys,
    effectiveValue,
    changeValue,
    load,
    save,
  } = useSettingsController({ ready, notify: toast.show });
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [flashKey, setFlashKey] = useState<string | null>(null);

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
            <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg-elevated)] px-4 py-3 text-xs text-[var(--color-text-muted)]">
              Desktop networking is fail-closed: only a <code>master</code>
              {" "}with a non-empty <code>api_token</code> listens on
              LAN/Tailscale. Satellite, standalone, and tokenless Master roles
              stay on 127.0.0.1. Saving either field restarts the owned backend
              automatically.
            </div>
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
                    const value = effectiveValue(key);
                    const isSecret = secretKeys.has(key);
                    const isBool = booleanKeys.has(key);
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
                            onChange={(e) => changeValue(key, e.target.checked)}
                          />
                        ) : (
                          <input
                            id={`cfg-${key}`}
                            type={isSecret ? "password" : "text"}
                            value={value == null ? "" : String(value)}
                            onChange={(e) => changeValue(key, e.target.value)}
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
                onClick={save}
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
