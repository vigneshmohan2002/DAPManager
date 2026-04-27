import type { SmartField, SmartOp, SmartRule, SmartRuleset } from "./api";

// Mirrors src/smart_playlist.py's _FIELDS / _TEXT_OPS / _NUMERIC_OPS
// whitelist. The server re-validates, so this is for UI gating only —
// out-of-whitelist combinations would 400 anyway.
export const SMART_FIELDS: Array<{ field: SmartField; label: string }> = [
  { field: "artist", label: "Artist" },
  { field: "album", label: "Album" },
  { field: "title", label: "Title" },
  { field: "tag_tier", label: "Tag tier" },
  { field: "tag_score", label: "Tag score" },
];

const TEXT_OPS: SmartOp[] = ["contains", "equals", "starts_with", "ends_with"];
const NUMERIC_OPS: SmartOp[] = ["gt", "lt", "equals"];
const NUMERIC_FIELDS: SmartField[] = ["tag_score"];

export const OP_LABELS: Record<SmartOp, string> = {
  contains: "contains",
  equals: "equals",
  starts_with: "starts with",
  ends_with: "ends with",
  gt: ">",
  lt: "<",
};

export function isNumericField(field: SmartField): boolean {
  return NUMERIC_FIELDS.includes(field);
}

export function opsForField(field: SmartField): SmartOp[] {
  return isNumericField(field) ? NUMERIC_OPS : TEXT_OPS;
}

export function defaultOpForField(field: SmartField): SmartOp {
  return opsForField(field)[0];
}

export function defaultRule(): SmartRule {
  return { field: "artist", op: "contains", value: "" };
}

// Coerce a rule's op to one that's valid for its field — used when
// the user changes the field after picking an op (e.g., text "contains"
// → numeric field forces "gt").
export function reconcileRule(rule: SmartRule): SmartRule {
  const validOps = opsForField(rule.field);
  if (validOps.includes(rule.op)) return rule;
  return { ...rule, op: defaultOpForField(rule.field) };
}

// True iff the ruleset would be accepted by the server. Used to gate
// the Save button so we don't roundtrip to get a 400. Empty rules are
// considered invalid here (the server would store NULL and resolve to
// "no tracks", which is not what the user typing in a dialog wants).
export function isRulesetValid(ruleset: SmartRuleset): boolean {
  if (!ruleset.rules.length) return false;
  return ruleset.rules.every((r) => isRuleValid(r));
}

export function isRuleValid(rule: SmartRule): boolean {
  if (!opsForField(rule.field).includes(rule.op)) return false;
  const trimmed = String(rule.value ?? "").trim();
  if (!trimmed) return false;
  if (isNumericField(rule.field)) {
    return Number.isFinite(Number(trimmed));
  }
  // Text fields: empty string is meaningless ("contains nothing"
  // matches everything, which isn't a useful rule). Server tolerates
  // it but UX-wise we shouldn't let it through.
  return true;
}
