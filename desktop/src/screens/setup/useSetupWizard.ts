import { useEffect, useRef, useState } from "react";

import {
  detectPublicUrl,
  fetchSatelliteBundleLink,
  restartBackend,
  saveSetupConfig,
  validatePath,
} from "../../lib/api";
import {
  buildSetupPayload,
  DEFAULT_SETUP_FORM,
  type SetupForm,
  type SetupFormSetter,
} from "../../lib/setupForm";

export type SetupPathKey =
  | "music_library_path"
  | "downloads_path"
  | "dap_mount_point";

export type SetupPathValidity = Partial<
  Record<SetupPathKey, boolean | null>
>;

export type SetupWizard = {
  step: number;
  form: SetupForm;
  pathValidity: SetupPathValidity;
  detecting: boolean;
  saving: boolean;
  saveError: string | null;
  copyLabel: string;
  downloadLink: string | null;
  bundleLinkError: string | null;
  setField: SetupFormSetter;
  validateField: (key: SetupPathKey, value: string) => Promise<void>;
  autoDetect: () => Promise<void>;
  generateToken: () => void;
  next: () => Promise<void>;
  back: () => void;
  copyDownloadLink: () => void;
};

function randomToken(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export function useSetupWizard(): SetupWizard {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<SetupForm>(DEFAULT_SETUP_FORM);
  const [pathValidity, setPathValidity] = useState<SetupPathValidity>({});
  const [detecting, setDetecting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [copyLabel, setCopyLabel] = useState("Copy link");
  const [downloadLink, setDownloadLink] = useState<string | null>(null);
  const [bundleLinkError, setBundleLinkError] = useState<string | null>(null);
  const [restartOnly, setRestartOnly] = useState(false);
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setField: SetupFormSetter = (key, value) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const validateField = async (key: SetupPathKey, value: string) => {
    if (!value.trim()) {
      setPathValidity((current) => ({ ...current, [key]: null }));
      return;
    }
    const { ok } = await validatePath(value.trim());
    setPathValidity((current) => ({ ...current, [key]: ok }));
  };

  const autoDetect = async () => {
    setDetecting(true);
    try {
      const result = await detectPublicUrl();
      if (result.url) setField("public_master_url", result.url);
    } finally {
      setDetecting(false);
    }
  };

  const generateToken = () => {
    setField("api_token", randomToken());
  };

  const persistSetup = async (): Promise<boolean> => {
    if (restartOnly) {
      const recovered = await restartBackend();
      if (!recovered.success) {
        setRestartOnly(!recovered.backend_running);
        setSaveError(recovered.message);
        return false;
      }
      setRestartOnly(false);
    }

    const result = await saveSetupConfig(buildSetupPayload(form));
    if (!result.success) {
      setSaveError(result.message ?? "Save failed");
      return false;
    }

    const restarted = await restartBackend();
    if (!restarted.success) {
      setRestartOnly(!restarted.backend_running);
      setSaveError(restarted.message);
      return false;
    }
    setRestartOnly(false);

    if (form.role !== "master" || !form.public_master_url.trim()) return true;
    try {
      const bundle = await fetchSatelliteBundleLink();
      setDownloadLink(bundle.url);
      setBundleLinkError(null);
    } catch (error) {
      setDownloadLink(null);
      setBundleLinkError(String(error));
    }
    return true;
  };

  const next = async () => {
    if (step !== 4) {
      setStep((current) => current + 1);
      return;
    }

    setSaving(true);
    setSaveError(null);
    try {
      if (!(await persistSetup())) return;
    } catch (error) {
      setSaveError(`Setup failed: ${String(error)}`);
      return;
    } finally {
      setSaving(false);
    }
    setStep((current) => current + 1);
  };

  const copyDownloadLink = () => {
    if (!downloadLink) return;
    navigator.clipboard.writeText(downloadLink).then(() => {
      setCopyLabel("Copied!");
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopyLabel("Copy link"), 2000);
    });
  };

  useEffect(
    () => () => {
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    },
    [],
  );

  return {
    step,
    form,
    pathValidity,
    detecting,
    saving,
    saveError,
    copyLabel,
    downloadLink,
    bundleLinkError,
    setField,
    validateField,
    autoDetect,
    generateToken,
    next,
    back: () => setStep((current) => current - 1),
    copyDownloadLink,
  };
}
