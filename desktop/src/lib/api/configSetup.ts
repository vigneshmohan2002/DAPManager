import {
  apiFetch,
  arrayField,
  backendUrl,
  invalidateApiToken,
  readJsonRecord,
  recordField,
} from "./client";
import type {
  ConfigGroup,
  ConfigValue,
  ConfigPayload,
  SaveConfigResult,
  SuggestionItem,
  SuggestionResult,
  PublicUrlDetection,
  SetupPayload,
} from "./types";

export async function fetchConfig(): Promise<ConfigPayload> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/config`);
  if (!r.ok) throw new Error(`config: ${r.status}`);
  const data = await readJsonRecord(r);
  if (!data.success) throw new Error(String(data.message ?? "config failed"));
  return {
    config: recordField(data, "config") as Record<string, ConfigValue>,
    editable_keys: arrayField<string>(data, "editable_keys"),
    secret_keys: arrayField<string>(data, "secret_keys"),
    bool_keys: arrayField<string>(data, "bool_keys"),
    groups: arrayField<ConfigGroup>(data, "groups"),
  };
}

export async function saveConfig(
  patch: Record<string, ConfigValue>,
): Promise<SaveConfigResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  const data = await readJsonRecord(r);
  if (data.success && Object.prototype.hasOwnProperty.call(patch, "api_token")) {
    invalidateApiToken();
  }
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    changed: arrayField<string>(data, "changed"),
  };
}

export const SUGGESTION_HOST_KEY = "master_url";

export function suggestionHostFromConfig(
  config: Record<string, ConfigValue>,
): string | null {
  const canonical = config[SUGGESTION_HOST_KEY];
  const legacy = config.dap_manager_host_url;
  const raw =
    typeof canonical === "string" && canonical.trim()
      ? canonical
      : typeof legacy === "string"
        ? legacy
        : "";
  return raw.trim() || null;
}

export function parseManualSuggestions(text: string): SuggestionItem[] {
  const items: SuggestionItem[] = [];
  for (const raw of (text ?? "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const dash = line.indexOf(" - ");
    if (dash > 0) {
      const artist = line.slice(0, dash).trim();
      const title = line.slice(dash + 3).trim();
      if (artist && title) {
        items.push({ artist, title });
        continue;
      }
    }
    items.push({ search_query: line });
  }
  return items;
}

export async function fetchSetupStatus(): Promise<{ needs_setup: boolean }> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/setup/status`);
  if (!r.ok) throw new Error(`setup/status: ${r.status}`);
  return (await readJsonRecord(r)) as { needs_setup: boolean };
}

export async function detectPublicUrl(): Promise<PublicUrlDetection> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/setup/detect-public-url`);
  if (!r.ok) return { source: "none" };
  return (await readJsonRecord(r)) as PublicUrlDetection;
}

export async function validatePath(
  path: string,
  kind: "directory" | "file" = "directory",
): Promise<{ ok: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/setup/validate-path`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, kind }),
  });
  if (!r.ok) return { ok: false, message: `${r.status}` };
  return (await readJsonRecord(r)) as { ok: boolean; message?: string };
}

export async function saveSetupConfig(
  payload: SetupPayload,
): Promise<{ success: boolean; message?: string }> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/save_config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await readJsonRecord(r);
  if (data.success) invalidateApiToken();
  return {
    success: Boolean(data.success),
    message: typeof data.message === "string" ? data.message : undefined,
  };
}

export async function fetchSatelliteBundleLink(): Promise<{
  url: string;
  expires_at: number | null;
}> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/satellite-bundle-link`);
  const data = await readJsonRecord(r);
  if (!r.ok || !data.success) {
    throw new Error(String(data.message ?? `bundle link: ${r.status}`));
  }
  return {
    url: String(data.url),
    expires_at: data.expires_at == null ? null : Number(data.expires_at),
  };
}

export async function postSuggestions(
  items: SuggestionItem[],
): Promise<SuggestionResult> {
  const url = await backendUrl();
  const r = await apiFetch(`${url}/api/suggestions/forward`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!r.ok) {
    return {
      success: false,
      message: `${r.status} ${r.statusText}`,
      received: 0,
      queued: 0,
      skipped: 0,
    };
  }
  const data = await readJsonRecord(r);
  return {
    success: Boolean(data.success),
    message: String(data.message ?? ""),
    received: Number(data.received ?? 0),
    queued: Number(data.queued ?? 0),
    skipped: Number(data.skipped ?? 0),
  };
}
