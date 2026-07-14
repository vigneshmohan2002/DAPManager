import {
  canAdvanceSetupStep,
  type SetupForm as Form,
  type SetupFormSetter,
  type SetupRole as Role,
} from "../lib/setupForm";
import {
  useSetupWizard,
  type SetupPathKey,
  type SetupPathValidity,
} from "./setup/useSetupWizard";

const STEPS = ["Role", "Paths", "Connection", "Integrations", "Auth", "Done"];

// ── Shared input primitives ──────────────────────────────────────────────────

function Field({
  label,
  value,
  onChange,
  onBlur,
  placeholder,
  hint,
  validity,
  secret,
  optional,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur?: () => void;
  placeholder?: string;
  hint?: string;
  validity?: boolean | null;
  secret?: boolean;
  optional?: boolean;
}) {
  const borderColor =
    validity === true
      ? "border-green-600"
      : validity === false
        ? "border-red-500"
        : "border-[var(--color-border)]";

  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
        {label}
        {optional && (
          <span className="text-[10px] text-[var(--color-text-muted)] opacity-60">
            (optional)
          </span>
        )}
      </label>
      <input
        type={secret ? "password" : "text"}
        className={`w-full px-3 py-2 rounded bg-[var(--color-surface)] border ${borderColor} text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-accent)]`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
      {hint && (
        <p className="text-[11px] text-[var(--color-text-muted)]">{hint}</p>
      )}
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)] mt-5 mb-2 first:mt-0">
      {children}
    </p>
  );
}

// ── SetupScreen ──────────────────────────────────────────────────────────────

