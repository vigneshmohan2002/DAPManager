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
      jsonResponse({ running: 0, message: "Master idle" }),
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
    await expect(api.fetchStatus("downloads")).resolves.toEqual({
      running: false,
      task: null,
      message: "Master idle",
      detail: null,
    });
    expect(vi.mocked(globalThis.fetch)).toHaveBeenLastCalledWith(
      "http://localhost:5001/api/status?scope=downloads",
      {},
    );
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

  it("retains server-supported smart rules that the editor does not expose", async () => {
    const api = await loadApiWithResponses(
      jsonResponse({
        playlists: [
          {
            playlist_id: "web-smart-list",
            name: "Web smart list",
            track_count: 4,
            updated_at: "2026-07-15 10:00:00",
            smart_rules: {
              match: "all",
              rules: [
                { field: "genre", op: "contains", value: "jazz" },
                { field: "is_liked", op: "equals", value: true },
              ],
            },
          },
        ],
      }),
    );

    await expect(api.fetchPlaylists()).resolves.toEqual([
      {
        playlist_id: "web-smart-list",
        name: "Web smart list",
        track_count: 4,
        updated_at: "2026-07-15 10:00:00",
        smart_rules: {
          match: "all",
          rules: [
            { field: "genre", op: "contains", value: "jazz" },
            { field: "is_liked", op: "equals", value: true },
          ],
        },
      },
    ]);
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

  it("validates MusicBrainz release candidates and persistent album progress", async () => {
    const releaseMbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36";
    const request = {
      id: 12,
      release_mbid: releaseMbid,
      title: "Album",
      artist: "Artist",
      track_count: 10,
      stage: "downloading",
      detail: "Searching lossless sources",
      completed_tracks: 2,
      queue_status: "pending",
      last_attempt: null,
      created_at: "2026-07-18 10:00:00",
      updated_at: "2026-07-18 10:01:00",
      cover_url: "https://coverartarchive.org/front.jpg",
    };
    const api = await loadApiWithResponses(
      jsonResponse({
        success: true,
        query: "Artist - Album",
        ambiguous: true,
        candidates: [
          {
            release_mbid: releaseMbid,
            title: "Album",
            artist: "Artist",
            track_count: 10,
            format: "CD",
            label: "Label",
            score: 99,
          },
          {
            release_mbid: "not-an-mbid",
            title: "Unsafe",
            artist: "Artist",
            track_count: 10,
          },
        ],
      }),
      jsonResponse({ success: true, queued: true, message: "queued", request }),
      jsonResponse({ success: true, requests: [request, { ...request, stage: "bogus" }] }),
      jsonResponse({ success: true, request: { ...request, stage: "importing" } }),
    );

    await expect(api.searchAlbumReleases("Artist - Album")).resolves.toEqual({
      query: "Artist - Album",
      ambiguous: true,
      candidates: [
        {
          release_mbid: releaseMbid,
          title: "Album",
          artist: "Artist",
          track_count: 10,
          date: "",
          country: "",
          status: "",
          disambiguation: "",
          primary_type: "",
          format: "CD",
          label: "Label",
          catalog_number: "",
          barcode: "",
          cover_url: "",
          musicbrainz_url: "",
          score: 99,
        },
      ],
    });
    await expect(api.requestAlbumDownload(releaseMbid)).resolves.toMatchObject({
      success: true,
      queued: true,
      request: { id: 12, stage: "downloading", completed_tracks: 2 },
    });
    await expect(api.fetchAlbumDownloadRequests()).resolves.toHaveLength(1);
    await expect(api.fetchAlbumDownloadRequest(12)).resolves.toMatchObject({
      id: 12,
      stage: "importing",
    });
  });

  it("surfaces the master message when an album request is rejected", async () => {
    const api = await loadApiWithResponses(
      jsonResponse(
        { success: false, message: "The selected release is not an album" },
        400,
      ),
    );

    await expect(
      api.requestAlbumDownload("95fb59ed-1ece-419b-b62f-aef31e0ebf36"),
    ).resolves.toEqual({
      success: false,
      queued: false,
      message: "The selected release is not an album",
    });
  });

  it("queues wanted releases with an explicit album identity marker", async () => {
    const releaseMbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36";
    const api = await loadApiWithResponses(
      jsonResponse({ success: true, message: "queued" }),
    );

    await expect(api.queueWantedRelease({
      mbid: releaseMbid,
      artist: "Boards of Canada",
      title: "Geogaddi",
    })).resolves.toEqual({ success: true, message: "queued" });

    const [, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      search_query: "::ALBUM:: Boards of Canada - Geogaddi",
      mbid_guess: releaseMbid,
    });
  });

  it("decodes additive download retry state while accepting older queue rows", async () => {
    const retryState = {
      id: 2,
      query: "Artist - Exact Album",
      status: "failed",
      last_attempt: "2026-07-22T20:00:00+00:00",
      attempt_count: 2,
      max_attempts: 3,
      next_attempt_at: "2026-07-22T20:05:00+00:00",
      is_paused: false,
      is_quarantined: true,
      last_error: "Exact release remains incomplete",
    };
    const api = await loadApiWithResponses(
      jsonResponse({
        success: true,
        items: [
          {
            id: 1,
            query: "Legacy row",
            status: "failed",
            last_attempt: null,
          },
          retryState,
          { ...retryState, id: 3, is_quarantined: 1 },
        ],
      }),
    );

    await expect(api.fetchDownloads()).resolves.toEqual([
      {
        id: 1,
        query: "Legacy row",
        status: "failed",
        last_attempt: null,
      },
      retryState,
    ]);
  });
});
