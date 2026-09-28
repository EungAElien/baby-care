import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CareEventCard } from "../src/components/care-event-card";
import type { CareEvent } from "../src/lib/api/care-events";

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const fixtures = JSON.parse(readFileSync(fixtureFile, "utf8")) as {
  scenarios: { name: string; response: { body: unknown } }[];
};
const example = fixtures.scenarios.find((scenario) => scenario.name === "care_event_saved")!.response.body as CareEvent;

describe("A-04 ① CareEvent display", () => {
  it("shows the supplied fixture as example data with its author and amount", () => {
    const html = renderToStaticMarkup(
      <CareEventCard
        event={example}
        babyId={example.baby_id}
        memberNames={new Map([[example.created_by_user_id, "예시 보호자"]])}
      />,
    );
    expect(html).toContain("80mL");
    expect(html).toContain("예시 보호자");
    expect(html).toContain("예시 자료");
  });

  it("does not replace unknown actual time or amount with recorded time or zero", () => {
    const event: CareEvent = {
      ...example,
      event: {
        type: "FEEDING",
        occurred_at: null,
        ended_at: null,
        time_precision: "UNKNOWN",
        payload: { mode: "FORMULA", amount_ml: null, duration_minutes: null },
      },
    };
    const html = renderToStaticMarkup(<CareEventCard event={event} babyId={event.baby_id} />);
    expect(html).toContain("실제 시각 모름");
    expect(html).toContain("양 모름");
    expect(html).not.toContain("0mL");
  });
});
