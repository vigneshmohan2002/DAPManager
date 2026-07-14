import { afterEach, describe, expect, it, vi } from "vitest";

type ApiModule = typeof import("./api");

async function loadApi(options: {
  token?: string;
  backend?: string;
} = {}): Promise<{ api: ApiModule; invoke: ReturnType<typeof vi.fn> }> {
  vi.resetModules();
  const invoke = vi.fn(async (command: string) => {
    if (command === "api_token") return options.token ?? "";
    if (command === "backend_url") return options.backend ?? "http://localhost:5001";
    if (command === "backend_startup_error") return null;
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
  it("keeps the established runtime exports available", async () => {
    const { api } = await loadApi();

    expect(api).toEqual(
      expect.objectContaining({
        apiFetch: expect.any(Function),
        backendUrl: expect.any(Function),
        waitForBackend: expect.any(Function),
        restartBackend: expect.any(Function),
        fetchSetupStatus: expect.any(Function),
        saveSetupConfig: expect.any(Function),
        fetchAllTracks: expect.any(Function),
        fetchAlbumTracks: expect.any(Function),
        fetchPlaylists: expect.any(Function),
        createPlaylist: expect.any(Function),
        fetchSyncState: expect.any(Function),
        fetchContributions: expect.any(Function),
        fetchDownloads: expect.any(Function),
        streamUrl: expect.any(Function),
        albumCoverUrl: expect.any(Function),
      }),
    );
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
});
