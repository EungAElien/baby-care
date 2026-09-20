// @vitest-environment jsdom
import { createElement } from "react";
import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CareEventDeletionPage from "../src/app/account/care-event-deletions/[deletionJobId]/page";
import type { DeletionJob } from "../src/lib/api/care-events";
import { NetworkRequestError } from "../src/lib/api/client";

const deletionJobId = "10000000-0000-4000-8000-000000000841";
const pendingJob: DeletionJob = {
  deletion_job_id: deletionJobId,
  requester_user_id: "10000000-0000-4000-8000-000000000002",
  baby_id: "10000000-0000-4000-8000-000000000101",
  scope: "CARE_EVENT",
  resource_id: "10000000-0000-4000-8000-000000000601",
  status: "PENDING",
  access_blocked: true,
  requested_at: "2026-09-19T09:00:00Z",
  completed_at: null,
  failure: null,
  pending_categories: ["RECORDS"],
  attempt_no: 1,
};
const mocks = vi.hoisted(() => ({
  job: null as DeletionJob | null,
  mutateAsync: vi.fn(),
  refetch: vi.fn(),
}));
vi.mock("next/navigation", () => ({ useParams: () => ({ deletionJobId }) }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => createElement("a", { href }, children),
}));
vi.mock("@/lib/auth/real-session", () => ({
  useRealSession: () => ({ status: "signed-in", userId: "10000000-0000-4000-8000-000000000002" }),
}));
vi.mock("@/lib/api/care-events", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/api/care-events")>();
  return {
    ...actual,
    useCareEventDeletionQuery: () => ({ data: mocks.job, isLoading: false, isError: false, isFetching: false, refetch: mocks.refetch }),
    useRetryCareEventDeletionMutation: () => ({ mutateAsync: mocks.mutateAsync, isPending: false }),
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.job = pendingJob;
});
afterEach(cleanup);

describe("A-04 ② deletion job UI", () => {
  it("does not call a 202 PENDING job complete and allows manual status refresh", () => {
    render(<CareEventDeletionPage />);
    expect(screen.getByText("접수됨 · 정리 대기")).toBeTruthy();
    expect(screen.getByText(/전체 정리가 완료된 것은 아니에요/)).toBeTruthy();
    expect(screen.queryByText("삭제 완료")).toBeNull();
    expect(screen.queryByRole("button", { name: "같은 삭제 작업 재시도" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "작업 상태 다시 조회" }));
    expect(mocks.refetch).toHaveBeenCalledTimes(1);
  });

  it("offers retry only for FAILED and reuses the same request after an uncertain outcome", async () => {
    mocks.job = { ...pendingJob, status: "FAILED" };
    mocks.mutateAsync.mockRejectedValueOnce(new NetworkRequestError()).mockResolvedValueOnce({ job: { ...pendingJob, status: "RUNNING" } });
    render(<CareEventDeletionPage />);
    fireEvent.click(screen.getByRole("button", { name: "같은 삭제 작업 재시도" }));
    await screen.findByRole("button", { name: "같은 재시도 요청으로 결과 확인" });
    fireEvent.click(screen.getByRole("button", { name: "같은 재시도 요청으로 결과 확인" }));
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(2));
    expect(mocks.mutateAsync.mock.calls[0]![0]).toEqual(mocks.mutateAsync.mock.calls[1]![0]);
  });
});
