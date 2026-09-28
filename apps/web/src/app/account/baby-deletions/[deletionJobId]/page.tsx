"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ScreenSection } from "@/components/screen-state";
import { useApiClient } from "@/lib/api/real-client";
import { getBabyDeletion, retryBabyDeletion } from "@/lib/api/baby-deletion";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useRealSession } from "@/lib/auth/real-session";

const statusLabel = {
  PENDING: "삭제 접수 · 정리 대기", RUNNING: "자료 정리 중",
  FAILED: "일부 자료 정리 실패", COMPLETE: "삭제 완료",
} as const;

export default function BabyDeletionPage() {
  const { deletionJobId } = useParams<{ deletionJobId: string }>();
  const real = useRealSession();
  const client = useApiClient();
  const [retryRequest, setRetryRequest] = useState<{ attempt: number; requestId: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["private", real.userId, "baby-deletion", deletionJobId],
    queryFn: () => {
      if (!client || !real.userId) throw new Error("A requester session is required.");
      return getBabyDeletion(client, deletionJobId, real.userId);
    },
    enabled: real.status === "signed-in" && !!client,
  });

  async function retry(attempt: number, requestId: string) {
    if (!client || !real.userId) return;
    setBusy(true);
    setError(null);
    setRetryRequest({ attempt, requestId });
    try {
      await retryBabyDeletion(client, deletionJobId, real.userId, attempt, requestId);
      setRetryRequest(null);
      void query.refetch();
    } catch (failure) {
      if (failure instanceof ContractApiError && failure.kind === "version-conflict") {
        setRetryRequest(null);
        void query.refetch();
        setError("삭제 작업 상태가 바뀌었어요. 최신 상태를 확인한 뒤 다시 요청해 주세요.");
      } else setError("재시도 결과를 확인하지 못했어요. 같은 요청으로 다시 확인할 수 있어요.");
    } finally {
      setBusy(false);
    }
  }

  if (real.status === "loading") return <main className="p-6">계정을 확인하고 있어요.</main>;
  if (real.status !== "signed-in") return <main className="p-6"><Link href="/login" className="text-primary">로그인 후 본인 삭제 작업 확인</Link></main>;
  const job = query.data;
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-4 px-6 py-10">
      <Link href="/account" className="text-sm text-primary">← 내 계정</Link>
      <h1 className="text-lg font-semibold text-foreground">아기 전체 자료 삭제 작업</h1>
      {query.isLoading && <p className="text-sm">삭제 작업 상태를 확인하고 있어요.</p>}
      {query.isError && (
        <ScreenSection title="작업 상태를 확인할 수 없어요">
          <p className="text-sm">{query.error instanceof ContractApiError && query.error.status === 404
            ? "찾을 수 없거나 접근할 수 없는 작업이에요." : "조회 결과를 확인하지 못했어요."}</p>
          <button type="button" onClick={() => void query.refetch()} className="min-h-11 text-sm">다시 조회</button>
        </ScreenSection>
      )}
      {job && (
        <ScreenSection title={statusLabel[job.status]}>
          <p className="text-sm">{job.status === "COMPLETE"
            ? "서버가 전체 정리 완료를 반환했어요." : "접근은 차단됐으며 전체 정리는 아직 완료되지 않았어요."}</p>
          {job.pending_categories.length > 0 && <p className="text-xs text-muted-foreground">남은 범위: {job.pending_categories.join(", ")}</p>}
          {job.status !== "COMPLETE" && <button type="button" onClick={() => void query.refetch()} disabled={query.isFetching}
            className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">작업 상태 다시 조회</button>}
          {job.status === "FAILED" && (
            <button type="button" disabled={busy} onClick={() => void retry(retryRequest?.attempt ?? job.attempt_no,
              retryRequest?.requestId ?? newClientRequestId())}
              className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
              {retryRequest ? "같은 재시도 요청 확인" : "남은 정리 재시도"}
            </button>
          )}
        </ScreenSection>
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </main>
  );
}
