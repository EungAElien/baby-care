// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { dateInTimezone } from "../src/components/real-summary";

describe("summary's baby-local date", () => {
  it("uses the baby timezone across the UTC day boundary", () => {
    const now = new Date("2026-09-20T00:30:00Z");
    expect(dateInTimezone("Asia/Seoul", now)).toBe("2026-09-20");
    expect(dateInTimezone("America/Los_Angeles", now)).toBe("2026-09-19");
  });
  it("keeps local midnight correct across daylight-saving changes", () => {
    expect(
      dateInTimezone("America/New_York", new Date("2026-11-01T04:30:00Z")),
    ).toBe("2026-11-01");
  });
});
