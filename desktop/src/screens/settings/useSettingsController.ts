import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchConfig,
  restartBackend,
  saveConfig,
  type ConfigPayload,
  type ConfigValue,
} from "../../lib/api";
import { buildConfigPatch } from "../../lib/configDraft";

type ToastVariant = "ok" | "err";
type Notify = (message: string, variant?: ToastVariant) => void;

type SettingsControllerOptions = {
  ready: boolean;
  notify: Notify;
};

export type SettingsController = {
  payload: ConfigPayload | null;
  loadError: string | null;
  saving: boolean;
  dirty: boolean;
  secretKeys: ReadonlySet<string>;
  booleanKeys: ReadonlySet<string>;
  effectiveValue: (key: string) => ConfigValue;
  changeValue: (key: string, value: ConfigValue) => void;
  load: () => Promise<void>;
  save: () => Promise<void>;
};

function savedMessage(changed: readonly string[], restartMessage: string): string {
  if (changed.length === 0) return "No changes.";
  const suffix = changed.length === 1 ? "" : "s";
  return `Saved ${changed.length} change${suffix}: ${changed.join(", ")}.${restartMessage}`;
}

export function useSettingsController({
  ready,
  notify,
}: SettingsControllerOptions): SettingsController {
  const [payload, setPayload] = useState<ConfigPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, ConfigValue>>({});
  const [saving, setSaving] = useState(false);
  const [restartOnly, setRestartOnly] = useState(false);

  const load = useCallback(async () => {
    if (!ready) return;
    setLoadError(null);
    try {
      const nextPayload = await fetchConfig();
      setPayload(nextPayload);
      setDraft({});
    } catch (error) {
      setLoadError(String(error));
    }
  }, [ready]);

  useEffect(() => {
    void load();
  }, [load]);

  const secretKeys = useMemo(
    () => new Set(payload?.secret_keys ?? []),
    [payload?.secret_keys],
  );
  const booleanKeys = useMemo(
    () => new Set(payload?.bool_keys ?? []),
    [payload?.bool_keys],
  );

  const effectiveValue = useCallback(
    (key: string): ConfigValue => {
      if (key in draft) return draft[key];
      return payload?.config[key] ?? (booleanKeys.has(key) ? false : "");
    },
    [booleanKeys, draft, payload],
  );

  const changeValue = useCallback((key: string, value: ConfigValue) => {
    setDraft((current) => ({ ...current, [key]: value }));
  }, []);

  const save = useCallback(async () => {
    if (!payload) return;
    setSaving(true);
    const patch = buildConfigPatch(draft, payload.config, booleanKeys);
    try {
      if (restartOnly) {
        const recovered = await restartBackend();
        if (!recovered.success) {
          setRestartOnly(!recovered.backend_running);
          notify(recovered.message, "err");
          return;
        }
        setRestartOnly(false);
      }

      const result = await saveConfig(patch);
      if (!result.success) {
        notify(result.message || "Save failed", "err");
        return;
      }

      const needsRestart = result.changed.some(
        (key) => key === "device_role" || key === "api_token",
      );
      let restartMessage = "";
      if (needsRestart) {
        const restarted = await restartBackend();
        if (!restarted.success) {
          setRestartOnly(!restarted.backend_running);
          notify(`Settings saved, but ${restarted.message}`, "err");
          if (restarted.backend_running) await load();
          return;
        }
        setRestartOnly(false);
        restartMessage = ` ${restarted.message}`;
      }

      notify(savedMessage(result.changed, restartMessage));
      await load();
    } catch (error) {
      notify(`Settings could not be saved: ${String(error)}`, "err");
    } finally {
      setSaving(false);
    }
  }, [booleanKeys, draft, load, notify, payload, restartOnly]);

  return {
    payload,
    loadError,
    saving,
    dirty: Object.keys(draft).length > 0,
    secretKeys,
    booleanKeys,
    effectiveValue,
    changeValue,
    load,
    save,
  };
}
