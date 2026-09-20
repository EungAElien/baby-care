// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CareEventDelete } from "../src/components/care-event-delete";
import type { CareEvent, DeleteCareEventRequest, DeletionJob } from "../src/lib/api/care-events";
import { ContractApiError } from "../src/lib/api/errors";
import type { ApiErrorEnvelope } from "../src/lib/api/errors";

const mocks = vi.hoisted(() => ({
  mutateAsync: vi.fn(),
  getAfterConflict: vi.fn(),
  getCareEvent: vi.fn(),
  push: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/components/app-providers", () => ({
  usePrivateScope: () => ({ snapshot: () => ({ userId: "synthetic-user", babyId: "synthetic-baby" }), assertCurrent: () => {} }),
}));
vi.mock("@/lib/api/real-client", () => ({ useApiClient: () => ({}) }));
vi.mock("@/lib/api/care-events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/api/care-events")>();
  return {
    ...actual,
    useDeleteCareEventMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }),
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
const job: DeletionJob = {
  deletion_job_id: "10000000-0000-4000-8000-000000000841",
  requester_user_id: saved.created_by_user_id,
  baby_id: saved.baby_id,
  scope: "CARE_EVENT",
  resource_id: saved.care_event_id,
  status: "PENDING",
  access_blocked: true,
  requested_at: "2026-09-19T09:00:00Z",
  completed_at: null,
  failure: null,
  pending_categories: ["RECORDS"],
  attempt_no: 1,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

function renderDelete(onAccessLost = vi.fn()) {
  render(<CareEventDelete
    babyId={saved.baby_id}
    baseline={saved}
    memberNames={new Map()}
    onCancel={vi.fn()}
    onAccessLost={onAccessLost}
  />);
  return onAccessLost;
}

describe("A-04 ② deletion confirmation", () => {
  it("requires new confirmation after a version conflict and follows the returned job", async () => {
    mocks.mutateAsync.mockRejectedValueOnce(new ContractApiError(409, conflictEnvelope, new Headers()))
      .mockResolvedValueOnce({ job });
    mocks.getAfterConflict.mockResolvedValueOnce({ ...saved, version: 2 });
    renderDelete();

    fireEvent.click(screen.getByRole("button", { name: "이 기록 삭제 요청" }));
    await screen.findByRole("button", { name: "이 최신 기록도 삭제 요청" });
    expect(mocks.mutateAsync).toHaveBeenCalledTimes(1);
    expect(mocks.getAfterConflict).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "이 최신 기록도 삭제 요청" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith(`/account/care-event-deletions/${job.deletion_job_id}`));
    const first = mocks.mutateAsync.mock.calls[0]![0] as DeleteCareEventRequest;
    const second = mocks.mutateAsync.mock.calls[1]![0] as DeleteCareEventRequest;
    expect(first.version).toBe(1);
    expect(second.version).toBe(2);
    expect(second.clientRequestId).not.toBe(first.clientRequestId);
  });

  it("hides the conflict resource if the authorized follow-up read returns 404", async () => {
    const onAccessLost = vi.fn();
    mocks.mutateAsync.mockRejectedValueOnce(new ContractApiError(409, conflictEnvelope, new Headers()));
    mocks.getAfterConflict.mockRejectedValueOnce(new ContractApiError(404, {
      code: "RESOURCE_NOT_FOUND", message: "Not found", retryable: false, request_id: "synthetic",
      field_errors: [], details: {},
    }, new Headers()));
    renderDelete(onAccessLost);
    fireEvent.click(screen.getByRole("button", { name: "이 기록 삭제 요청" }));
    await waitFor(() => expect(onAccessLost).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "이 최신 기록도 삭제 요청" })).toBeNull();
  });
});
