import { afterEach, describe, expect, it, vi } from "vitest";

type ApiModule = typeof import("./api");

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function loadApiWithResponses(
  ...responses: Response[]
): Promise<ApiModule> {
  vi.resetModules();
  const invoke = vi.fn(async (command: string) => {
    if (command === "api_token") return "";
    if (command === "backend_url") return "http://localhost:5001";
    throw new Error(`Unexpected command: ${command}`);
  });
  const fetch = vi.fn();
  for (const response of responses) fetch.mockResolvedValueOnce(response);
  vi.doMock("@tauri-apps/api/core", () => ({ invoke }));
  vi.stubGlobal("fetch", fetch);
  return import("./api");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("desktop API runtime decoding", () => {
  it("filters malformed collection rows instead of trusting a generic cast", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({
        albums: [
          { id: "release-1", title: "Album", artist: "Artist", track_count: 2 },
          { id: "release-2", title: "Missing count", artist: "Artist" },
          "not an album",
        ],
      }),
    );

    await expect(api.fetchAlbums()).resolves.toEqual([
      { id: "release-1", title: "Album", artist: "Artist", track_count: 2 },
    ]);
  });

  it("normalizes sync and backend status records without assertions", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({
        success: true,
        state: {
          catalog_pull: "2026-07-15 10:00:00",
          playlist_pull: 123,
          playlist_push: null,
        },
      }),
      jsonResponse({ running: 1, message: "Busy" }),
    );

    await expect(api.fetchSyncState()).resolves.toEqual({
      catalog_pull: "2026-07-15 10:00:00",
      playlist_pull: null,
      playlist_push: null,
      inventory_report: null,
    });
    await expect(api.fetchStatus()).resolves.toEqual({
      running: true,
      task: null,
      message: "Busy",
      detail: null,
    });
  });

  it("keeps create and queue failures as typed results on non-2xx responses", async () => {
    const api = await loadApiWithResponses(
      jsonResponse(
        { success: false, message: "name is required", playlist_id: 42 },
        400,
      ),
      jsonResponse(
        {
          success: false,
          message: "bad mbids",
          queued: "2",
          skipped_linked: 1,
          skipped_queued: null,
          not_found: 3,
        },
        400,
      ),
    );

    await expect(api.createPlaylist("")).resolves.toEqual({
      success: false,
      message: "name is required",
      playlist_id: undefined,
      name: undefined,
    });
    await expect(api.queueCatalogDownload(["bad"])).resolves.toEqual({
      success: false,
      message: "bad mbids",
      queued: 2,
      skipped_linked: 1,
      skipped_queued: 0,
      not_found: 3,
    });
  });

  it("narrows public URL detection and falls back to none", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({ source: "tailscale", url: "http://home-pc:5001" }),
      jsonResponse({ source: "unexpected", url: 5001 }),
    );

    await expect(api.detectPublicUrl()).resolves.toEqual({
      source: "tailscale",
      url: "http://home-pc:5001",
    });
    await expect(api.detectPublicUrl()).resolves.toEqual({ source: "none" });
  });

  it("returns null for malformed artist information", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({
        success: true,
        info: {
          summary: "Biography",
          source_url: 123,
          image_url: null,
          title: "Artist",
        },
      }),
    );

    await expect(api.fetchArtistInfo("Artist")).resolves.toBeNull();
  });

  it("decodes tag metadata and reports malformed candidates as results", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({
        success: true,
        candidate: null,
        current: { artist: "Artist", title: "Track", track_number: 7 },
      }),
      jsonResponse({
        success: true,
        candidate: { score: "high", tier: "green", meta: {}, current: {} },
      }),
    );

    await expect(api.identifyTrack("track-1")).resolves.toEqual({
      kind: "no_match",
      current: { artist: "Artist", title: "Track" },
    });
    await expect(api.identifyTrack("track-2")).resolves.toEqual({
      kind: "error",
      message: "identify returned an invalid candidate",
    });
  });

  it("decodes Wrapped payloads without changing their result contract", async () => {
    const payload = {
      success: true,
      year: 2026,
      total_plays: 12,
      total_listening_time_ms: 34_000,
      has_legacy_rows: false,
      top_track: null,
      top_artist: null,
      top_album: null,
      busiest_day: { date: "2026-07-15", plays: 4 },
      top_hour: 10,
      first_play: null,
      longest_streak_days: 3,
    };
    const api = await loadApiWithResponses(
      jsonResponse(payload),
      jsonResponse({ ...payload, longest_streak_days: "three" }),
    );

    const expected = {
      year: 2026,
      total_plays: 12,
      total_listening_time_ms: 34_000,
      has_legacy_rows: false,
      top_track: null,
      top_artist: null,
      top_album: null,
      busiest_day: { date: "2026-07-15", plays: 4 },
      top_hour: 10,
      first_play: null,
      longest_streak_days: 3,
    };
    await expect(api.fetchWrapped(2026)).resolves.toEqual(expected);
    await expect(api.fetchWrapped(2026)).resolves.toEqual({
      ...expected,
      longest_streak_days: 0,
    });
  });
});
