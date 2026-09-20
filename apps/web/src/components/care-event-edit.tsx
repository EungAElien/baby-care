"use client";

import { useEffect, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { CareEventCard, CareEventValueSummary } from "@/components/care-event-card";
import { CareEventFields } from "@/components/care-event-fields";
import { ErrorState, ScreenSection } from "@/components/screen-state";
import { usePrivateScope } from "@/components/app-providers";
import { getCareEvent, getCareEventAfterConflict, usePatchCareEventMutation } from "@/lib/api/care-events";
import type { CareEvent, PatchCareEventRequest } from "@/lib/api/care-events";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useApiClient } from "@/lib/api/real-client";
import { buildCareEventValue, careEventFormSchema, careEventValueToFormValues } from "@/lib/care-events/form";
import type { CareEventFormValues, CareEventValue } from "@/lib/care-events/form";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";
import { useDraft } from "@/lib/mock/draft";

type Conflict = Readonly<{
  attemptedVersion: number;
  draft: CareEventValue;
  latest: CareEvent | null;
  readFailed: boolean;
}>;

function editErrorMessage(error: unknown): string {
  if (error instanceof ContractApiError) {
    switch (error.envelope.code) {
      case "AUTHOR_ONLY": return "이 기록을 수정할 권한이 없어요.";
      case "RESOURCE_NOT_FOUND": return "찾을 수 없거나 접근할 수 없어요.";
      case "SLEEP_ALREADY_ACTIVE": return "진행 중인 다른 수면 기록이 있어요. 타임라인에서 확인해 주세요.";
      case "VALIDATION_ERROR": return "입력값을 확인해 주세요.";
      case "IDEMPOTENCY_KEY_REUSED": return "같은 요청 키에 다른 내용이 사용됐어요. 자동으로 다시 보내지 않았어요.";
      default: return error.envelope.message;
    }
  }
  if (isCareEventOutcomeUnknown(error)) return "수정 결과를 확인하지 못했어요. 같은 요청으로 결과를 다시 확인해 주세요.";
  return "기록을 수정하지 못했어요. 접속 상태를 확인해 주세요.";
}

