import { useEffect, useMemo, useState } from "react";
import TopBar from "../components/TopBar";
import { useToast } from "../components/Toast";
import {
  fetchDuplicates,
  resolveDuplicate,
  type DuplicateGroup,
} from "../lib/api";

type Props = {
  ready: boolean;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "loaded"; groups: DuplicateGroup[] }
  | { kind: "error"; message: string };

// Sentinel value for "skip this group" — empty string distinguishes
// from a real path. Mirrors the PySide6 dialog's skip radio.
const SKIP = "";

export default function DuplicatesScreen({ ready }: Props) {
  const [load, setLoad] = useState<LoadState>({ kind: "loading" });
  // mbid → chosen keep path, or SKIP. Seeded from each group's
  // recommended candidate when the data arrives.
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [resolving, setResolving] = useState(false);
  const toast = useToast();

  const loadGroups = async () => {
    try {
      const groups = await fetchDuplicates();
      setLoad({ kind: "loaded", groups });
      const seeded: Record<string, string> = {};
      for (const g of groups) {
        const rec =
          g.candidates.find((c) => c.is_recommended) ?? g.candidates[0];
        seeded[g.mbid] = rec ? rec.path : SKIP;
      }
      setPicks(seeded);
    } catch (e) {
      setLoad({ kind: "error", message: String(e) });
    }
  };

  useEffect(() => {
    if (ready) loadGroups();
  }, [ready]);

  const groups = load.kind === "loaded" ? load.groups : [];

  // A "plan" is a group with a real keep path AND at least one path
  // to delete. Groups with only one candidate (shouldn't happen) or
  // skipped ones get filtered.
  const plans = useMemo(() => {
    return groups.flatMap((g) => {
      const keep = picks[g.mbid];
      if (!keep || keep === SKIP) return [];
      const deletes = g.candidates
        .map((c) => c.path)
        .filter((p) => p !== keep);
      if (deletes.length === 0) return [];
      return [{ mbid: g.mbid, keep, deletes }];
    });
  }, [groups, picks]);

  const totalDeletes = plans.reduce((n, p) => n + p.deletes.length, 0);

  const handleResolve = async () => {
    if (plans.length === 0) return;
    if (
      !window.confirm(
        `Resolve ${plans.length} group${
          plans.length === 1 ? "" : "s"
        } and delete ${totalDeletes} file${
          totalDeletes === 1 ? "" : "s"
        } on disk? This cannot be undone.`,
      )
    )
      return;
    setResolving(true);
    let resolved = 0;
    let deletedTotal = 0;
    const errors: string[] = [];
    for (const p of plans) {
      const result = await resolveDuplicate(p.mbid, p.keep, p.deletes);
      if (!result.success) {
        errors.push(result.message || `${p.mbid}: failed`);
        continue;
      }
      resolved += 1;
      deletedTotal += result.deleted.length;
      errors.push(...result.errors);
    }
    setResolving(false);
    const summary = `Resolved ${resolved} group${
      resolved === 1 ? "" : "s"
    }; deleted ${deletedTotal} file${deletedTotal === 1 ? "" : "s"}.`;
    if (errors.length > 0) {
      toast.show(`${summary} ${errors.length} error(s).`, "err");
    } else {
      toast.show(summary);
    }
    loadGroups();
  };

  const subtitle =
    load.kind === "loading"
      ? "Loading…"
      : load.kind === "error"
      ? "Failed to load"
      : groups.length === 0
      ? "No duplicates"
      : `${groups.length} group${groups.length === 1 ? "" : "s"} · ${
          plans.length
        } selected`;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Duplicates" subtitle={subtitle} />
      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-6">
        <section className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleResolve}
            disabled={!ready || resolving || plans.length === 0}
            className="px-4 py-2 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            {resolving
              ? "Resolving…"
              : plans.length === 0
              ? "Resolve"
              : `Resolve ${plans.length} (${totalDeletes} file${
                  totalDeletes === 1 ? "" : "s"
                })`}
          </button>
          <p className="text-xs text-[var(--color-text-muted)] max-w-xl">
            Pick which copy to keep in each group. Other copies are deleted on
            disk; the catalog row is updated to point at the kept path. Set a
            group to <em>Skip</em> to leave it alone.
          </p>
          <button
            onClick={loadGroups}
            disabled={!ready || resolving}
            className="ml-auto px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] disabled:opacity-50 hover:bg-[var(--color-surface)]/70 border border-[var(--color-border)]"
          >
            Refresh
          </button>
        </section>

        <Body
          load={load}
          picks={picks}
          onPick={(mbid, keep) =>
            setPicks((prev) => ({ ...prev, [mbid]: keep }))
          }
        />
      </div>
    </div>
  );
}

function Body({
  load,
  picks,
  onPick,
}: {
  load: LoadState;
  picks: Record<string, string>;
  onPick: (mbid: string, keep: string) => void;
}) {
  if (load.kind === "loading") {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Scanning library…
      </div>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="text-sm text-[var(--color-accent)]">{load.message}</div>
    );
  }
  if (load.groups.length === 0) {
    return (
      <div className="text-sm text-[var(--color-text-muted)] italic">
        Nothing duplicated — every track has a single on-disk copy.
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {load.groups.map((g) => (
        <GroupCard
          key={g.mbid}
          group={g}
          pick={picks[g.mbid] ?? SKIP}
          onPick={(keep) => onPick(g.mbid, keep)}
        />
      ))}
    </div>
  );
}

function GroupCard({
  group,
  pick,
  onPick,
}: {
  group: DuplicateGroup;
  pick: string;
  onPick: (keep: string) => void;
}) {
  const groupName = `dupes-${group.mbid}`;
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]/40 px-4 py-3">
      <div className="text-sm text-[var(--color-text)] mb-2">
        <span className="font-medium">{group.artist}</span>
        <span className="text-[var(--color-text-muted)]"> — </span>
        <span>{group.title}</span>
      </div>
      <div className="space-y-1">
        {group.candidates.map((c) => (
          <label
            key={c.path}
            className="flex items-start gap-2 text-sm cursor-pointer py-1 px-2 rounded hover:bg-[var(--color-surface)]/40"
          >
            <input
              type="radio"
              name={groupName}
              checked={pick === c.path}
              onChange={() => onPick(c.path)}
              className="mt-1 accent-[var(--color-accent)]"
            />
            <span className="flex-1 min-w-0 break-all font-mono text-xs text-[var(--color-text)]">
              {c.path}
            </span>
            <span className="text-xs text-[var(--color-text-muted)] whitespace-nowrap">
              score {c.score}
              {c.is_recommended ? (
                <span className="ml-2 px-1.5 py-0.5 rounded bg-[var(--color-accent)]/20 text-[var(--color-accent)]">
                  recommended
                </span>
              ) : null}
            </span>
          </label>
        ))}
        <label className="flex items-center gap-2 text-sm cursor-pointer py-1 px-2 rounded hover:bg-[var(--color-surface)]/40 text-[var(--color-text-muted)]">
          <input
            type="radio"
            name={groupName}
            checked={pick === SKIP}
            onChange={() => onPick(SKIP)}
            className="accent-[var(--color-accent)]"
          />
          <span>Skip this group</span>
        </label>
      </div>
    </div>
  );
}
