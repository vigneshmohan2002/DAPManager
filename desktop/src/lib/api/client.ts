import { invoke } from "@tauri-apps/api/core";

import type { BackendRestartResult, BackendStartupResult } from "./types";

export type JsonRecord = Record<string, unknown>;

export function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function readJsonUnknown(response: Response): Promise<unknown> {
  return (await response.json()) as unknown;
}

export async function readJsonRecord(response: Response): Promise<JsonRecord> {
  const value = await readJsonUnknown(response);
  if (!isJsonRecord(value)) {
    throw new TypeError("Expected the API response to be a JSON object");
  }
  return value;
}

export function arrayField<T>(record: JsonRecord, key: string): T[] {
  const value = record[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

export function recordField(record: JsonRecord, key: string): JsonRecord {
  const value = record[key];
  return isJsonRecord(value) ? value : {};
}

let cachedBackend: string | null = null;

let cachedApiToken: string | null = null;

let apiTokenPromise: Promise<string> | null = null;

async function configuredApiToken(): Promise<string> {
  if (cachedApiToken !== null) return cachedApiToken;
  if (!apiTokenPromise) {
    apiTokenPromise = invoke<string>("api_token")
      .then((token) => token.trim())
      .catch(() => "");
  }
  cachedApiToken = await apiTokenPromise;
  apiTokenPromise = null;
  return cachedApiToken;
}

export function invalidateApiToken(): void {
  cachedApiToken = null;
  apiTokenPromise = null;
}

export async function apiFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const token = await configuredApiToken();
  if (!token) return window.fetch(input, init);
  const headers = new Headers(init.headers);
  if (!headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return window.fetch(input, { ...init, headers });
}

export function authenticatedMediaUrl(url: string): string {
  if (!cachedApiToken) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(cachedApiToken)}`;
}

export async function backendUrl(): Promise<string> {
  if (!cachedBackend) cachedBackend = await invoke<string>("backend_url");
  // Populate the synchronous media-URL helper before components render
  // <audio>/<img> elements, which cannot attach Authorization headers.
  await configuredApiToken();
  return cachedBackend;
}

async function backendStartupError(): Promise<string | null> {
  try {
    return await invoke<string | null>("backend_startup_error");
  } catch {
    // Older development shells may not expose the command yet. The health
    // poll remains a valid fallback and will eventually return a timeout.
    return null;
  }
}

export async function waitForBackend(
  deadlineMs = 300_000,
): Promise<BackendStartupResult> {
  const url = await backendUrl();
  const deadline = Date.now() + deadlineMs;
  while (Date.now() < deadline) {
    const startupError = await backendStartupError();
    if (startupError) return { ok: false, error: startupError };
    try {
      const r = await apiFetch(`${url}/api/healthz`);
      if (r.ok) return { ok: true };
    } catch {
      // not up yet
    }
    await new Promise((res) => setTimeout(res, 500));
  }
  const startupError = await backendStartupError();
  return {
    ok: false,
    error:
      startupError ??
      "The Python backend did not become ready within five minutes. Check that Python 3 is installed and that pip can install requirements.txt, then relaunch DAPManager.",
  };
}

export async function restartBackend(): Promise<BackendRestartResult> {
  let result: BackendRestartResult;
  try {
    result = await invoke<BackendRestartResult>("restart_backend");
  } catch (error) {
    return {
      success: false,
      message: `Could not request a backend restart: ${String(error)}`,
      bind_host: "127.0.0.1",
      backend_running: false,
    };
  }

  if (!result.backend_running) return result;
  const ready = await waitForBackend(30_000);
  if (!ready.ok) {
    return {
      ...result,
      success: false,
      backend_running: false,
      message: `${result.message}\n\n${ready.error}`,
    };
  }
  return result;
}
