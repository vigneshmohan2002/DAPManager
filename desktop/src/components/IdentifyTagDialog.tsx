import { useEffect } from "react";
import { createPortal } from "react-dom";
import type { IdentifyCandidate, TagMeta, TagTier } from "../lib/api";

type Props = {
  candidate: IdentifyCandidate;
  localPath: string;
  applying: boolean;
  onApply: (meta: TagMeta) => void;
  onCancel: () => void;
};

// Ordered so the diff reads top-to-bottom like a track's info pane
// does elsewhere in the app. Keep "mbid" and "release_mbid" last —
// they're disambiguating identifiers, not presentational.
const FIELDS: Array<{ key: keyof TagMeta; label: string }> = [
  { key: "title", label: "Title" },
  { key: "artist", label: "Artist" },
  { key: "album", label: "Album" },
  { key: "album_artist", label: "Album Artist" },
  { key: "date", label: "Date" },
  { key: "track_number", label: "Track #" },
  { key: "disc_number", label: "Disc #" },
  { key: "mbid", label: "MBID" },
  { key: "release_mbid", label: "Release MBID" },
];

const TIER_CLASS: Record<TagTier, string> = {
  green: "bg-emerald-900/40 text-emerald-300 border-emerald-800",
  yellow: "bg-amber-900/40 text-amber-300 border-amber-800",
  red: "bg-rose-900/40 text-rose-300 border-rose-800",
};

const TIER_HINT: Record<TagTier, string> = {
  green: "High confidence",
  yellow: "Mid confidence — review before applying",
  red: "Low confidence — likely wrong, double-check",
};

export default function IdentifyTagDialog({
  candidate,
  localPath,
  applying,
  onApply,
  onCancel,
}: Props) {
  useEffect(() => {
    // Esc cancels; Cmd/Ctrl+Enter applies. Apply is guarded inside
    // the handler too so a fast double-press doesn't submit twice.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        if (!applying) onApply(candidate.meta);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [applying, candidate, onApply, onCancel]);

  return createPortal(
    <div
      className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] shadow-2xl"
      >
        <header className="px-6 py-4 border-b border-[var(--color-border)] flex items-center gap-3">
          <h2 className="text-lg font-semibold">Identify &amp; Tag</h2>
          <span
            className={`px-2 py-0.5 text-xs uppercase tracking-wider rounded-full border ${TIER_CLASS[candidate.tier]}`}
            title={TIER_HINT[candidate.tier]}
          >
            {candidate.tier} · {(candidate.score * 100).toFixed(0)}%
          </span>
        </header>
        <div className="px-6 py-4">
          <div
            className="text-xs text-[var(--color-text-muted)] font-mono truncate mb-4"
            title={localPath}
          >
            {localPath}
          </div>
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] border-b border-[var(--color-border)]">
              <tr>
                <th className="text-left font-medium py-2 w-32">Field</th>
                <th className="text-left font-medium py-2">Current</th>
                <th className="text-left font-medium py-2">Proposed</th>
              </tr>
            </thead>
            <tbody>
              {FIELDS.map(({ key, label }) => {
                const current = (candidate.current[key] ?? "") as string;
                const proposed = (candidate.meta[key] ?? "") as string;
                const changed = String(current) !== String(proposed);
                return (
                  <tr
                    key={key}
                    className="border-b border-[var(--color-border)]/40 align-top"
                  >
                    <td className="py-2 text-[var(--color-text-muted)]">
                      {label}
                    </td>
                    <td className="py-2 pr-2 text-[var(--color-text-muted)] break-words">
                      {current || (
                        <span className="italic opacity-60">empty</span>
                      )}
                    </td>
                    <td
                      className={`py-2 break-words ${
                        changed
                          ? "text-[var(--color-text)] font-medium"
                          : "text-[var(--color-text-muted)]"
                      }`}
                    >
                      {proposed || (
                        <span className="italic opacity-60">empty</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-4 text-xs text-[var(--color-text-muted)]">
            Apply writes tags to the file and updates the catalog row. If the
            MBID changed, the old row becomes an orphan and a new row is
            upserted in its place.
          </p>
        </div>
        <footer className="px-6 py-3 border-t border-[var(--color-border)] flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={applying}
            className="px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 hover:bg-[var(--color-surface)]/70"
          >
            Cancel
          </button>
          <button
            onClick={() => onApply(candidate.meta)}
            disabled={applying}
            className="px-4 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            {applying ? "Applying…" : "Apply"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
