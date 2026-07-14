import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SetupScreen from "./SetupScreen";

const apiMocks = vi.hoisted(() => ({
  detectPublicUrl: vi.fn(),
  fetchSatelliteBundleLink: vi.fn(),
  restartBackend: vi.fn(),
  saveSetupConfig: vi.fn(),
  validatePath: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  detectPublicUrl: apiMocks.detectPublicUrl,
  fetchSatelliteBundleLink: apiMocks.fetchSatelliteBundleLink,
  restartBackend: apiMocks.restartBackend,
  saveSetupConfig: apiMocks.saveSetupConfig,
  validatePath: apiMocks.validatePath,
}));

describe("SetupScreen payload contract", () => {
  beforeEach(() => {
    apiMocks.validatePath.mockResolvedValue({ ok: true });
    apiMocks.saveSetupConfig.mockResolvedValue({ success: true, message: "saved" });
    apiMocks.restartBackend.mockResolvedValue({
      success: true,
      message: "restarted",
      bind_host: "127.0.0.1",
      backend_running: true,
    });
    apiMocks.fetchSatelliteBundleLink.mockResolvedValue({ url: "https://example.test/bundle" });
  });

  it("trims fields and omits empty optional values for a satellite setup", async () => {
    const user = userEvent.setup();
    render(<SetupScreen onDone={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Satellite/ }));
    await user.click(screen.getByRole("button", { name: "Next" }));

    await user.type(
      screen.getByPlaceholderText("/Users/you/Music"),
      "  /srv/music  ",
    );
    await user.type(
      screen.getByPlaceholderText("/Users/you/Downloads/Music"),
      "  /srv/downloads  ",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));

    await user.type(
      screen.getByPlaceholderText("http://mybox.tail47bdc0.ts.net:5001"),
      "  http://master.tailnet:5001/  ",
    );
    await user.type(
      screen.getByPlaceholderText("living-room-mac"),
      "  kitchen-mac  ",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));

    await user.type(screen.getByPlaceholderText("abc123…"), "  acoustid-key  ");
    await user.type(
      screen.getByPlaceholderText("you@example.com"),
      "  owner@example.com  ",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));

    await user.type(
      screen.getByPlaceholderText("optional in local-only mode"),
      "  shared-token  ",
    );
    await user.click(screen.getByRole("button", { name: "Save & Continue" }));

    await screen.findByText("You're connected");
    expect(apiMocks.saveSetupConfig).toHaveBeenCalledTimes(1);
    expect(apiMocks.saveSetupConfig).toHaveBeenCalledWith({
      role: "satellite",
      music_library_path: "/srv/music",
      downloads_path: "/srv/downloads",
      master_url: "http://master.tailnet:5001/",
      device_name: "kitchen-mac",
      acoustid_api_key: "acoustid-key",
      contact_email: "owner@example.com",
      api_token: "shared-token",
    });
    expect(apiMocks.restartBackend).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchSatelliteBundleLink).not.toHaveBeenCalled();
  });

  it("does not advance from Paths until both required paths are present", async () => {
    const user = userEvent.setup();
    render(<SetupScreen onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Next" }));

    const next = screen.getByRole("button", { name: "Next" });
    expect(next).toBeDisabled();
    await user.type(screen.getByPlaceholderText("/Users/you/Music"), "/music");
    expect(next).toBeDisabled();
    await user.type(
      screen.getByPlaceholderText("/Users/you/Downloads/Music"),
      "/downloads",
    );

    await waitFor(() => expect(next).toBeEnabled());
  });

  it("retries a stopped backend before re-saving the retained setup form", async () => {
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
        message: "Recovered",
        bind_host: "127.0.0.1",
        backend_running: true,
      })
      .mockResolvedValueOnce({
        success: true,
        message: "Restarted",
        bind_host: "127.0.0.1",
        backend_running: true,
      });

    render(<SetupScreen onDone={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(screen.getByPlaceholderText("/Users/you/Music"), "/music");
    await user.type(
      screen.getByPlaceholderText("/Users/you/Downloads/Music"),
      "/downloads",
    );
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.type(
      screen.getByPlaceholderText("generate or enter a strong token"),
      "master-token",
    );

    await user.click(screen.getByRole("button", { name: "Save & Continue" }));
    expect(await screen.findByText("Restart failed")).toBeInTheDocument();
    expect(apiMocks.saveSetupConfig).toHaveBeenCalledTimes(1);
    expect(apiMocks.restartBackend).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Save & Continue" }));
    expect(await screen.findByText("DAPManager is ready")).toBeInTheDocument();
    expect(apiMocks.saveSetupConfig).toHaveBeenCalledTimes(2);
    expect(apiMocks.restartBackend).toHaveBeenCalledTimes(3);
    expect(apiMocks.restartBackend.mock.invocationCallOrder[1]).toBeLessThan(
      apiMocks.saveSetupConfig.mock.invocationCallOrder[1],
    );
    expect(apiMocks.saveSetupConfig.mock.invocationCallOrder[1]).toBeLessThan(
      apiMocks.restartBackend.mock.invocationCallOrder[2],
    );
  });
});
