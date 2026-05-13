import { useEffect, useMemo, useRef, useState } from "react";
import { fetchLyrics, saveLyrics, type LyricsResponse } from "../lib/api";
import { usePlayer } from "../player/PlayerContext";

type Props = {
  open: boolean;
  onClose: () => void;
};

type SyncedLine = { tMs: number; text: string };

// Parse an LRC time tag: [mm:ss.xx] or [mm:ss] → ms. Returns null when
// the bracketed prefix isn't a time tag (e.g. [ar:Artist] metadata).
function parseTimeTag(token: string): number | null {
  const m = token.match(/^\[(\d+):(\d{1,2})(?:\.(\d{1,3}))?\]$/);
  if (!m) return null;
  const minutes = Number(m[1]);
  const seconds = Number(m[2]);
  const fracStr = m[3] ?? "0";
  // ".5" should be 500ms not 5ms; pad to 3 digits before parsing.
  const fracMs = Number(fracStr.padEnd(3, "0").slice(0, 3));
  return minutes * 60_000 + seconds * 1_000 + fracMs;
}

// Parse an LRC blob into ascending-time lines. Lines without a time
// tag are dropped (LRC metadata headers, blank separator lines) so
// the active-line index can do a clean ordered search.
function parseLrc(blob: string): SyncedLine[] {
  const lines: SyncedLine[] = [];
  for (const raw of blob.split(/\r?\n/)) {
    const tags = raw.match(/\[[^\]]*\]/g) ?? [];
    const text = raw.replace(/\[[^\]]*\]/g, "").trim();
    for (const tag of tags) {
      const ms = parseTimeTag(tag);
      if (ms !== null) lines.push({ tMs: ms, text });
    }
  }
  lines.sort((a, b) => a.tMs - b.tMs);
  return lines;
}

// Active line = largest tMs <= positionMs. Falls back to -1 (none
// active) when position is before the first line, which is the
// pre-roll case at the start of a track.
function findActiveIndex(lines: SyncedLine[], positionMs: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].tMs <= positionMs) {
      best = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return best;
}

