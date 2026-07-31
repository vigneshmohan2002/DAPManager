import { afterEach, describe, expect, it, vi } from "vitest";

type ApiModule = typeof import("./api");

const EXPECTED_RUNTIME_EXPORTS = [
  "SUGGESTION_HOST_KEY",
  "addTrackToPlaylist",
  "albumCoverUrl",
  "apiFetch",
  "applyTrackTags",
  "backendUrl",
  "clearCompletedDownloads",
  "contributeAllLocalTracks",
  "contributeTrack",
  "createPlaylist",
  "deleteDownload",
  "deleteDownloadResidue",
  "deletePlaylist",
  "deleteTrackFile",
  "detectPublicUrl",
  "fetchAlbumTracks",
  "fetchAlbumDownloadRequest",
  "fetchAlbumDownloadRequests",
  "fetchAlbums",
  "fetchAllTracks",
  "fetchArtistInfo",
  "fetchArtistRadio",
  "fetchArtists",
  "fetchAuditResults",
  "fetchConfig",
  "fetchContributions",
  "fetchDownloads",
  "fetchDuplicates",
  "fetchFleetSummary",
  "fetchHome",
  "fetchLyrics",
  "fetchOrphanPlaylists",
  "fetchOrphanTracks",
  "fetchOutgoingContributions",
  "fetchPlayStats",
  "fetchPlaylists",
  "fetchSatelliteBundleLink",
  "fetchSetupStatus",
  "fetchStatus",
  "fetchSyncState",
  "fetchWantedReleases",
  "fetchWrapped",
  "identifyTrack",
  "parseManualSuggestions",
  "postAction",
  "postSuggestions",
  "purgePlaylist",
  "purgeTrack",
  "queueCatalogDownload",
  "queueWantedRelease",
  "requestAlbumDownload",
  "recordPlay",
  "regenerateDailyMixes",
  "renamePlaylist",
  "resolveDuplicate",
  "restartBackend",
  "restorePlaylist",
  "restoreTrack",
  "retryDownload",
  "runCompleteAlbums",
  "saveConfig",
  "saveLyrics",
  "saveSetupConfig",
  "searchFleet",
  "searchAlbumReleases",
  "searchTracks",
  "setTrackLiked",
  "softDeleteTrack",
  "startTagBackfill",
  "streamUrl",
  "suggestionHostFromConfig",
  "updatePlaylistSmartRules",
  "validatePath",
  "waitForBackend",
] as const;

async function loadApi(options: {
  token?: string;
  backend?: string;
  restartResult?: import("./api").BackendRestartResult;
} = {}): Promise<{ api: ApiModule; invoke: ReturnType<typeof vi.fn> }> {
  vi.resetModules();
  const invoke = vi.fn(async (command: string) => {
    if (command === "api_token") return options.token ?? "";
    if (command === "backend_url") return options.backend ?? "http://localhost:5001";
    if (command === "backend_startup_error") return null;
    if (command === "restart_backend") {
      return (
        options.restartResult ?? {
          success: false,
          message: "not running",
          bind_host: "127.0.0.1",
          backend_running: false,
        }
      );
    }
    throw new Error(`Unexpected command: ${command}`);
  });
  vi.doMock("@tauri-apps/api/core", () => ({ invoke }));
  return { api: await import("./api"), invoke };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("desktop API compatibility surface", () => {
  it("keeps exactly the established runtime exports available", async () => {
    const { api } = await loadApi();

    expect(Object.keys(api).sort()).toEqual([...EXPECTED_RUNTIME_EXPORTS].sort());
    for (const name of EXPECTED_RUNTIME_EXPORTS) {
      if (name === "SUGGESTION_HOST_KEY") continue;
      expect(api[name]).toEqual(expect.any(Function));
    }
  });
});

describe("apiFetch authentication", () => {
  it("adds the configured bearer token while preserving caller headers", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const { api, invoke } = await loadApi({ token: "  satellite-secret  " });

    await api.apiFetch("http://master/api/library", {
      headers: { "X-Request-ID": "request-1" },
    });

    expect(invoke).toHaveBeenCalledWith("api_token");
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer satellite-secret");
    expect(headers.get("X-Request-ID")).toBe("request-1");
  });

  it("does not overwrite an explicit Authorization header", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const { api } = await loadApi({ token: "configured-secret" });

    await api.apiFetch("http://master/api/library", {
      headers: { Authorization: "Basic explicit" },
    });

    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("Authorization")).toBe(
      "Basic explicit",
    );
  });

  it("leaves tokenless requests untouched", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const { api } = await loadApi({ token: "   " });
    const init: RequestInit = { method: "POST" };

    await api.apiFetch("http://localhost/api/action", init);

    expect(fetch).toHaveBeenCalledWith("http://localhost/api/action", init);
  });

  it("uses the cached token for media query URLs after backend discovery", async () => {
    const { api, invoke } = await loadApi({
      token: "secret with spaces",
      backend: "http://master:5001",
    });

    const base = await api.backendUrl();

    expect(api.streamUrl(base, "mbid/a")).toBe(
      "http://master:5001/api/stream/mbid%2Fa?token=secret%20with%20spaces",
    );
    expect(api.albumCoverUrl(base, "album/a")).toBe(
      "http://master:5001/api/library/albums/album%2Fa/cover?token=secret%20with%20spaces",
    );
    expect(invoke.mock.calls.filter(([command]) => command === "api_token")).toHaveLength(1);
  });

  it("preserves the restart command name and serialized result shape", async () => {
    const restartResult = {
      success: false,
      message: "bind failed; restored loopback",
      bind_host: "127.0.0.1",
      backend_running: false,
    };
    const { api, invoke } = await loadApi({ restartResult });

    await expect(api.restartBackend()).resolves.toEqual(restartResult);
    expect(invoke).toHaveBeenCalledWith("restart_backend");
  });
});

describe("API error contracts", () => {
  it("throws the endpoint-specific status for failed collection requests", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })),
    );
    const { api } = await loadApi();

    await expect(api.fetchAlbums()).rejects.toThrow("albums: 503");
  });

  it("returns null for an unavailable optional artist-info response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("missing", { status: 404 })),
    );
    const { api } = await loadApi();

    await expect(api.fetchArtistInfo("Unknown Artist")).resolves.toBeNull();
  });

  it("keeps play recording fire-and-forget when transport fails", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { api } = await loadApi();

    await expect(api.recordPlay("track-1", "desktop", 1_234)).resolves.toBeUndefined();
    expect(warn).toHaveBeenCalledWith("recordPlay failed", expect.any(Error));
  });

  it("rejects a non-object JSON collection response at the boundary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const { api } = await loadApi();

    await expect(api.fetchAlbums()).rejects.toThrow(
      "Expected the API response to be a JSON object",
    );
  });

  it("invalidates the warmed media token only after a successful token save", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true, changed: ["api_token"] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const { api, invoke } = await loadApi({ token: "secret" });

    await api.backendUrl();
    await api.saveConfig({ api_token: "replacement" });
    await api.apiFetch("http://master/api/status");

    expect(
      invoke.mock.calls.filter(([command]) => command === "api_token"),
    ).toHaveLength(2);
  });

  it("invalidates the warmed media token after setup saves a seeded token", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    const { api, invoke } = await loadApi({ token: "seed-token" });

    await api.backendUrl();
    await api.saveSetupConfig({
      role: "satellite",
      music_library_path: "",
      downloads_path: "",
      api_token: "seed-token",
    });
    await api.apiFetch("http://master/api/status");

    expect(
      invoke.mock.calls.filter(([command]) => command === "api_token"),
    ).toHaveLength(2);
  });
});
