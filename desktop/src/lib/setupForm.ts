import type { SetupPayload } from "./api";

export type SetupRole = "master" | "satellite" | "standalone";

export type SetupForm = {
  role: SetupRole;
  music_library_path: string;
  downloads_path: string;
  dap_mount_point: string;
  master_url: string;
  public_master_url: string;
  device_name: string;
  slsk_username: string;
  slsk_password: string;
  jellyfin_url: string;
  jellyfin_api_key: string;
  jellyfin_user_id: string;
  lidarr_url: string;
  lidarr_api_key: string;
  lidarr_enabled: boolean;
  acoustid_api_key: string;
  contact_email: string;
  api_token: string;
};

export type SetupFormSetter = <Key extends keyof SetupForm>(
  key: Key,
  value: SetupForm[Key],
) => void;

export const DEFAULT_SETUP_FORM: SetupForm = {
  role: "master",
  music_library_path: "",
  downloads_path: "",
  dap_mount_point: "",
  master_url: "",
  public_master_url: "",
  device_name: "",
  slsk_username: "",
  slsk_password: "",
  jellyfin_url: "",
  jellyfin_api_key: "",
  jellyfin_user_id: "",
  lidarr_url: "",
  lidarr_api_key: "",
  lidarr_enabled: false,
  acoustid_api_key: "",
  contact_email: "",
  api_token: "",
};

export function canAdvanceSetupStep(step: number, form: SetupForm): boolean {
  if (step === 1) {
    return Boolean(
      form.music_library_path.trim() && form.downloads_path.trim(),
    );
  }
  if (step === 2 && form.role === "satellite") {
    return Boolean(form.master_url.trim());
  }
  if (step === 4 && form.role === "master") {
    return Boolean(form.api_token.trim());
  }
  return true;
}

export function buildSetupPayload(form: SetupForm): SetupPayload {
  const dapMountPoint = form.dap_mount_point.trim();
  const masterUrl = form.master_url.trim();
  const publicMasterUrl = form.public_master_url.trim();
  const deviceName = form.device_name.trim();
  const slskUsername = form.slsk_username.trim();
  const jellyfinUrl = form.jellyfin_url.trim();
  const lidarrUrl = form.lidarr_url.trim();
  const acoustidApiKey = form.acoustid_api_key.trim();
  const contactEmail = form.contact_email.trim();
  const apiToken = form.api_token.trim();

  return {
    role: form.role,
    music_library_path: form.music_library_path.trim(),
    downloads_path: form.downloads_path.trim(),
    ...(dapMountPoint && { dap_mount_point: dapMountPoint }),
    ...(masterUrl && { master_url: masterUrl }),
    ...(publicMasterUrl && { public_master_url: publicMasterUrl }),
    ...(deviceName && { device_name: deviceName }),
    ...(slskUsername && {
      slsk_username: slskUsername,
      slsk_password: form.slsk_password.trim(),
    }),
    ...(jellyfinUrl && {
      jellyfin_url: jellyfinUrl,
      jellyfin_api_key: form.jellyfin_api_key.trim(),
      jellyfin_user_id: form.jellyfin_user_id.trim(),
    }),
    ...(lidarrUrl && {
      lidarr_url: lidarrUrl,
      lidarr_api_key: form.lidarr_api_key.trim(),
      lidarr_enabled: true,
    }),
    ...(acoustidApiKey && { acoustid_api_key: acoustidApiKey }),
    ...(contactEmail && { contact_email: contactEmail }),
    ...(apiToken && { api_token: apiToken }),
  };
}
