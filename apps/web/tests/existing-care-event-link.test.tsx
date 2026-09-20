// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExistingCareEventLink } from "../src/components/existing-care-event-link";
import type { CareEvent, LinkCareEventRequest } from "../src/lib/api/care-events";
import { NetworkRequestError } from "../src/lib/api/client";

const mocks = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/lib/api/care-events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/api/care-events")>();
  return { ...actual, useLinkExistingCareEventMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }) };
});

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const scenarios = JSON.parse(readFileSync(fixtureFile, "utf8")) as {
  scenarios: { name: string; response: { body: unknown } }[];
};
const saved = scenarios.scenarios.find((scenario) => scenario.name === "care_event_saved")!
  .response.body as CareEvent;
const episodeId = "10000000-0000-4000-8000-000000000301";

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("A-04 ② existing record linkage", () => {
  it("requires an existing episode ID and performed confirmation, then retries with the same request", async () => {
    mocks.mutateAsync.mockRejectedValueOnce(new NetworkRequestError()).mockResolvedValueOnce({
      action_id: "10000000-0000-4000-8000-000000000602",
    });
    render(<ExistingCareEventLink babyId={saved.baby_id} event={saved} />);
    const submit = () => screen.getByRole("button", { name: "기존 기록 연결" }) as HTMLButtonElement;
    expect(submit().disabled).toBe(true);
    fireEvent.input(screen.getByLabelText("기존 울음 사건 ID"), { target: { value: episodeId } });
    expect((screen.getByLabelText("기존 울음 사건 ID") as HTMLInputElement).value).toBe(episodeId);
    expect(submit().disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("이 기록은 실제 수행한 행동이에요"));
    expect((screen.getByLabelText("이 기록은 실제 수행한 행동이에요") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("기존 울음 사건 ID") as HTMLInputElement).value).toBe(episodeId);
    await waitFor(() => expect(submit().disabled).toBe(false));
    fireEvent.click(submit());
    await screen.findByRole("button", { name: "같은 연결 요청으로 결과 다시 확인" });
    fireEvent.click(screen.getByRole("button", { name: "같은 연결 요청으로 결과 다시 확인" }));
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(2));
    const first = mocks.mutateAsync.mock.calls[0]![0] as LinkCareEventRequest;
    expect(first.careEventId).toBe(saved.care_event_id);
    expect(first.episodeId).toBe(episodeId);
    expect(mocks.mutateAsync.mock.calls[1]![0]).toEqual(first);
    expect(screen.getByText(/생활 기록은 새로 생성하지 않았어요/)).toBeTruthy();
  });
});
