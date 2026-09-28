"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivateScope } from "@/components/app-providers";
import { CareEventCard } from "@/components/care-event-card";
import { ErrorState, ScreenSection } from "@/components/screen-state";
import { getCareEvent, getCareEventAfterConflict, useDeleteCareEventMutation } from "@/lib/api/care-events";
import type { CareEvent, DeleteCareEventRequest } from "@/lib/api/care-events";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useApiClient } from "@/lib/api/real-client";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";

type DeleteConflict = Readonly<{ latest: CareEvent | null; readFailed: boolean }>;

function deleteErrorMessage(error: unknown): string {
  if (error instanceof ContractApiError) {
    switch (error.envelope.code) {
      case "AUTHOR_ONLY": return "이 기록을 삭제할 권한이 없어요.";
      case "RESOURCE_NOT_FOUND": return "찾을 수 없거나 접근할 수 없어요.";
      case "RESOURCE_DELETING": return "이미 삭제 처리 중인 기록이에요. 새 삭제 요청을 보내지 않았어요.";
      case "IDEMPOTENCY_KEY_REUSED": return "같은 요청 키에 다른 내용이 사용됐어요. 자동으로 다시 보내지 않았어요.";
      default: return error.envelope.message;
    }
  }
  if (isCareEventOutcomeUnknown(error)) return "삭제 요청 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.";
  return "삭제를 요청하지 못했어요. 접속 상태를 확인해 주세요.";
}

export function CareEventDelete({
  babyId, baseline, memberNames, onCancel, onAccessLost,
}: Readonly<{
  babyId: string;
  baseline: CareEvent;
  memberNames: ReadonlyMap<string, string>;
  onCancel: () => void;
  onAccessLost: () => void;
}>) {
  const router = useRouter();
  const client = useApiClient();
  const scope = usePrivateScope();
  const mutation = useDeleteCareEventMutation();
  const [pendingRequest, setPendingRequest] = useState<DeleteCareEventRequest | null>(null);
  const [conflict, setConflict] = useState<DeleteConflict | null>(null);
  const [deleteError, setDeleteError] = useState<unknown>(null);

  async function readLatest(error?: unknown) {
    setConflict({ latest: null, readFailed: false });
    if (!client) {
      setConflict({ latest: null, readFailed: true });
      return;
    }
    const snapshot = scope.snapshot();
    try {
      const latest = error === undefined
        ? await getCareEvent(client, babyId, baseline.care_event_id)
        : await getCareEventAfterConflict(client, babyId, baseline.care_event_id, error);
      scope.assertCurrent(snapshot);
      setConflict({ latest, readFailed: false });
    } catch (readError) {
      if (readError instanceof ContractApiError && (readError.status === 404 || readError.status === 403)) {
        onAccessLost();
        return;
      }
      setConflict({ latest: null, readFailed: true });
    }
  }

  async function send(request: DeleteCareEventRequest) {
    setDeleteError(null);
    setConflict(null);
    try {
      const { job } = await mutation.mutateAsync(request);
      setPendingRequest(null);
      router.push(`/account/care-event-deletions/${job.deletion_job_id}`);
    } catch (error) {
      if (error instanceof ContractApiError && error.status === 409 && error.envelope.code === "VERSION_CONFLICT") {
        setPendingRequest(null);
        await readLatest(error);
        return;
      }
      if (error instanceof ContractApiError && error.status === 404) {
        onAccessLost();
        return;
      }
      setDeleteError(error);
      setPendingRequest(isCareEventOutcomeUnknown(error) ? request : null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="생활 기록 삭제">
        <p className="text-sm text-foreground">이 기록을 삭제 요청할까요?</p>
        <p className="mt-2 text-sm text-muted-foreground">
          연결된 행동과 개인화 참조에도 영향을 줍니다. 요청이 접수되면 일반 조회에서 제외되지만,
          202 응답만으로 실제 정리가 끝난 것은 아니에요. 작업 상태를 따로 확인해야 합니다.
        </p>
        <p className="mt-2 text-xs text-muted-foreground">읽은 version {baseline.version}으로 요청해요.</p>
      </ScreenSection>
      <CareEventCard event={baseline} babyId={babyId} memberNames={memberNames} />
      {!pendingRequest && !conflict && (
        <div className="flex flex-col gap-2">
          <button
            type="button"
            onClick={() => void send({
              babyId, careEventId: baseline.care_event_id, version: baseline.version,
              clientRequestId: newClientRequestId(),
            })}
            disabled={mutation.isPending}
            className="min-h-11 rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground disabled:opacity-50"
          >
            {mutation.isPending ? "삭제 요청 확인 중" : "이 기록 삭제 요청"}
          </button>
          <button type="button" onClick={onCancel} disabled={mutation.isPending} className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
            취소
          </button>
        </div>
      )}

      {deleteError !== null && (
        <div className="flex flex-col gap-2">
          <ErrorState label={deleteErrorMessage(deleteError)} retryable={pendingRequest !== null} />
          {pendingRequest && (
            <button
              type="button"
              onClick={() => void send(pendingRequest)}
              disabled={mutation.isPending}
              className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50"
            >
              같은 삭제 요청으로 결과 다시 확인
            </button>
          )}
        </div>
      )}

      {conflict && (
        <div className="flex flex-col gap-3" role="alert">
          <p className="text-sm font-medium text-destructive">삭제를 요청하지 않았어요. 기록이 먼저 바뀌었습니다.</p>
          {conflict.latest ? (
            <>
              <p className="text-sm text-foreground">현재 권한으로 다시 확인한 최신 기록을 검토해 주세요.</p>
              <CareEventCard event={conflict.latest} babyId={babyId} memberNames={memberNames} />
              <button
                type="button"
                onClick={() => {
                  if (!conflict.latest) return;
                  void send({
                    babyId, careEventId: baseline.care_event_id, version: conflict.latest.version,
                    clientRequestId: newClientRequestId(),
                  });
                }}
                disabled={mutation.isPending}
                className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive disabled:opacity-50"
              >
                이 최신 기록도 삭제 요청
              </button>
              <button type="button" onClick={onCancel} className="min-h-11 rounded-md border border-border px-4 text-sm">삭제하지 않기</button>
            </>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted-foreground">
                {conflict.readFailed ? "최신 기록을 다시 조회하지 못했어요. 409 응답에 담긴 값은 표시하지 않습니다." : "현재 권한으로 최신 기록을 다시 확인하고 있어요."}
              </p>
              {conflict.readFailed && (
                <button type="button" onClick={() => void readLatest()} className="min-h-11 rounded-md border border-border px-4 text-sm">
                  최신 기록 다시 조회
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
