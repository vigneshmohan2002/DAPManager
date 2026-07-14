import { describe, expect, it } from "vitest";
import { buildConfigPatch, coerceConfigDraftValue } from "./configDraft";

describe("coerceConfigDraftValue", () => {
  it("coerces valid numeric strings only when the original value was numeric", () => {
    expect(coerceConfigDraftValue(" 42.5 ", 10)).toBe(42.5);
    expect(coerceConfigDraftValue("42", "10")).toBe("42");
  });

  it("preserves blank and invalid numeric drafts as strings", () => {
    expect(coerceConfigDraftValue("", 10)).toBe("");
    expect(coerceConfigDraftValue("many", 10)).toBe("many");
  });
});

describe("buildConfigPatch", () => {
  it("coerces numeric fields while preserving booleans and non-string values", () => {
    expect(
      buildConfigPatch(
        {
          timeout: "45",
          enabled: true,
          nullable: null,
          label: "45",
        },
        {
          timeout: 30,
          enabled: false,
          nullable: "previous",
          label: "previous",
        },
        new Set(["enabled"]),
      ),
    ).toEqual({
      timeout: 45,
      enabled: true,
      nullable: null,
      label: "45",
    });
  });
});
