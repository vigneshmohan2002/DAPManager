import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { SmartField, SmartOp, SmartRule, SmartRuleset } from "../lib/api";
import {
  OP_LABELS,
  SMART_FIELDS,
  defaultRule,
  isNumericField,
  isRulesetValid,
  opsForField,
  reconcileRule,
} from "../lib/smartRules";

type Mode =
  | { kind: "create" }
  | { kind: "edit"; playlistId: string; nameLocked: true };

type Props = {
  mode: Mode;
  initialName?: string;
  initialRules?: SmartRuleset | null;
  saving: boolean;
  // Returned ruleset is null when the "Smart playlist" toggle is off;
  // the parent decides whether to call createPlaylist (with null) or
  // updatePlaylistSmartRules (with null clears it). Name is always
  // returned so a single Save button can drive both create and edit.
  onSave: (name: string, ruleset: SmartRuleset | null) => void;
  onCancel: () => void;
};

export default function SmartPlaylistDialog({
  mode,
  initialName,
  initialRules,
  saving,
  onSave,
  onCancel,
}: Props) {
  const [name, setName] = useState(initialName ?? "");
  // Smart toggle defaults on iff we were given rules. Editing a static
  // playlist's rules isn't a flow we expose today, so edit mode always
  // arrives with rules.
  const [smart, setSmart] = useState<boolean>(Boolean(initialRules));
  const [match, setMatch] = useState<"all" | "any">(
    initialRules?.match ?? "all",
  );
  const [rules, setRules] = useState<SmartRule[]>(
    initialRules?.rules?.length ? initialRules.rules : [defaultRule()],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const trimmedName = name.trim();
  const ruleset: SmartRuleset = { match, rules };
  const canSave =
    !saving && trimmedName.length > 0 && (!smart || isRulesetValid(ruleset));

  const handleSave = () => {
    if (!canSave) return;
    onSave(trimmedName, smart ? ruleset : null);
  };

  const updateRule = (idx: number, patch: Partial<SmartRule>) => {
    setRules((prev) =>
      prev.map((r, i) => (i === idx ? reconcileRule({ ...r, ...patch }) : r)),
    );
  };

  const addRule = () => setRules((prev) => [...prev, defaultRule()]);
  const removeRule = (idx: number) =>
    setRules((prev) =>
      prev.length === 1 ? prev : prev.filter((_, i) => i !== idx),
    );

  return createPortal(
    <div
      className="fixed inset-0 z-[1200] flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-xl max-h-[85vh] overflow-y-auto rounded-lg bg-[var(--color-bg)] border border-[var(--color-border)] shadow-2xl"
      >
        <header className="px-6 py-4 border-b border-[var(--color-border)]">
          <h2 className="text-lg font-semibold">
            {mode.kind === "edit" ? "Edit smart playlist" : "New playlist"}
          </h2>
        </header>
        <div className="px-6 py-4 space-y-5">
          <div>
            <label className="block text-xs uppercase tracking-wider text-[var(--color-text-muted)] mb-1">
              Name
            </label>
            <input
              autoFocus={mode.kind !== "edit"}
              disabled={mode.kind === "edit"}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My playlist"
              className="w-full px-3 py-1.5 rounded-md bg-[var(--color-surface)] border border-[var(--color-border)] text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)] disabled:opacity-60"
            />
          </div>

          {mode.kind === "create" ? (
            <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
              <input
                type="checkbox"
                checked={smart}
                onChange={(e) => setSmart(e.target.checked)}
              />
              Smart playlist (auto-populated from rules)
            </label>
          ) : null}

          {smart ? (
            <div className="space-y-3 border border-[var(--color-border)] rounded-md p-3 bg-[var(--color-surface)]/40">
              <div className="flex items-center gap-3 text-sm">
                <span className="text-[var(--color-text-muted)]">Match</span>
                <label className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="radio"
                    checked={match === "all"}
                    onChange={() => setMatch("all")}
                  />
                  all rules
                </label>
                <label className="flex items-center gap-1 cursor-pointer">
                  <input
                    type="radio"
                    checked={match === "any"}
                    onChange={() => setMatch("any")}
                  />
                  any rule
                </label>
              </div>

              <div className="space-y-2">
                {rules.map((r, idx) => (
                  <RuleRow
                    key={idx}
                    rule={r}
                    onChange={(patch) => updateRule(idx, patch)}
                    onRemove={() => removeRule(idx)}
                    removable={rules.length > 1}
                  />
                ))}
              </div>

              <button
                onClick={addRule}
                className="text-xs text-[var(--color-accent)] hover:underline"
              >
                + Add rule
              </button>
            </div>
          ) : null}
        </div>
        <footer className="px-6 py-3 border-t border-[var(--color-border)] flex items-center justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={saving}
            className="px-3 py-1.5 rounded-md bg-[var(--color-surface)] text-sm text-[var(--color-text)] border border-[var(--color-border)] disabled:opacity-50 hover:bg-[var(--color-surface)]/70"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave}
            className="px-4 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-bg)] text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed hover:brightness-110"
          >
            {saving ? "Saving…" : mode.kind === "edit" ? "Save rules" : "Create"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

function RuleRow({
  rule,
  onChange,
  onRemove,
  removable,
}: {
  rule: SmartRule;
  onChange: (patch: Partial<SmartRule>) => void;
  onRemove: () => void;
  removable: boolean;
}) {
  const ops = opsForField(rule.field);
  const numeric = isNumericField(rule.field);
  return (
    <div className="flex items-center gap-2 text-sm">
      <select
        value={rule.field}
        onChange={(e) => onChange({ field: e.target.value as SmartField })}
        className="px-2 py-1 rounded bg-[var(--color-bg)] border border-[var(--color-border)]"
      >
        {SMART_FIELDS.map((f) => (
          <option key={f.field} value={f.field}>
            {f.label}
          </option>
        ))}
      </select>
      <select
        value={rule.op}
        onChange={(e) => onChange({ op: e.target.value as SmartOp })}
        className="px-2 py-1 rounded bg-[var(--color-bg)] border border-[var(--color-border)]"
      >
        {ops.map((op) => (
          <option key={op} value={op}>
            {OP_LABELS[op]}
          </option>
        ))}
      </select>
      <input
        type={numeric ? "number" : "text"}
        value={String(rule.value ?? "")}
        onChange={(e) => onChange({ value: e.target.value })}
        placeholder={numeric ? "0" : "value"}
        className="flex-1 px-2 py-1 rounded bg-[var(--color-bg)] border border-[var(--color-border)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
      />
      <button
        onClick={onRemove}
        disabled={!removable}
        title={removable ? "Remove rule" : "At least one rule is required"}
        className="px-2 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-accent)] disabled:opacity-30"
      >
        ✕
      </button>
    </div>
  );
}
