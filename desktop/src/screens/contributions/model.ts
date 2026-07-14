import type {
  AudioQuality,
  ConfigValue,
  Contribution,
} from "../../lib/api";

export type DeviceContext = {
  role: string;
  masterUrl: string | null;
  automatic: boolean;
};

export type ContributionStatusMeta = {
  label: string;
  className: string;
  terminal: boolean;
};

const UNKNOWN_STATUS_META: ContributionStatusMeta = {
  label: "Unknown",
  className: "bg-neutral-800 text-neutral-300",
  terminal: false,
};

const STATUS_META: Readonly<Record<string, ContributionStatusMeta>> = {
  attempting: {
    label: "Master downloading",
    className: "bg-sky-900/40 text-sky-300",
    terminal: false,
  },
  have_better: {
    label: "Already matched",
    className: "bg-violet-900/40 text-violet-300",
    terminal: true,
  },
  satisfied: {
    label: "Downloaded",
    className: "bg-emerald-900/40 text-emerald-300",
    terminal: true,
  },
  needs_upload: {
    label: "Upload requested",
    className: "bg-amber-900/40 text-amber-300",
    terminal: false,
  },
  ingested: {
    label: "Ingested",
    className: "bg-emerald-900/40 text-emerald-300",
    terminal: true,
  },
};

export function parseDeviceContext(
  config: Readonly<Record<string, ConfigValue>>,
): DeviceContext {
  const rawRole = config.device_role;
  const rawMasterUrl = config.master_url;
  const rawAutomatic = config.contribute_to_host;

  return {
    role:
      typeof rawRole === "string" && rawRole.trim()
        ? rawRole.trim()
        : "satellite",
    masterUrl:
      typeof rawMasterUrl === "string" && rawMasterUrl.trim()
        ? rawMasterUrl.trim()
        : null,
    // Preserve the legacy fallback: any configured raw value enables
    // contribution unless the dedicated boolean explicitly overrides it.
    automatic:
      typeof rawAutomatic === "boolean"
        ? rawAutomatic
        : Boolean(rawMasterUrl),
  };
}

export function contributionStatusMeta(
  status: string,
): ContributionStatusMeta {
  const known = STATUS_META[status];
  if (known) return known;
  if (!status) return UNKNOWN_STATUS_META;
  return { ...UNKNOWN_STATUS_META, label: status };
}

export function audioQualityLabel(quality: AudioQuality | null): string {
  if (!quality) return "—";

  const parts: string[] = [];
  if (quality.ext) parts.push(quality.ext.toUpperCase());
  parts.push(quality.lossless ? "Lossless" : "Lossy");
  if (quality.bits_per_sample) parts.push(`${quality.bits_per_sample}-bit`);
  if (quality.sample_rate) {
    const khz = quality.sample_rate / 1000;
    parts.push(`${Number.isInteger(khz) ? khz : khz.toFixed(1)} kHz`);
  }
  if (quality.bitrate) {
    parts.push(`${Math.round(quality.bitrate / 1000)} kbps`);
  }
  return parts.join(" · ");
}

export function contributionCounts(items: readonly Contribution[] | null): {
  pending: number;
  complete: number;
} {
  if (!items) return { pending: 0, complete: 0 };
  const pending = items.filter(
    (item) => !contributionStatusMeta(item.status).terminal,
  ).length;
  return { pending, complete: items.length - pending };
}