export default function SetupScreen({ onDone }: { onDone: () => void }) {
  const wizard = useSetupWizard();
  const {
    step,
    form,
    pathValidity,
    detecting,
    saving,
    saveError,
    copyLabel,
    downloadLink,
    bundleLinkError,
    setField: set,
    validateField,
    autoDetect: handleAutoDetect,
    generateToken,
    next: handleNext,
    back,
    copyDownloadLink: handleCopy,
  } = wizard;

  // ── Step renderers ─────────────────────────────────────────────────────────

  const renderStep = () => {
    switch (step) {
      case 0:
        return <StepRole form={form} set={set} />;
      case 1:
        return (
          <StepPaths
            form={form}
            set={set}
            pathValidity={pathValidity}
            validateField={validateField}
          />
        );
      case 2:
        return (
          <StepConnection
            form={form}
            set={set}
            detecting={detecting}
            onAutoDetect={handleAutoDetect}
          />
        );
      case 3:
        return <StepIntegrations form={form} set={set} />;
      case 4:
        return (
          <StepAuth
            form={form}
            set={set}
            onGenerate={generateToken}
            saveError={saveError}
          />
        );
      case 5:
        return (
          <StepDone
            form={form}
            downloadLink={downloadLink}
            bundleLinkError={bundleLinkError}
            copyLabel={copyLabel}
            onCopy={handleCopy}
            onFinish={onDone}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div className="h-screen w-screen flex flex-col bg-[var(--color-bg)]">
      {/* Titlebar drag region */}
      <div className="titlebar-drag h-10 shrink-0" />

      <div className="flex-1 flex flex-col items-center justify-start overflow-y-auto px-6 pb-8">
        <div className="w-full max-w-lg">
          {/* Header */}
          <div className="mb-8 text-center">
            <h1 className="text-2xl font-bold text-[var(--color-text)]">
              Set up DAPManager
            </h1>
            <p className="mt-1 text-sm text-[var(--color-text-muted)]">
              {STEPS[step]}
            </p>
          </div>

          {/* Step indicators */}
          <div className="flex items-center justify-center gap-2 mb-8">
            {STEPS.map((label, i) => (
              <div key={label} className="flex items-center gap-2">
                <div
                  className={`w-2 h-2 rounded-full transition-colors ${
                    i < step
                      ? "bg-[var(--color-accent)]"
                      : i === step
                        ? "bg-[var(--color-text)]"
                        : "bg-[var(--color-border)]"
                  }`}
                />
                {i < STEPS.length - 1 && (
                  <div
                    className={`w-6 h-px ${i < step ? "bg-[var(--color-accent)]" : "bg-[var(--color-border)]"}`}
                  />
                )}
              </div>
            ))}
          </div>

          {/* Step content */}
          <div className="bg-[var(--color-surface)] rounded-xl border border-[var(--color-border)] p-6">
            {renderStep()}
          </div>

          {/* Navigation */}
          {step < 5 && (
            <div className="flex items-center justify-between mt-4">
              <button
                className="px-4 py-2 text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] disabled:opacity-30"
                onClick={back}
                disabled={step === 0}
              >
                Back
              </button>
              <button
                className="px-5 py-2 text-sm font-medium rounded-lg bg-[var(--color-accent)] text-white disabled:opacity-40 hover:opacity-90 transition-opacity flex items-center gap-2"
                onClick={handleNext}
                disabled={!canAdvanceSetupStep(step, form) || saving}
              >
                {saving ? (
                  <>
                    <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    Saving…
                  </>
                ) : step === 4 ? (
                  "Save & Continue"
                ) : (
                  "Next"
                )}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Step 0: Role ─────────────────────────────────────────────────────────────

function StepRole({
  form,
  set,
}: {
  form: Form;
  set: SetupFormSetter;
}) {
  const roles: { value: Role; label: string; description: string }[] = [
    {
      value: "master",
      label: "Master",
      description: "Owns the library and serves other devices on your network",
    },
    {
      value: "satellite",
      label: "Satellite",
      description: "Connects to an existing master and syncs its library",
    },
    {
      value: "standalone",
      label: "Standalone",
      description: "Single-device setup — no master or satellite network",
    },
  ];

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-[var(--color-text-muted)] mb-2">
        How will this device be used?
      </p>
      {roles.map(({ value, label, description }) => {
        const selected = form.role === value;
        return (
          <button
            key={value}
            onClick={() => set("role", value)}
            className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
              selected
                ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10"
                : "border-[var(--color-border)] hover:border-[var(--color-text-muted)]"
            }`}
          >
            <p
              className={`text-sm font-medium ${selected ? "text-[var(--color-accent)]" : "text-[var(--color-text)]"}`}
            >
              {label}
            </p>
            <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
              {description}
            </p>
          </button>
        );
      })}
    </div>
  );
}

// ── Step 1: Paths ─────────────────────────────────────────────────────────────

function StepPaths({
  form,
  set,
  pathValidity,
  validateField,
}: {
  form: Form;
  set: SetupFormSetter;
  pathValidity: SetupPathValidity;
  validateField: (key: SetupPathKey, value: string) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        Enter the paths where your music lives and where downloads land.
      </p>
      <Field
        label="Music library path"
        value={form.music_library_path}
        onChange={(v) => set("music_library_path", v)}
        onBlur={() => validateField("music_library_path", form.music_library_path)}
        placeholder="/Users/you/Music"
        hint="The root folder DAPManager scans for your local tracks."
        validity={pathValidity.music_library_path ?? null}
      />
      <Field
        label="Downloads path"
        value={form.downloads_path}
        onChange={(v) => set("downloads_path", v)}
        onBlur={() => validateField("downloads_path", form.downloads_path)}
        placeholder="/Users/you/Downloads/Music"
        hint="Where newly downloaded tracks are placed before they're scanned."
        validity={pathValidity.downloads_path ?? null}
      />
      <Field
        label="DAP mount point"
        value={form.dap_mount_point}
        onChange={(v) => set("dap_mount_point", v)}
        onBlur={() => validateField("dap_mount_point", form.dap_mount_point)}
        placeholder="/Volumes/DAP"
        hint="Root of your Digital Audio Player when plugged in."
        validity={
          form.dap_mount_point.trim()
            ? (pathValidity.dap_mount_point ?? null)
            : null
        }
        optional
      />
    </div>
  );
}

// ── Step 2: Connection ────────────────────────────────────────────────────────

function StepConnection({
  form,
  set,
  detecting,
  onAutoDetect,
}: {
  form: Form;
  set: SetupFormSetter;
  detecting: boolean;
  onAutoDetect: () => void;
}) {
  const isSatellite = form.role === "satellite";
  const isMaster = form.role === "master";

  return (
    <div className="flex flex-col gap-4">
      {isSatellite ? (
        <>
          <p className="text-sm text-[var(--color-text-muted)]">
            Enter the URL of the master DAPManager instance.
          </p>
          <Field
            label="Master URL"
            value={form.master_url}
            onChange={(v) => set("master_url", v)}
            placeholder="http://mybox.tail47bdc0.ts.net:5001"
            hint="The Tailscale (or LAN) address of your master."
          />
          <Field
            label="Device name"
            value={form.device_name}
            onChange={(v) => set("device_name", v)}
            placeholder="living-room-mac"
            hint="A friendly name shown in the fleet view on the master."
            optional
          />
        </>
      ) : isMaster ? (
        <>
          <p className="text-sm text-[var(--color-text-muted)]">
            The public URL satellites use to reach this Master. The authenticated
            desktop backend will listen on LAN/Tailscale after setup.
          </p>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
              Public URL
              <span className="text-[10px] opacity-60">(optional)</span>
            </label>
            <div className="flex gap-2">
              <input
                className="flex-1 px-3 py-2 rounded bg-[var(--color-surface)] border border-[var(--color-border)] text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-accent)]"
                value={form.public_master_url}
                onChange={(e) => set("public_master_url", e.target.value)}
                placeholder="http://mybox.tail47bdc0.ts.net:5001"
                autoComplete="off"
                spellCheck={false}
              />
              <button
                className="shrink-0 px-3 py-2 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-text-muted)] transition-colors disabled:opacity-40 flex items-center gap-1.5"
                onClick={onAutoDetect}
                disabled={detecting}
              >
                {detecting ? (
                  <span className="w-3 h-3 border-2 border-current/30 border-t-current rounded-full animate-spin" />
                ) : null}
                Auto-detect
              </button>
            </div>
            <p className="text-[11px] text-[var(--color-text-muted)]">
              Satellites use this URL to download a pre-configured app bundle
              and to sync with this master.
            </p>
          </div>
        </>
      ) : (
        <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/40 px-4 py-3">
          <p className="text-sm text-[var(--color-text)]">Local-only mode</p>
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            Standalone keeps its API on 127.0.0.1 and does not accept satellite
            connections. No public URL is needed.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Step 3: Integrations ──────────────────────────────────────────────────────

function StepIntegrations({
  form,
  set,
}: {
  form: Form;
  set: SetupFormSetter;
}) {
  const showDownloader = form.role !== "satellite";
  const showJellyfin = form.role !== "satellite";
  const showLidarr = form.role === "master";

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        All integrations are optional — you can configure them later from
        Settings.
      </p>

      {showDownloader && (
        <>
          <SectionHeading>Soulseek downloader</SectionHeading>
          <Field
            label="Username"
            value={form.slsk_username}
            onChange={(v) => set("slsk_username", v)}
            placeholder="soulseek_username"
            optional
          />
          <Field
            label="Password"
            value={form.slsk_password}
            onChange={(v) => set("slsk_password", v)}
            secret
            placeholder="••••••••"
            optional
          />
        </>
      )}

      {showJellyfin && (
        <>
          <SectionHeading>Jellyfin</SectionHeading>
          <Field
            label="Server URL"
            value={form.jellyfin_url}
            onChange={(v) => set("jellyfin_url", v)}
            placeholder="http://jellyfin:8096"
            optional
          />
          <Field
            label="API key"
            value={form.jellyfin_api_key}
            onChange={(v) => set("jellyfin_api_key", v)}
            secret
            placeholder="••••••••••••••••••••••••••••••••"
            optional
          />
          <Field
            label="User ID"
            value={form.jellyfin_user_id}
            onChange={(v) => set("jellyfin_user_id", v)}
            placeholder="00000000000000000000000000000000"
            optional
          />
        </>
      )}

      {showLidarr && (
        <>
          <SectionHeading>Lidarr</SectionHeading>
          <Field
            label="Server URL"
            value={form.lidarr_url}
            onChange={(v) => set("lidarr_url", v)}
            placeholder="http://lidarr:8686"
            optional
          />
          <Field
            label="API key"
            value={form.lidarr_api_key}
            onChange={(v) => set("lidarr_api_key", v)}
            secret
            placeholder="••••••••••••••••••••••••••••••••"
            optional
          />
        </>
      )}

      <SectionHeading>Identify &amp; Tag</SectionHeading>
      <Field
        label="AcoustID API key"
        value={form.acoustid_api_key}
        onChange={(v) => set("acoustid_api_key", v)}
        placeholder="abc123…"
        hint="Required for fingerprint-based track identification."
        optional
      />
      <Field
        label="Contact email"
        value={form.contact_email}
        onChange={(v) => set("contact_email", v)}
        placeholder="you@example.com"
        hint="Sent in User-Agent headers to MusicBrainz and Wikipedia."
        optional
      />
    </div>
  );
}

// ── Step 4: Auth ──────────────────────────────────────────────────────────────

function StepAuth({
  form,
  set,
  onGenerate,
  saveError,
}: {
  form: Form;
  set: SetupFormSetter;
  onGenerate: () => void;
  saveError: string | null;
}) {
  const masterRequiresToken = form.role === "master";
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-[var(--color-text-muted)]">
        {masterRequiresToken
          ? "A bearer token is required for Desktop Master. Once saved, the authenticated API listens on LAN/Tailscale as well as this Mac."
          : "Add a bearer token to require authentication on local API routes. Tokenless satellite and standalone installs remain loopback-only."}
      </p>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-[var(--color-text-muted)] flex items-center gap-1">
          API token
          <span className="text-[10px] opacity-60">
            {masterRequiresToken ? "(required)" : "(optional)"}
          </span>
        </label>
        <div className="flex gap-2">
          <input
            type="password"
            className="flex-1 px-3 py-2 rounded bg-[var(--color-surface)] border border-[var(--color-border)] text-sm text-[var(--color-text)] font-mono placeholder-[var(--color-text-muted)] focus:outline-none focus:border-[var(--color-accent)]"
            value={form.api_token}
            onChange={(e) => set("api_token", e.target.value)}
            placeholder={
              masterRequiresToken
                ? "generate or enter a strong token"
                : "optional in local-only mode"
            }
            autoComplete="new-password"
            spellCheck={false}
          />
          <button
            className="shrink-0 px-3 py-2 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-text-muted)] transition-colors"
            onClick={onGenerate}
          >
            Generate
          </button>
        </div>
        <p className="text-[11px] text-[var(--color-text-muted)]">
          {masterRequiresToken
            ? "Every satellite and API client must use this token. Treat it like a password."
            : "When set, local API clients must include this token. Treat it like a password."}
        </p>
      </div>
      {saveError && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-900 rounded px-3 py-2">
          {saveError}
        </p>
      )}
    </div>
  );
}

// ── Step 5: Done ──────────────────────────────────────────────────────────────

function StepDone({
  form,
  downloadLink,
  bundleLinkError,
  copyLabel,
  onCopy,
  onFinish,
}: {
  form: Form;
  downloadLink: string | null;
  bundleLinkError: string | null;
  copyLabel: string;
  onCopy: () => void;
  onFinish: () => void;
}) {
  const isSatellite = form.role === "satellite";
  const isMaster = form.role === "master";

  return (
    <div className="flex flex-col gap-4">
      <div className="text-center py-2">
        <div className="text-3xl mb-3">✓</div>
        <h2 className="text-lg font-semibold text-[var(--color-text)]">
          {isSatellite ? "You're connected" : "DAPManager is ready"}
        </h2>
        <p className="text-sm text-[var(--color-text-muted)] mt-1">
          {isSatellite
            ? `Syncing with ${form.master_url || "your master"}`
            : isMaster
              ? "Your authenticated Master is ready for LAN/Tailscale satellites."
              : "Your local-only library is set up and ready to go."}
        </p>
      </div>

      {isMaster && downloadLink && (
        <div className="flex flex-col gap-2 mt-2">
          <p className="text-xs text-[var(--color-text-muted)]">
            Share this link to let other Macs set up as satellites:
          </p>
          <div className="flex gap-2">
            <input
              readOnly
              className="flex-1 px-3 py-2 rounded bg-[var(--color-bg)] border border-[var(--color-border)] text-xs text-[var(--color-text-muted)] font-mono truncate focus:outline-none"
              value={downloadLink}
            />
            <button
              className="shrink-0 px-3 py-2 text-xs rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-text-muted)] transition-colors"
              onClick={onCopy}
            >
              {copyLabel}
            </button>
          </div>
          {form.api_token && (
            <p className="text-[11px] text-amber-400/80">
              This download-only link expires in one hour.
            </p>
          )}
        </div>
      )}

      {isMaster && bundleLinkError && (
        <p className="text-sm text-red-400 text-center">
          Could not create a satellite link: {bundleLinkError}
        </p>
      )}

      {isMaster && !downloadLink && !bundleLinkError && (
        <p className="text-sm text-[var(--color-text-muted)] text-center">
          You can configure a public URL later in Settings → Master to enable
          satellite distribution.
        </p>
      )}

      <button
        className="w-full mt-2 px-5 py-2.5 text-sm font-medium rounded-lg bg-[var(--color-accent)] text-white hover:opacity-90 transition-opacity"
        onClick={onFinish}
      >
        Open DAPManager
      </button>
    </div>
  );
}
