import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AlbumRequestPanel from "./AlbumRequestPanel";

const apiMocks = vi.hoisted(() => ({
  fetchAlbumDownloadRequest: vi.fn(),
  fetchAlbumDownloadRequests: vi.fn(),
  fetchConfig: vi.fn(),
  postAction: vi.fn(),
  requestAlbumDownload: vi.fn(),
  searchAlbumReleases: vi.fn(),
}));

const toast = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../../lib/api", () => apiMocks);
vi.mock("../../components/Toast", () => ({ useToast: () => toast }));

const releaseMbid = "95fb59ed-1ece-419b-b62f-aef31e0ebf36";
const STORAGE_KEY_PREFIX_FOR_TEST = "dapmanager.desktop.albumRequestIds.v1";

const candidate = {
  release_mbid: releaseMbid,
  title: "Verified Album",
  artist: "Verified Artist",
  track_count: 10,
  date: "2026-01-02",
  country: "GB",
  status: "Official",
  disambiguation: "Deluxe gatefold",
  primary_type: "Album",
  format: "CD",
  label: "Example Records",
  catalog_number: "EX-10",
  barcode: "1234567890123",
  cover_url: "https://coverartarchive.org/front.jpg",
  musicbrainz_url: `https://musicbrainz.org/release/${releaseMbid}`,
  score: 99,
};

const queuedRequest = {
  id: 12,
  release_mbid: releaseMbid,
  title: "Verified Album",
  artist: "Verified Artist",
  track_count: 10,
  stage: "queued" as const,
  detail: "Waiting for the master download queue",
  completed_tracks: 0,
  queue_status: "pending",
  last_attempt: null,
  created_at: "2026-07-18 10:00:00",
  updated_at: "2026-07-18 10:00:00",
  cover_url: "https://coverartarchive.org/front.jpg",
};