export function CareEventEdit({
  babyId, baseline, memberNames, onDone, onCancel, onAccessLost,
}: Readonly<{
  babyId: string;
  baseline: CareEvent;
  memberNames: ReadonlyMap<string, string>;
  onDone: () => void;
  onCancel: () => void;
  onAccessLost: () => void;
}>) {
  const client = useApiClient();
  const scope = usePrivateScope();
  const mutation = usePatchCareEventMutation();
  const { setCareEventDirty } = useDraft();
  const [baseVersion, setBaseVersion] = useState(baseline.version);
  const [pendingRequest, setPendingRequest] = useState<PatchCareEventRequest | null>(null);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const form = useForm<CareEventFormValues>({
    resolver: zodResolver(careEventFormSchema),
    defaultValues: careEventValueToFormValues(baseline.event),
  });

  useEffect(() => {
    setCareEventDirty(babyId, form.formState.isDirty || pendingRequest !== null || conflict !== null);
  }, [babyId, conflict, form.formState.isDirty, pendingRequest, setCareEventDirty]);
  useEffect(() => () => setCareEventDirty(babyId, false), [babyId, setCareEventDirty]);

  async function readLatest(draft: CareEventValue, attemptedVersion: number, error?: unknown) {
    setConflict({ draft, attemptedVersion, latest: null, readFailed: false });
    if (!client) {
      setConflict({ draft, attemptedVersion, latest: null, readFailed: true });
      return;
    }
    const snapshot = scope.snapshot();
    try {
      const latest = error === undefined
        ? await getCareEvent(client, babyId, baseline.care_event_id)
        : await getCareEventAfterConflict(client, babyId, baseline.care_event_id, error);
      scope.assertCurrent(snapshot);
      setConflict({ draft, attemptedVersion, latest, readFailed: false });
    } catch (readError) {
      if (readError instanceof ContractApiError && (readError.status === 404 || readError.status === 403)) {
        onAccessLost();
        return;
      }
      setConflict({ draft, attemptedVersion, latest: null, readFailed: true });
    }
  }

  async function send(request: PatchCareEventRequest) {
    setSaveError(null);
    setConflict(null);
    try {
      await mutation.mutateAsync(request);
      setPendingRequest(null);
      onDone();
    } catch (error) {
      if (error instanceof ContractApiError && error.status === 409 && error.envelope.code === "VERSION_CONFLICT") {
        setPendingRequest(null);
        await readLatest(request.event, request.version, error);
        return;
      }
      if (error instanceof ContractApiError && error.status === 404) {
        onAccessLost();
        return;
      }
      setSaveError(error);
      setPendingRequest(isCareEventOutcomeUnknown(error) ? request : null);
    }
  }

  const submit = form.handleSubmit(async (values) => {
    if (mutation.isPending || pendingRequest || conflict) return;
    const request: PatchCareEventRequest = {
      babyId,
      careEventId: baseline.care_event_id,
      version: baseVersion,
      clientRequestId: newClientRequestId(),
      event: buildCareEventValue(values),
    };
    await send(request);
  });

  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="기록 수정">
        <p className="text-xs text-muted-foreground">읽은 version {baseVersion}을 기준으로 저장해요. 다른 보호자의 변경을 자동으로 합치지 않아요.</p>
        <form onSubmit={submit} className="mt-3 flex flex-col gap-4" noValidate>
          <CareEventFields form={form} disabled={mutation.isPending || pendingRequest !== null || conflict !== null} />
          <button
            type="submit"
            disabled={!form.formState.isDirty || mutation.isPending || pendingRequest !== null || conflict !== null}
            className="min-h-11 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
          >
            {mutation.isPending ? "수정 확인 중" : "수정 저장"}
          </button>
        </form>
        {!pendingRequest && !conflict && (
          <button type="button" onClick={onCancel} className="mt-3 min-h-11 rounded-md border border-border px-4 text-sm">
            수정안 버리기
          </button>
        )}
      </ScreenSection>

      {saveError !== null && (
        <div className="flex flex-col gap-2">
          <ErrorState label={editErrorMessage(saveError)} retryable={pendingRequest !== null} />
          {pendingRequest && (
            <button
              type="button"
              onClick={() => void send(pendingRequest)}
              disabled={mutation.isPending}
              className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50"
            >
              같은 수정 요청으로 결과 다시 확인
            </button>
          )}
        </div>
      )}

      {conflict && (
        <div className="flex flex-col gap-3" role="alert">
          <p className="text-sm font-medium text-destructive">다른 변경이 먼저 저장됐어요. 자동 덮어쓰기는 하지 않았어요.</p>
          <ScreenSection title={`내 미저장 수정안 · 읽은 version ${conflict.attemptedVersion}`}>
            <CareEventValueSummary value={conflict.draft} />
          </ScreenSection>
          {conflict.latest ? (
            <>
              <p className="text-sm font-medium text-foreground">현재 조회 권한으로 다시 확인한 최신 서버 기록</p>
              <CareEventCard event={conflict.latest} babyId={babyId} memberNames={memberNames} />
              <button
                type="button"
                onClick={() => {
                  if (!conflict.latest) return;
                  form.reset(careEventValueToFormValues(conflict.latest.event));
                  setBaseVersion(conflict.latest.version);
                  setConflict(null);
                  setSaveError(null);
                }}
                className="min-h-11 rounded-md border border-border px-4 text-sm"
              >
                최신 기록부터 다시 편집
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!conflict.latest) return;
                  void send({
                    babyId, careEventId: baseline.care_event_id, version: conflict.latest.version,
                    clientRequestId: newClientRequestId(), event: conflict.draft,
                  });
                }}
                disabled={mutation.isPending}
                className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive disabled:opacity-50"
              >
                최신 version으로 내 수정안을 다시 저장
              </button>
              <p className="text-xs text-muted-foreground">마지막 선택은 최신 기록을 내 수정안으로 대체합니다. 두 입력을 합치지는 않아요.</p>
            </>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted-foreground">
                {conflict.readFailed ? "최신 기록을 다시 조회하지 못했어요. 409 응답에 담긴 값은 표시하지 않습니다." : "현재 권한으로 최신 기록을 다시 확인하고 있어요."}
              </p>
              {conflict.readFailed && (
                <button
                  type="button"
                  onClick={() => void readLatest(conflict.draft, conflict.attemptedVersion)}
                  className="min-h-11 rounded-md border border-border px-4 text-sm"
                >
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
