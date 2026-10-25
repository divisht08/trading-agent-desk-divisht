import { describe, expect, it } from "vitest";
import { hhmm, pct, signed, tone } from "./format";

describe("format", () => {
  it("formats percentages with a sign", () => {
    expect(pct(1.234)).toBe("+1.23%");
    expect(pct(-0.5, 1)).toBe("-0.5%");
    expect(pct(null)).toBe("–");
  });
  it("signs rupee amounts", () => {
    expect(signed(-1500)).toBe("−₹1,500");
    expect(signed(0)).toBe("₹0");
  });
  it("picks a tone", () => {
    expect(tone(2)).toBe("up");
    expect(tone(-2)).toBe("down");
    expect(tone(0)).toBe("");
  });
  it("reads clock time from ISO", () => {
    expect(hhmm("2026-10-25T14:24:00+05:30")).toBe("14:24");
  });
});
