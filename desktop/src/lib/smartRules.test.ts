import { describe, expect, it } from "vitest";

import {
  SMART_FIELDS,
  isBooleanField,
  isRuleValid,
  opsForField,
  reconcileRule,
} from "./smartRules";

describe("smart playlist rule parity", () => {
  it("offers every server-supported field", () => {
    expect(SMART_FIELDS.map(({ field }) => field)).toEqual([
      "artist",
      "album",
      "title",
      "tag_tier",
      "tag_score",
      "genre",
      "is_liked",
    ]);
  });

  it("limits liked rules to boolean equality", () => {
    expect(isBooleanField("is_liked")).toBe(true);
    expect(opsForField("is_liked")).toEqual(["equals"]);
    expect(
      reconcileRule({ field: "is_liked", op: "contains", value: "yes" }),
    ).toEqual({ field: "is_liked", op: "equals", value: true });
  });

  it("accepts boolean liked values and normal genre text rules", () => {
    expect(
      isRuleValid({ field: "is_liked", op: "equals", value: false }),
    ).toBe(true);
    expect(
      isRuleValid({ field: "genre", op: "contains", value: "neo soul" }),
    ).toBe(true);
  });
});
