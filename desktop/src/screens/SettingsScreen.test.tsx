import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsScreen from "./SettingsScreen";

const apiMocks = vi.hoisted(() => ({
  fetchConfig: vi.fn(),
  fetchStatus: vi.fn(),
  regenerateDailyMixes: vi.fn(),
  restartBackend: vi.fn(),
  saveConfig: vi.fn(),
  startTagBackfill: vi.fn(),
}));

const toast = vi.hoisted(() => ({ show: vi.fn() }));

vi.mock("../lib/api", () => apiMocks);
vi.mock("../components/Toast", () => ({ useToast: () => toast }));

const configPayload = {
  config: {
    device_role: "standalone",
    api_token: "",
    report_inventory_to_host: false,
    sync_interval_hours: 12,
  },
  editable_keys: [
    "device_role",
    "api_token",
    "report_inventory_to_host",
    "sync_interval_hours",
  ],
  secret_keys: ["api_token"],
  bool_keys: ["report_inventory_to_host"],
  groups: [
    {
      label: "General",
      keys: [
        "device_role",
        "api_token",
        "report_inventory_to_host",
        "sync_interval_hours",
      ],
    },
  ],
};

describe("SettingsScreen controller contract", () => {
  beforeEach(() => {
    apiMocks.fetchConfig.mockResolvedValue(configPayload);
    apiMocks.fetchStatus.mockResolvedValue({
      running: false,
      task: null,
      message: null,
      detail: null,
    });
    apiMocks.saveConfig.mockResolvedValue({
      success: true,
      message: "",
      changed: ["device_role"],
    });
    apiMocks.restartBackend.mockResolvedValue({
      success: true,
      message: "Backend restarted.",
      bind_host: "127.0.0.1",
      backend_running: true,
    });
  });

  it("coerces the draft, saves it, and restarts after network-sensitive changes", async () => {
    const user = userEvent.setup();
    render(<SettingsScreen ready />);

    const role = await screen.findByLabelText("device_role");
    const interval = screen.getByLabelText("sync_interval_hours");
    await user.clear(role);
    await user.type(role, "master");
    await user.clear(interval);
    await user.type(interval, "24");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(apiMocks.saveConfig).toHaveBeenCalledWith({
        device_role: "master",
        sync_interval_hours: 24,
      });
    });
    expect(apiMocks.saveConfig.mock.invocationCallOrder[0]).toBeLessThan(
      apiMocks.restartBackend.mock.invocationCallOrder[0],
    );
    expect(toast.show).toHaveBeenCalledWith(
      "Saved 1 change: device_role. Backend restarted.",
    );
  });

  it("retries a failed stopped backend before saving the retained draft again", async () => {
    const user = userEvent.setup();
    apiMocks.restartBackend
      .mockResolvedValueOnce({
        success: false,
        message: "Restart failed",
        bind_host: "127.0.0.1",
        backend_running: false,
      })
      .mockResolvedValueOnce({
        success: true,
        message: "Recovered.",
        bind_host: "127.0.0.1",
        backend_running: true,
      });
    apiMocks.saveConfig
      .mockResolvedValueOnce({
        success: true,
        message: "",
        changed: ["device_role"],
      })
      .mockResolvedValueOnce({ success: true, message: "", changed: [] });

    render(<SettingsScreen ready />);
    const role = await screen.findByLabelText("device_role");
    await user.clear(role);
    await user.type(role, "master");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Unsaved changes");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(apiMocks.restartBackend).toHaveBeenCalledTimes(2));
    expect(apiMocks.saveConfig).toHaveBeenCalledTimes(2);
    expect(apiMocks.restartBackend.mock.invocationCallOrder[1]).toBeLessThan(
      apiMocks.saveConfig.mock.invocationCallOrder[1],
    );
  });
});
