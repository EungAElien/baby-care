// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SourceBadge } from "../src/components/source-badge";
import { feedingAmount } from "../src/components/summary-overview";
import { mockDailySummary } from "../src/lib/mock/fixtures";

afterEach(cleanup);
describe("source and missing-data semantics", () => {
  it("does not promote REAL configuration into proof of model execution", () => {
    const view = render(<SourceBadge inferenceMode="REAL" dataOrigin="USER" />);
    expect(screen.getByText("모델 실행 여부 미확인")).toBeTruthy();
    view.rerender(
      <SourceBadge
        inferenceMode="REAL"
        inferenceExecuted={false}
        dataOrigin="DEMO"
      />,
    );
    expect(screen.getByText("예시 음원 · 모델 미실행")).toBeTruthy();
    view.rerender(
      <SourceBadge inferenceMode="REAL" inferenceExecuted dataOrigin="USER" />,
    );
    expect(screen.getByText("실제 모델 실행")).toBeTruthy();
  });
  it("keeps absence, unknown quantity and recorded zero separate", () => {
    const base = mockDailySummary("summary_empty");
    expect(feedingAmount(base)).toBe("기록 없음");
    expect(
      feedingAmount({
        ...base,
        feeding: { ...base.feeding, record_count: 1, total_recorded_ml: null },
      }),
    ).toBe("양 정보 부족");
    expect(
      feedingAmount({
        ...base,
        feeding: {
          ...base.feeding,
          record_count: 1,
          known_amount_count: 1,
          total_recorded_ml: 0,
        },
      }),
    ).toBe("0 mL");
  });
});

function luminance(hex: string) {
  const linear = hex.match(/[a-f\d]{2}/gi)!.map((byte) => {
    const n = parseInt(byte, 16) / 255;
    return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
  });
  return linear[0]! * 0.2126 + linear[1]! * 0.7152 + linear[2]! * 0.0722;
}
function contrast(first: string, second: string) {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0]! + 0.05) / (values[1]! + 0.05);
}
describe("brand contrast and prohibited surfaces", () => {
  const css = readFileSync(resolve("src/app/globals.css"), "utf8");
  const colors = Object.fromEntries(
    [...css.matchAll(/--palette-([\w-]+):\s*(#[a-f\d]{6});/gi)].map((match) => [
      match[1],
      match[2]!,
    ]),
  );
  it.each([
    ["black", "white"],
    ["black", "pink"],
    ["black", "brick"],
    ["white", "navy"],
    ["white", "cobalt"],
    ["black", "sky"],
    ["white", "wine"],
    ["gray-700", "white"],
    ["gray-700", "gray-50"],
    ["white", "pressed"],
    ["soft-pink", "navy"],
    ["white", "panel"],
    ["white", "panel-subtle"],
    ["white", "panel-pressed"],
    ["black", "soft-pink"],
  ])("%s on %s meets normal-text contrast", (fg, bg) => {
    expect(contrast(colors[fg]!, colors[bg]!)).toBeGreaterThanOrEqual(4.5);
  });
  it("focus and control outlines meet 3:1, including focus on pink", () => {
    expect(contrast(colors.cobalt!, colors.pink!)).toBeGreaterThanOrEqual(3);
    expect(contrast(colors["gray-500"]!, colors.white!)).toBeGreaterThanOrEqual(
      3,
    );
  });
  it("does not merge image samples with brand values or include an ivory surface", () => {
    expect(colors.pink!.toLowerCase()).toBe("#ff9398");
    expect(colors.brick!.toLowerCase()).toBe("#d14836");
    expect(colors["soft-pink"]!.toLowerCase()).toBe("#fda1a2");
    expect(colors.coral!.toLowerCase()).toBe("#f03b34");
    expect(css).not.toMatch(/#fefaf1|beige|ivory|cream/i);
  });
});
