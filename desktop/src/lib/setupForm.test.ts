import { describe, expect, it } from "vitest";
import {
  buildSetupPayload,
  canAdvanceSetupStep,
  DEFAULT_SETUP_FORM,
  type SetupForm,
} from "./setupForm";

function form(overrides: Partial<SetupForm> = {}): SetupForm {
  return { ...DEFAULT_SETUP_FORM, ...overrides };
}

describe("canAdvanceSetupStep", () => {
  it("requires both trimmed library paths on the Paths step", () => {
    expect(
      canAdvanceSetupStep(
        1,
        form({ music_library_path: "/music", downloads_path: "   " }),
      ),
    ).toBe(false);
    expect(
      canAdvanceSetupStep(
        1,
        form({ music_library_path: " /music ", downloads_path: " /downloads " }),
      ),
    ).toBe(true);
  });

  it("applies role-specific connection and authentication requirements", () => {
    expect(canAdvanceSetupStep(2, form({ role: "satellite" }))).toBe(false);
    expect(
      canAdvanceSetupStep(2, form({ role: "satellite", master_url: " /master " })),
    ).toBe(true);
    expect(canAdvanceSetupStep(4, form({ role: "master" }))).toBe(false);
    expect(canAdvanceSetupStep(4, form({ role: "standalone" }))).toBe(true);
  });
});

describe("buildSetupPayload", () => {
  it("trims required fields and omits empty optional groups", () => {
    expect(
      buildSetupPayload(
        form({
          role: "satellite",
          music_library_path: " /music ",
          downloads_path: " /downloads ",
          slsk_password: "unused-secret",
          jellyfin_api_key: "unused-key",
          lidarr_api_key: "unused-key",
        }),
      ),
    ).toEqual({
      role: "satellite",
      music_library_path: "/music",
      downloads_path: "/downloads",
    });
  });

  it("preserves the existing anchor-field rules for integration groups", () => {
    expect(
      buildSetupPayload(
        form({
          music_library_path: "/music",
          downloads_path: "/downloads",
          slsk_username: " user ",
          slsk_password: " pass ",
          jellyfin_url: " http://jellyfin ",
          jellyfin_api_key: " jellyfin-key ",
          jellyfin_user_id: " user-id ",
          lidarr_url: " http://lidarr ",
          lidarr_api_key: " lidarr-key ",
          lidarr_enabled: false,
        }),
      ),
    ).toEqual({
      role: "master",
      music_library_path: "/music",
      downloads_path: "/downloads",
      slsk_username: "user",
      slsk_password: "pass",
      jellyfin_url: "http://jellyfin",
      jellyfin_api_key: "jellyfin-key",
      jellyfin_user_id: "user-id",
      lidarr_url: "http://lidarr",
      lidarr_api_key: "lidarr-key",
      lidarr_enabled: true,
    });
  });
});
