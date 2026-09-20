// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CareEventEdit } from "../src/components/care-event-edit";
import { NetworkRequestError } from "../src/lib/api/client";
import { ContractApiError } from "../src/lib/api/errors";
import type { ApiErrorEnvelope } from "../src/lib/api/errors";
import type { CareEvent, PatchCareEventRequest } from "../src/lib/api/care-events";

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  getAfterConflict: vi.fn(),
  getCareEvent: vi.fn(),
  setDirty: vi.fn(),
}));
vi.mock("@/components/app-providers", () => ({
  usePrivateScope: () => ({ snapshot: () => ({ userId: "synthetic-user", babyId: "synthetic-baby" }), assertCurrent: () => {} }),
}));
vi.mock("@/lib/api/real-client", () => ({ useApiClient: () => ({}) }));
vi.mock("@/lib/mock/draft", () => ({ useDraft: () => ({ setCareEventDirty: mocks.setDirty }) }));
vi.mock("@/lib/api/care-events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/api/care-events")>();
  return {
    ...actual,
    usePatchCareEventMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }),
    getCareEventAfterConflict: mocks.getAfterConflict,
    getCareEvent: mocks.getCareEvent,
  };
});

const fixtureFile = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const scenarios = JSON.parse(readFileSync(fixtureFile, "utf8")) as {
  scenarios: { name: string; response: { body: unknown } }[];
};
const saved = scenarios.scenarios.find((scenario) => scenario.name === "care_event_saved")!
  .response.body as CareEvent;
const conflictEnvelope = scenarios.scenarios.find((scenario) => scenario.name === "edit_conflict")!
  .response.body as ApiErrorEnvelope;
const conflictError = () => new ContractApiError(409, conflictEnvelope, new Headers());
const notFound = () => new ContractApiError(404, {
  code: "RESOURCE_NOT_FOUND", message: "Not found", retryable: false, request_id: "synthetic",
  field_errors: [], details: {},
}, new Headers());

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

function renderEdit(onDone = vi.fn(), onAccessLost = vi.fn()) {
  render(<CareEventEdit
    babyId={saved.baby_id}
    baseline={saved}
    memberNames={new Map()}
    onDone={onDone}
    onCancel={vi.fn()}
    onAccessLost={onAccessLost}
  />);
  fireEvent.change(screen.getByLabelText(/양 \(mL/), { target: { value: "90" } });
  return { onDone, onAccessLost };
}

describe("A-04 ② edit conflict presentation", () => {
  it("compares the local draft with an authorized fresh GET and requires a new explicit save", async () => {
    const latest: CareEvent = {
      ...saved, version: 2,
      event: { type: "FEEDING", occurred_at: saved.event.occurred_at, ended_at: null,
        time_precision: "EXACT", payload: { mode: "FORMULA", amount_ml: 100, duration_minutes: null } },
    };
    mocks.mutateAsync.mockRejectedValueOnce(conflictError()).mockResolvedValueOnce({ event: latest });
    mocks.getAfterConflict.mockResolvedValue(latest);
    const { onDone } = renderEdit();

    fireEvent.click(screen.getByRole("button", { name: "수정 저장" }));
    await screen.findByText("내 미저장 수정안 · 읽은 version 1");
    expect(screen.getByText(/90mL/)).toBeTruthy();
    expect(screen.getByText(/100mL/)).toBeTruthy();
    expect(mocks.getAfterConflict).toHaveBeenCalledTimes(1);
    expect(mocks.mutateAsync).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "최신 version으로 내 수정안을 다시 저장" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    const first = mocks.mutateAsync.mock.calls[0]![0] as PatchCareEventRequest;
    const second = mocks.mutateAsync.mock.calls[1]![0] as PatchCareEventRequest;
    expect(first.version).toBe(1);
    expect(second.version).toBe(2);
    expect(second.clientRequestId).not.toBe(first.clientRequestId);
    expect(second.event).toMatchObject({ payload: { amount_ml: 90 } });
  });

  it("does not show the 409 resource when a fresh GET loses permission", async () => {
    mocks.mutateAsync.mockRejectedValueOnce(conflictError());
    mocks.getAfterConflict.mockRejectedValueOnce(notFound());
    const { onAccessLost } = renderEdit();
    fireEvent.click(screen.getByRole("button", { name: "수정 저장" }));
    await waitFor(() => expect(onAccessLost).toHaveBeenCalledTimes(1));
    expect(screen.queryByText("현재 조회 권한으로 다시 확인한 최신 서버 기록")).toBeNull();
  });

  it("retries an uncertain PATCH with the same key, version, and body", async () => {
    mocks.mutateAsync.mockRejectedValueOnce(new NetworkRequestError()).mockResolvedValueOnce({ event: saved });
    const { onDone } = renderEdit();
    fireEvent.click(screen.getByRole("button", { name: "수정 저장" }));
    await screen.findByRole("button", { name: "같은 수정 요청으로 결과 다시 확인" });
    fireEvent.click(screen.getByRole("button", { name: "같은 수정 요청으로 결과 다시 확인" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1));
    expect(mocks.mutateAsync.mock.calls[0]![0]).toEqual(mocks.mutateAsync.mock.calls[1]![0]);
  });
});
