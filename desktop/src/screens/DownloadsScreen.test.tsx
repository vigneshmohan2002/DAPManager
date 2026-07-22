import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DownloadsScreen from "./DownloadsScreen";

const apiMocks = vi.hoisted(() => ({
  clearCompletedDownloads: vi.fn(),
  deleteDownload: vi.fn(),
  fetchDownloads: vi.fn(),
  fetchStatus: vi.fn(),
  postAction: vi.fn(),
  retryDownload: vi.fn(),
}));

const toast = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../components/Toast", () => ({ useToast: () => toast }));
vi.mock("./downloads/AlbumRequestPanel", () => ({
  default: () => null,
}));

const failedRows = [
  {
    id: 1,
    query: "Quarantined album",
    status: "failed",
    last_attempt: "2026-07-22T20:00:00+00:00",
    attempt_count: 3,
    max_attempts: 3,
    next_attempt_at: null,
    is_paused: false,
    is_quarantined: true,
    last_error: "Exact release remains incomplete",
  },
  {
    id: 2,
    query: "Paused album",
    status: "failed",
    last_attempt: null,
    attempt_count: 1,
    max_attempts: 3,
    next_attempt_at: null,
    is_paused: true,
    is_quarantined: false,
    last_error: "Paused by operator",
  },
  {
    id: 3,
    query: "Scheduled album",
    status: "failed",
    last_attempt: null,
    attempt_count: 2,
    max_attempts: 3,
    next_attempt_at: "2026-07-23T09:30:00+00:00",
    is_paused: false,
    is_quarantined: false,
    last_error: "Source timed out",
  },
  {
    id: 4,
    query: "Legacy failed row",
    status: "failed",
    last_attempt: null,
  },
];

describe("DownloadsScreen retry state", () => {
  beforeEach(() => {
    apiMocks.fetchDownloads.mockResolvedValue(failedRows);
    apiMocks.fetchStatus.mockResolvedValue({
      running: false,
      task: null,
      message: null,
      detail: null,
    });
    apiMocks.retryDownload.mockResolvedValue({
      success: true,
      message: "queued",
    });
  });

  it("shows quarantine, pause, scheduled retry, attempt, and error details", async () => {
    render(<DownloadsScreen ready />);

    const quarantinedRow = (await screen.findByText("Quarantined album")).closest(
      "tr",
    );
    const pausedRow = screen.getByText("Paused album").closest("tr");
    const scheduledRow = screen.getByText("Scheduled album").closest("tr");

    expect(quarantinedRow).not.toBeNull();
    expect(pausedRow).not.toBeNull();
    expect(scheduledRow).not.toBeNull();
    expect(within(quarantinedRow!).getByText("Quarantined")).toBeInTheDocument();
    expect(within(quarantinedRow!).getByText("Attempt 3/3")).toBeInTheDocument();
    expect(quarantinedRow).toHaveTextContent(
      "Last error: Exact release remains incomplete",
    );
    expect(within(pausedRow!).getByText("Paused")).toBeInTheDocument();
    expect(within(pausedRow!).getByText("Attempt 1/3")).toBeInTheDocument();
    expect(
      within(scheduledRow!).getByText(/^Retry scheduled /),
    ).toBeInTheDocument();
    expect(within(scheduledRow!).getByText("Attempt 2/3")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(4);
  });

  it("keeps Retry available for a quarantined row", async () => {
    const user = userEvent.setup();
    render(<DownloadsScreen ready />);

    const row = (await screen.findByText("Quarantined album")).closest("tr");
    await user.click(within(row!).getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(apiMocks.retryDownload).toHaveBeenCalledWith(1));
    expect(toast.show).toHaveBeenCalledWith("Queued for retry.", "ok");
    expect(apiMocks.fetchDownloads).toHaveBeenCalledTimes(2);
  });
});
