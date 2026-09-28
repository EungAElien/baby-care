"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { EmptyState, ErrorState, LoadingState, ScreenSection } from "@/components/screen-state";
import { useCareEventDeletionQuery, useRetryCareEventDeletionMutation } from "@/lib/api/care-events";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useRealSession } from "@/lib/auth/real-session";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";

type RetryRequest = Readonly<{ deletionJobId: string; expectedAttempt: number; clientRequestId: string }>;

const statusLabel = {
  PENDING: "접수됨 · 정리 대기",
  RUNNING: "정리 중",
  FAILED: "일부 자료 정리 중 · 재시도 가능",
  COMPLETE: "삭제 완료",
} as const;

export default function CareEventDeletionPage() {
  const { deletionJobId } = useParams<{ deletionJobId: string }>();
  const real = useRealSession();
  const query = useCareEventDeletionQuery(deletionJobId, real.status === "signed-in");
  const retry = useRetryCareEventDeletionMutation();
  const [pendingRetry, setPendingRetry] = useState<RetryRequest | null>(null);
  const [retryError, setRetryError] = useState<unknown>(null);

  async function sendRetry(request: RetryRequest) {
    setRetryError(null);
    try {
      await retry.mutateAsync(request);
      setPendingRetry(null);
    } catch (error) {
      setRetryError(error);
      setPendingRetry(isCareEventOutcomeUnknown(error) ? request : null);
    }
  }

  if (real.status === "loading") return <LoadingState label="계정을 확인하고 있어요" />;
  if (real.status !== "signed-in") {
    return (
      <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-4 px-6 py-10">
        <EmptyState label="본인이 신청한 삭제 작업은 실제 계정으로 로그인한 뒤 확인할 수 있어요" action={<Link href="/login" className="text-primary">로그인</Link>} />
      </main>
    );
  }

  const job = query.data;
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-4 px-6 py-10">
      <Link href="/login" className="text-sm text-primary">← 아기 선택</Link>
      <h1 className="text-lg font-semibold text-foreground">생활 기록 삭제 작업</h1>
      {query.isLoading ? <LoadingState label="삭제 작업을 확인하고 있어요" /> : query.isError ? (
        <div className="flex flex-col gap-3">
          <ErrorState label={query.error instanceof ContractApiError && query.error.status === 404
            ? "찾을 수 없거나 접근할 수 없는 삭제 작업이에요."
            : "삭제 작업 상태를 확인하지 못했어요."} retryable />
          <button type="button" onClick={() => void query.refetch()} className="min-h-11 rounded-md border border-border px-4 text-sm">다시 조회</button>
        </div>
      ) : job ? (
        <ScreenSection title={statusLabel[job.status]}>
          <p className="text-sm text-foreground">
            {job.status === "COMPLETE"
              ? "서버가 이 삭제 작업의 완료를 반환했어요."
              : "이 기록과 연결된 자료의 접근은 차단됐지만, 전체 정리가 완료된 것은 아니에요."}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">작업 ID {job.deletion_job_id} · 시도 {job.attempt_no}회</p>
          {job.pending_categories.length > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">아직 처리할 범위: {job.pending_categories.join(", ")}</p>
          )}
          {job.status !== "COMPLETE" && (
            <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching} className="mt-3 min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
              {query.isFetching ? "조회 중" : "작업 상태 다시 조회"}
            </button>
          )}
          {job.status === "FAILED" && !pendingRetry && (
            <button
              type="button"
              onClick={() => void sendRetry({ deletionJobId, expectedAttempt: job.attempt_no, clientRequestId: newClientRequestId() })}
              disabled={retry.isPending}
              className="mt-3 min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50"
            >
              {retry.isPending ? "재시도 확인 중" : "같은 삭제 작업 재시도"}
            </button>
          )}
        </ScreenSection>
      ) : <ErrorState label="삭제 작업을 확인할 수 없어요." />}

      {retryError !== null && (
        <div className="flex flex-col gap-2">
          <ErrorState label={pendingRetry
            ? "재시도 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요."
            : "재시도하지 못했어요. 작업 상태를 다시 조회해 주세요."} retryable={pendingRetry !== null} />
          {pendingRetry && (
            <button type="button" onClick={() => void sendRetry(pendingRetry)} disabled={retry.isPending} className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
              같은 재시도 요청으로 결과 확인
            </button>
          )}
        </div>
      )}
    </main>
  );
}
