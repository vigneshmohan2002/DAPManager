import { useEffect, useMemo, useRef, useState } from "react";
import { fetchLyrics, saveLyrics, type LyricsResponse } from "../lib/api";
import { usePlayer } from "../player/PlayerContext";
import Icon from "./Icon";
import { useResponsiveSidePanel } from "./useResponsiveSidePanel";

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
  const { closeButtonRef, compact, handleKeyDown } =
    useResponsiveSidePanel(open, onClose);

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
    <aside
      aria-label="Lyrics"
      role={compact ? "dialog" : "complementary"}
      onKeyDown={handleKeyDown}
      className="doppler-side-panel flex w-[21rem] min-w-[18rem] max-w-[22rem] shrink-0 flex-col border-l border-[var(--color-border)] bg-[var(--color-bg-elevated)]"
    >
      <header className="flex h-[54px] shrink-0 items-center gap-2 border-b border-[var(--color-border)] px-4">
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold tracking-[-0.01em]">
            Lyrics
          </div>
          {current && (
            <div className="truncate text-[11px] leading-4 text-[var(--color-text-muted)]">
              {current.title}
            </div>
          )}
        </div>
        {data?.source && !editing && (
          <button
            type="button"
            onClick={startEditing}
            className="doppler-control h-7 rounded-md px-2 text-[11px] font-medium"
            title="Paste your own lyrics for this track"
          >
            Edit
          </button>
        )}
        <button
          ref={closeButtonRef}
          type="button"
          onClick={onClose}
          aria-label="Close lyrics"
          className="doppler-control grid h-7 w-7 place-items-center rounded-md"
        >
          <Icon name="close" size={14} />
        </button>
      </header>
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-6 py-7 text-[13px]"
      >
        {!current ? (
          <div className="grid min-h-40 place-items-center text-center text-[var(--color-text-muted)]">
            <div>
              <Icon name="lyrics" size={24} className="mx-auto mb-2 opacity-45" />
              <p className="font-medium text-[var(--color-text)]">
                Nothing playing
              </p>
              <p className="mt-1 text-[11px]">Lyrics will appear here.</p>
            </div>
          </div>
        ) : loading ? (
          <p className="text-[var(--color-text-muted)]">Loading…</p>
        ) : error ? (
          <p className="text-[var(--color-danger)]">{error}</p>
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
          <ol className="flex flex-col gap-3.5 leading-relaxed">
            {lines.map((line, i) => {
              const active = i === activeIndex;
              return (
                <li
                  key={`${line.tMs}-${i}`}
                  data-lyric-index={i}
                >
                  <button
                    type="button"
                    onClick={() => seek(line.tMs / 1000)}
                    aria-label={`Seek to ${Math.floor(line.tMs / 60_000)}:${Math.floor(
                      (line.tMs % 60_000) / 1_000,
                    )
                      .toString()
                      .padStart(2, "0")}`}
                    className={`w-full cursor-pointer rounded-sm text-left transition-all ${
                      active
                        ? "text-[15px] font-semibold text-[var(--color-text)]"
                        : i < activeIndex
                          ? "text-[var(--color-text-muted)]/40"
                          : "text-[var(--color-text-muted)]/70 hover:text-[var(--color-text)]"
                    }`}
                  >
                    {line.text || " "}
                  </button>
                </li>
              );
            })}
          </ol>
        ) : (
          <pre className="whitespace-pre-wrap font-sans leading-relaxed text-[var(--color-text-muted)]">
            {data.lrc}
          </pre>
        )}
        {data?.stale && !editing && (
          <p className="mt-4 text-[11px] text-[var(--color-text-muted)]">
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
    <div className="flex flex-col items-center gap-3 py-12 text-center">
      <Icon name="lyrics" size={24} className="opacity-45" />
      <p className="text-[var(--color-text-muted)]">
        {stale
          ? "Couldn't reach LRCLIB and there's nothing cached for this track."
          : "No lyrics found on LRCLIB for this track."}
      </p>
      <button
        type="button"
        onClick={onAdd}
        className="doppler-control rounded-md border border-[var(--color-border)] px-3 py-1.5 text-[11px] font-medium"
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
        className="w-full rounded-md border border-[var(--color-border)] bg-[var(--color-content)] px-3 py-2 font-mono text-[12px] text-[var(--color-text)] shadow-inner focus:border-[var(--color-accent)] focus:outline-none"
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
          type="button"
          onClick={onCancel}
          className="doppler-control rounded-md px-3 py-1.5 text-[11px]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={!draft.trim()}
          className="rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-40"
        >
          Save
        </button>
      </div>
    </div>
  );
}
