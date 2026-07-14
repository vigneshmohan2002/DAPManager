import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BackendStatus, Contribution } from "../../lib/api";
import { useContributionsController } from "./useContributionsController";

const apiMocks = vi.hoisted(() => ({
  contributeAllLocalTracks: vi.fn(),
  fetchConfig: vi.fn(),
  fetchContributions: vi.fn(),
  fetchOutgoingContributions: vi.fn(),
  fetchStatus: vi.fn(),
}));

vi.mock("../../lib/api", () => apiMocks);

const idleStatus: BackendStatus = {
  running: false,
  task: null,
  message: null,
  detail: null,
};

const outgoing: Contribution = {
  id: 7,
  contribution_id: 9,
  device_id: null,
  mbid: "track-1",
  artist: "Artist",
  title: "Track",
  album: "Album",
  target_quality: null,
  acquired_quality: null,
  status: "needs_upload",
  updated_at: null,
};

describe("useContributionsController", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMocks.fetchConfig.mockResolvedValue({
      config: {
        device_role: "satellite",
        master_url: "http://master:5001",
        contribute_to_host: true,
      },
    });
    apiMocks.fetchContributions.mockResolvedValue([]);
    apiMocks.fetchOutgoingContributions.mockResolvedValue([outgoing]);
    apiMocks.fetchStatus.mockResolvedValue(idleStatus);
  });

  it("loads both activity directions and registers the exact polling cadence", async () => {
    const setIntervalSpy = vi.spyOn(window, "setInterval");
    const showToast = vi.fn();
    const { result, unmount } = renderHook(() =>
      useContributionsController({ ready: true, showToast }),
    );

    await waitFor(() => expect(result.current.context).not.toBeNull());
    expect(result.current.activityItems).toEqual([outgoing]);
    expect(result.current.pendingCount).toBe(1);
    expect(result.current.canContribute).toBe(true);
    expect(apiMocks.fetchContributions).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchOutgoingContributions).toHaveBeenCalledTimes(1);
    expect(
      setIntervalSpy.mock.calls
        .map((call) => call[1])
        .filter((delay) => delay === 2000 || delay === 5000),
    ).toEqual([2000, 5000]);

    unmount();
    setIntervalSpy.mockRestore();
  });

  it("retains contribution start result and status-refresh semantics", async () => {
    const showToast = vi.fn();
    apiMocks.contributeAllLocalTracks.mockResolvedValue({
      success: true,
      message: "Contribution started.",
    });
    const { result, unmount } = renderHook(() =>
      useContributionsController({ ready: true, showToast }),
    );
    await waitFor(() => expect(result.current.canContribute).toBe(true));

    await act(async () => result.current.contributeAll());

    expect(apiMocks.contributeAllLocalTracks).toHaveBeenCalledTimes(1);
    expect(showToast).toHaveBeenCalledWith("Contribution started.");
    expect(apiMocks.fetchStatus).toHaveBeenCalledTimes(2);
    expect(result.current.starting).toBe(false);
    unmount();
  });
});