describe("native verified album requests", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiMocks.fetchConfig.mockResolvedValue({
      config: {
        device_role: "satellite",
        master_url: "http://viggys-pc:5001",
      },
      editable_keys: [],
      secret_keys: [],
      bool_keys: [],
      groups: [],
    });
    apiMocks.fetchAlbumDownloadRequests.mockResolvedValue([]);
    apiMocks.fetchAlbumDownloadRequest.mockResolvedValue(queuedRequest);
    apiMocks.searchAlbumReleases.mockResolvedValue({
      query: "Verified Artist - Verified Album",
      ambiguous: false,
      candidates: [candidate],
    });
    apiMocks.requestAlbumDownload.mockResolvedValue({
      success: true,
      queued: true,
      message: "queued",
      request: queuedRequest,
    });
    apiMocks.postAction.mockResolvedValue({ success: true, message: "started" });
  });

  it("requires explicit release selection, sends only its MBID, and starts new work", async () => {
    const user = userEvent.setup();
    const onQueueChanged = vi.fn();
    render(<AlbumRequestPanel ready onQueueChanged={onQueueChanged} />);

    await user.type(
      screen.getByRole("searchbox", { name: "Search MusicBrainz albums" }),
      "Verified Artist - Verified Album",
    );
    const release = await screen.findByRole("button", {
      name: /Verified Album.*Verified Artist/i,
    });

    expect(screen.queryByRole("button", { name: /Request “/ })).not.toBeInTheDocument();
    expect(release).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("EX-10", { exact: false })).toBeInTheDocument();
    expect(screen.getByText(releaseMbid)).toBeInTheDocument();

    await user.click(release);
    await user.click(screen.getByRole("button", { name: "Request “Verified Album”" }));

    await waitFor(() => {
      expect(apiMocks.requestAlbumDownload).toHaveBeenCalledWith(releaseMbid);
    });
    expect(apiMocks.postAction).toHaveBeenCalledWith("/api/download");
    expect(onQueueChanged).toHaveBeenCalled();
    expect(
      await screen.findByRole("progressbar", {
        name: "Verified Album album progress",
      }),
    ).toHaveAttribute("aria-valuenow", "0");
    expect(toast.show).toHaveBeenCalledWith(
      "Verified FLAC album request queued and downloader started.",
      "ok",
    );
  });

  it("reconciles master progress and persists IDs under the configured master", async () => {
    const importing = {
      ...queuedRequest,
      stage: "importing" as const,
      detail: "Imported 3 of 10 file(s)",
      completed_tracks: 3,
    };
    apiMocks.fetchAlbumDownloadRequests.mockResolvedValue([importing]);
    apiMocks.fetchAlbumDownloadRequest.mockResolvedValue(importing);

    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);

    expect(await screen.findByText("Importing")).toBeInTheDocument();
    const progress = screen.getByRole("progressbar", {
      name: "Verified Album album progress",
    });
    expect(progress).toHaveAttribute("aria-valuenow", "3");
    expect(progress.closest("article")).toHaveTextContent("3/10 tracks");

    const key = `dapmanager.desktop.albumRequestIds.v1:${encodeURIComponent(
      "master:http://viggys-pc:5001",
    )}`;
    await waitFor(() => expect(window.localStorage.getItem(key)).toBe("[12]"));
  });

  it("keeps multiple tracked request IDs stable while polling", async () => {
    const key = `dapmanager.desktop.albumRequestIds.v1:${encodeURIComponent(
      "master:http://viggys-pc:5001",
    )}`;
    window.localStorage.setItem(key, "[12,13]");
    apiMocks.fetchAlbumDownloadRequest.mockImplementation((id: number) =>
      Promise.resolve({
        ...queuedRequest,
        id,
        title: id === 12 ? "First Album" : "Second Album",
      }),
    );

    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);

    await waitFor(() =>
      expect(apiMocks.fetchAlbumDownloadRequest).toHaveBeenCalledTimes(2),
    );
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    });

    expect(apiMocks.fetchAlbumDownloadRequest).toHaveBeenCalledTimes(2);
    expect(window.localStorage.getItem(key)).toBe("[12,13]");
    expect(await screen.findByText("First Album")).toBeInTheDocument();
    expect(await screen.findByText("Second Album")).toBeInTheDocument();
  });

  it("serializes slow progress polls instead of starting overlapping waves", async () => {
    vi.useFakeTimers();
    let unmount = () => {};
    try {
      const key = `dapmanager.desktop.albumRequestIds.v1:${encodeURIComponent(
        "master:http://viggys-pc:5001",
      )}`;
      window.localStorage.setItem(key, "[12]");
      let resolvePoll:
        | ((value: typeof queuedRequest) => void)
        | undefined;
      apiMocks.fetchAlbumDownloadRequest.mockImplementation(
        (_id: number, signal: AbortSignal) => {
          expect(signal).toBeInstanceOf(AbortSignal);
          return new Promise<typeof queuedRequest>((resolve) => {
            resolvePoll = resolve;
          });
        },
      );

      ({ unmount } = render(
        <AlbumRequestPanel ready onQueueChanged={vi.fn()} />,
      ));

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
        await Promise.resolve();
      });
      expect(apiMocks.fetchAlbumDownloadRequest).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });
      expect(apiMocks.fetchAlbumDownloadRequest).toHaveBeenCalledTimes(1);

      await act(async () => {
        resolvePoll?.(queuedRequest);
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      expect(apiMocks.fetchAlbumDownloadRequest).toHaveBeenCalledTimes(2);
    } finally {
      unmount();
      vi.useRealTimers();
    }
  });

  it("aborts progress polling and reconciliation when unmounted", async () => {
    const key = `dapmanager.desktop.albumRequestIds.v1:${encodeURIComponent(
      "master:http://viggys-pc:5001",
    )}`;
    window.localStorage.setItem(key, "[12]");
    const progressSignals: AbortSignal[] = [];
    const reconcileSignals: AbortSignal[] = [];
    apiMocks.fetchAlbumDownloadRequest.mockImplementation(
      (_id: number, signal: AbortSignal) => {
        progressSignals.push(signal);
        return new Promise(() => {});
      },
    );
    apiMocks.fetchAlbumDownloadRequests.mockImplementation(
      (signal: AbortSignal) => {
        reconcileSignals.push(signal);
        return new Promise(() => {});
      },
    );

    const { unmount } = render(
      <AlbumRequestPanel ready onQueueChanged={vi.fn()} />,
    );
    await waitFor(() => {
      expect(progressSignals.length).toBeGreaterThan(0);
      expect(reconcileSignals.length).toBeGreaterThan(0);
    });

    unmount();

    expect(progressSignals.every((signal) => signal.aborted)).toBe(true);
    expect(reconcileSignals.every((signal) => signal.aborted)).toBe(true);
  });

  it.each([
    "not a URL",
    "ftp://master.example/music",
    "http://user:secret@master.example:5001",
    "http://master.example:5001?token=secret",
    "http://master.example:5001#token-secret",
  ])("does not persist progress for an unsafe master URL: %s", async (masterUrl) => {
    apiMocks.fetchConfig.mockResolvedValue({
      config: {
        device_role: "satellite",
        master_url: masterUrl,
      },
      editable_keys: [],
      secret_keys: [],
      bool_keys: [],
      groups: [],
    });

    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);

    await waitFor(() => expect(apiMocks.fetchConfig).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    expect(window.localStorage.length).toBe(0);
  });

  it("does not load a shared fallback key when config lookup fails", async () => {
    const unsafeFallbackKey = `${STORAGE_KEY_PREFIX_FOR_TEST}:local-unavailable`;
    window.localStorage.setItem(unsafeFallbackKey, "[99]");
    apiMocks.fetchConfig.mockRejectedValue(new Error("config unavailable"));

    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);

    await waitFor(() => expect(apiMocks.fetchConfig).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });
    expect(apiMocks.fetchAlbumDownloadRequest).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(unsafeFallbackKey)).toBe("[99]");
    expect(window.localStorage.length).toBe(1);
  });

  it("does not restart the whole queue when the tracker already exists", async () => {
    const user = userEvent.setup();
    apiMocks.requestAlbumDownload.mockResolvedValue({
      success: true,
      queued: false,
      message: "already requested",
      request: { ...queuedRequest, stage: "downloading" },
    });
    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);

    fireEvent.change(
      screen.getByRole("searchbox", { name: "Search MusicBrainz albums" }),
      { target: { value: "Verified Album" } },
    );
    const release = await screen.findByRole("button", {
      name: /Verified Album.*Verified Artist/i,
    });
    await user.click(release);
    await user.click(screen.getByRole("button", { name: "Request “Verified Album”" }));

    await waitFor(() => expect(apiMocks.requestAlbumDownload).toHaveBeenCalled());
    expect(apiMocks.postAction).not.toHaveBeenCalled();
    expect(toast.show).toHaveBeenCalledWith(
      "That release is already downloading.",
      "ok",
    );
  });

  it("aborts and ignores a stale MusicBrainz response", async () => {
    const secondMbid = "461eac33-7edd-481a-a7d1-089ec6fc01af";
    let resolveFirst: ((value: { query: string; ambiguous: boolean; candidates: typeof candidate[] }) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    apiMocks.searchAlbumReleases.mockImplementation(
      (query: string, signal: AbortSignal) => {
        if (query === "First Album") {
          firstSignal = signal;
          return new Promise((resolve) => {
            resolveFirst = resolve;
          });
        }
        return Promise.resolve({
          query,
          ambiguous: false,
          candidates: [
            {
              ...candidate,
              release_mbid: secondMbid,
              title: "Second Album",
            },
          ],
        });
      },
    );
    render(<AlbumRequestPanel ready onQueueChanged={vi.fn()} />);
    const search = screen.getByRole("searchbox", { name: "Search MusicBrainz albums" });

    fireEvent.change(search, { target: { value: "First Album" } });
    await waitFor(() =>
      expect(apiMocks.searchAlbumReleases).toHaveBeenCalledWith(
        "First Album",
        expect.any(AbortSignal),
      ),
    );
    fireEvent.change(search, { target: { value: "Second Album" } });

    expect(
      await screen.findByRole("button", { name: /Second Album.*Verified Artist/i }),
    ).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    resolveFirst?.({
      query: "First Album",
      ambiguous: false,
      candidates: [{ ...candidate, title: "First Album" }],
    });
    await Promise.resolve();
    expect(
      screen.queryByRole("button", { name: /First Album.*Verified Artist/i }),
    ).not.toBeInTheDocument();
  });
});
