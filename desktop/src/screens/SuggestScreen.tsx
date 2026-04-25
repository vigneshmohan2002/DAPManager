import { useEffect, useMemo, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchConfig,
  parseManualSuggestions,
  postSuggestions,
} from "../lib/api";

type Props = {
  ready: boolean;
  onOpenSettings: (focusKey?: string) => void;
};

const HOST_KEY = "dap_manager_host_url";

export default function SuggestScreen({ ready, onOpenSettings }: Props) {
  const [host, setHost] = useState<string | null>(null);
  const [hostError, setHostError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const toast = useToast();

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      try {
        const cfg = await fetchConfig();
        if (cancelled) return;
        const raw = cfg.config[HOST_KEY];
        const value = typeof raw === "string" ? raw.trim() : "";
        setHost(value || null);
        setHostError(null);
      } catch (e) {
        if (!cancelled) setHostError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ready]);

  const items = useMemo(() => parseManualSuggestions(text), [text]);
  const canSend = ready && !sending && Boolean(host) && items.length > 0;

  const handleSend = async () => {
    if (!host || items.length === 0) return;
    setSending(true);
    const result = await postSuggestions(host, items);
    setSending(false);
    if (!result.success) {
      toast.show(result.message || "Suggestion failed", "err");
      return;
    }
    toast.show(
      `Sent ${items.length} to host. Queued ${result.queued}, skipped ${result.skipped}.`,
    );
    setText("");
  };

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Suggest"
        subtitle={host ? `→ ${host}` : "Host not configured"}
      />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-4 max-w-3xl">
        {hostError ? (
          <div className="text-sm text-[var(--color-accent)]">{hostError}</div>
        ) : null}

        {!host ? (
          <div className="rounded-md border border-[var(--color-accent)]/40 bg-[var(--color-accent)]/10 px-4 py-3 text-sm">
            <div className="text-[var(--color-text)] mb-2">
              Set{" "}
              <code className="text-[var(--color-accent)]">{HOST_KEY}</code> in
              Settings (e.g.{" "}
              <code className="text-[var(--color-accent)]">
                http://jellyfin.local:5001
              </code>
              ) to point this device at the host's DAPManager.
            </div>
            <button
              onClick={() => onOpenSettings(HOST_KEY)}
              className="px-3 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold hover:brightness-110"
            >
              Open Settings
            </button>
          </div>
        ) : null}

        <p className="text-sm text-[var(--color-text-muted)]">
          One suggestion per line. Lines like{" "}
          <code className="text-[var(--color-text)]">Artist - Title</code> split
          on the dash; anything else is sent as a free-form query. Each item is
          queued on the host's downloader.
        </p>

        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Radiohead - Idioteque&#10;Portishead - Roads"
          rows={10}
          spellCheck={false}
          className="w-full bg-[var(--color-surface)] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] rounded-md px-3 py-2 text-sm font-mono outline-none focus:ring-1 focus:ring-[var(--color-accent)] resize-y"
        />

        <div className="flex items-center gap-3">
          <button
            onClick={handleSend}
            disabled={!canSend}
            className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            {sending
              ? "Sending…"
              : items.length === 0
              ? "Send"
              : `Send ${items.length}`}
          </button>
          <span className="text-xs text-[var(--color-text-muted)]">
            {items.length === 0
              ? "Nothing parsed yet."
              : `${items.length} item${items.length === 1 ? "" : "s"} parsed.`}
          </span>
        </div>
      </div>
    </div>
  );
}