export default function LyricsPane({ open, onClose }: Props) {
  const { current, position, seek } = usePlayer();
  const [data, setData] = useState<LyricsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [draftSynced, setDraftSynced] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Re-fetch when the user opens the pane or the track changes. The
  // server caches LRCLIB results so reopens on the same track are
  // cheap — but the fetch is still synchronous, so don't trigger it
  // when the pane is closed.
  useEffect(() => {
    if (!open || !current) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setEditing(false);
    (async () => {
      try {
        const payload = await fetchLyrics(current.mbid);
        if (!cancelled) setData(payload);
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, current?.mbid]);

  const lines = useMemo<SyncedLine[]>(() => {
    if (!data?.lrc || !data.synced) return [];
    return parseLrc(data.lrc);
  }, [data]);

  // Active index recomputed on every position tick. The lookup is a
  // binary search over a small array — cheap even at 20Hz.
  const activeIndex = useMemo(
    () => (lines.length === 0 ? -1 : findActiveIndex(lines, position * 1000)),
    [lines, position],
  );

  // Autoscroll the active line into view. Reads the live DOM node
  // each tick rather than holding refs per line — keeps the LRC
  // rendering loop simple at the cost of one querySelector per
  // active-index *change* (not per timeupdate).
  useEffect(() => {
    if (activeIndex < 0 || !containerRef.current) return;
    const node = containerRef.current.querySelector<HTMLElement>(
      `[data-lyric-index="${activeIndex}"]`,
    );
    node?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [activeIndex]);

  if (!open) return null;

  const startEditing = () => {
    setDraft(data?.lrc ?? "");
    setDraftSynced(Boolean(data?.synced));
    setEditing(true);
  };

  const handleSave = async () => {
    if (!current) return;
    try {
      const saved = await saveLyrics(current.mbid, draft, draftSynced);
      setData(saved);
      setEditing(false);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <aside className="w-96 shrink-0 bg-[var(--color-bg-sidebar)] border-l border-[var(--color-border)] flex flex-col">
      <header className="h-14 shrink-0 border-b border-[var(--color-border)] flex items-center px-4 gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold">Lyrics</div>
          {current && (
            <div className="text-xs text-[var(--color-text-muted)] truncate">
              {current.title}
            </div>
          )}
        </div>
        {data?.source && !editing && (
          <button
            onClick={startEditing}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title="Paste your own lyrics for this track"
          >
            Edit
          </button>
        )}
        <button
          onClick={onClose}
          aria-label="Close lyrics"
          className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          ✕
        </button>
      </header>
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-6 py-6 text-sm"
      >
        {!current ? (
          <p className="text-[var(--color-text-muted)]">Nothing playing.</p>
        ) : loading ? (
          <p className="text-[var(--color-text-muted)]">Loading…</p>
        ) : error ? (
          <p className="text-[var(--color-accent)]">{error}</p>
        ) : editing ? (
          <Editor
            draft={draft}
            draftSynced={draftSynced}
            onChangeDraft={setDraft}
            onChangeSynced={setDraftSynced}
            onSave={handleSave}
            onCancel={() => setEditing(false)}
          />
        ) : !data?.lrc ? (
          <EmptyState onAdd={startEditing} stale={data?.stale} />
        ) : data.synced ? (
          <ol className="flex flex-col gap-3 leading-relaxed">
            {lines.map((line, i) => {
              const active = i === activeIndex;
              return (
                <li
                  key={`${line.tMs}-${i}`}
                  data-lyric-index={i}
                  onClick={() => seek(line.tMs / 1000)}
                  className={`cursor-pointer transition-all ${
                    active
                      ? "text-[var(--color-text)] text-base font-medium"
                      : i < activeIndex
                        ? "text-[var(--color-text-muted)]/40"
                        : "text-[var(--color-text-muted)]/70 hover:text-[var(--color-text)]"
                  }`}
                >
                  {line.text || " "}
                </li>
              );
            })}
          </ol>
        ) : (
          <pre className="whitespace-pre-wrap leading-relaxed text-[var(--color-text-muted)] font-sans">
            {data.lrc}
          </pre>
        )}
        {data?.stale && !editing && (
          <p className="mt-4 text-[11px] text-amber-400/80">
            Couldn't reach LRCLIB — showing the cached copy.
          </p>
        )}
      </div>
    </aside>
  );
}

function EmptyState({
  onAdd,
  stale,
}: {
  onAdd: () => void;
  stale?: boolean;
}) {
  return (
    <div className="flex flex-col items-center text-center gap-3 py-12">
      <p className="text-[var(--color-text-muted)]">
        {stale
          ? "Couldn't reach LRCLIB and there's nothing cached for this track."
          : "No lyrics found on LRCLIB for this track."}
      </p>
      <button
        onClick={onAdd}
        className="text-xs px-3 py-1.5 rounded border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-text-muted)]"
      >
        Paste lyrics manually
      </button>
    </div>
  );
}

function Editor({
  draft,
  draftSynced,
  onChangeDraft,
  onChangeSynced,
  onSave,
  onCancel,
}: {
  draft: string;
  draftSynced: boolean;
  onChangeDraft: (v: string) => void;
  onChangeSynced: (v: boolean) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-[var(--color-text-muted)]">
        Paste plain text or LRC-formatted lines like{" "}
        <code>[01:23.45] line of lyrics</code>. Saved lyrics override
        LRCLIB for this track.
      </p>
      <textarea
        value={draft}
        onChange={(e) => onChangeDraft(e.target.value)}
        placeholder="Lyrics here…"
        rows={12}
        className="w-full px-3 py-2 rounded bg-[var(--color-surface)] border border-[var(--color-border)] text-sm font-mono text-[var(--color-text)] focus:outline-none focus:border-[var(--color-accent)]"
      />
      <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <input
          type="checkbox"
          checked={draftSynced}
          onChange={(e) => onChangeSynced(e.target.checked)}
        />
        These lines have <code>[mm:ss.xx]</code> time tags (synced)
      </label>
      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={onCancel}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5"
        >
          Cancel
        </button>
        <button
          onClick={onSave}
          disabled={!draft.trim()}
          className="text-xs px-3 py-1.5 rounded bg-[var(--color-accent)] text-white disabled:opacity-40"
        >
          Save
        </button>
      </div>
    </div>
  );
}
