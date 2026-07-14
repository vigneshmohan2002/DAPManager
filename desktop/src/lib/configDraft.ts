import type { ConfigValue } from "./api";

export function coerceConfigDraftValue(
  input: string,
  original: ConfigValue | undefined,
): ConfigValue {
  if (typeof original !== "number" || input.trim() === "") return input;

  const numericValue = Number(input);
  if (Number.isNaN(numericValue)) return input;
  return numericValue;
}

export function buildConfigPatch(
  draft: Readonly<Record<string, ConfigValue>>,
  original: Readonly<Record<string, ConfigValue>>,
  booleanKeys: ReadonlySet<string>,
): Record<string, ConfigValue> {
  return Object.fromEntries(
    Object.entries(draft).map(([key, value]) => {
      if (booleanKeys.has(key) || typeof value !== "string") {
        return [key, value];
      }
      return [key, coerceConfigDraftValue(value, original[key])];
    }),
  );
}
