"use client";

import { useEffect, useRef, useState } from "react";
import { ActionButton } from "./seed-design/ui/action-button";
import Link from "next/link";
import { zodResolver } from "@hookform/resolvers/zod";
import { useForm } from "react-hook-form";
import { CareEventCard } from "@/components/care-event-card";
import { CareEventFields } from "@/components/care-event-fields";
import { ErrorState, ScreenSection } from "@/components/screen-state";
import { useCreateCareEventMutation } from "@/lib/api/care-events";
import type { CareEvent, CreateCareEventRequest } from "@/lib/api/care-events";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { buildCareEventValue, careEventFormSchema, defaultCareEventFormValues } from "@/lib/care-events/form";
import type { CareEventFormValues } from "@/lib/care-events/form";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";
import { useDraft } from "@/lib/mock/draft";

function saveErrorMessage(error: unknown): string {
  if (error instanceof ContractApiError) {
    switch (error.envelope.code) {
      case "SLEEP_ALREADY_ACTIVE": return "진행 중인 수면 기록이 있어요. 타임라인에서 확인해 주세요.";
      case "RESOURCE_NOT_FOUND": return "찾을 수 없거나 접근할 수 없어요.";
      case "OWNER_ONLY":
      case "AUTHOR_ONLY": return "이 기록을 저장할 권한이 없어요.";
      case "VALIDATION_ERROR": return "입력값을 확인해 주세요.";
      case "IDEMPOTENCY_KEY_REUSED": return "이 요청의 중복 방지 키가 다른 내용에 사용됐어요. 자동 재전송하지 않았어요.";
      default: return error.envelope.message;
    }
  }
  if (isCareEventOutcomeUnknown(error)) return "저장 결과를 확인하지 못했어요. 같은 요청으로 다시 확인하거나 타임라인을 살펴봐 주세요.";
  return "기록을 저장하지 못했어요. 입력과 접속 상태를 확인해 주세요.";
}

function activeSleepId(error: unknown, babyId: string): string | null {
  if (!(error instanceof ContractApiError) || error.envelope.code !== "SLEEP_ALREADY_ACTIVE") return null;
  const resource = error.envelope.details.current_resource;
  if (typeof resource !== "object" || resource === null || Array.isArray(resource)) return null;
  const record = resource as Record<string, unknown>;
  const event = record.event;
  if (record.baby_id !== babyId || typeof record.care_event_id !== "string" ||
    typeof event !== "object" || event === null || Array.isArray(event) ||
    (event as Record<string, unknown>).type !== "SLEEP") return null;
  return record.care_event_id;
}

export function CareEventForm({ babyId, canSave }: Readonly<{ babyId: string; canSave: boolean }>) {
  const draft = useDraft();
  const mutation = useCreateCareEventMutation();
  const submissionLock = useRef(false);
  const [savedEvent, setSavedEvent] = useState<CareEvent | null>(null);
  const [pendingRequest, setPendingRequest] = useState<CreateCareEventRequest | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const form = useForm<CareEventFormValues>({
    resolver: zodResolver(careEventFormSchema),
    defaultValues: defaultCareEventFormValues(),
  });
  const { setCareEventDirty } = draft;
  const { handleSubmit, reset, formState } = form;

  useEffect(() => {
    setCareEventDirty(babyId, formState.isDirty);
  }, [babyId, formState.isDirty, setCareEventDirty]);
  useEffect(() => () => setCareEventDirty(babyId, false), [babyId, setCareEventDirty]);

  async function send(request: CreateCareEventRequest) {
    if (submissionLock.current) return;
    submissionLock.current = true;
    setSaveError(null);
    try {
      const { event } = await mutation.mutateAsync(request);
      setSavedEvent(event);
      setPendingRequest(null);
      reset(defaultCareEventFormValues());
    } catch (error) {
      setSaveError(error);
      setPendingRequest(isCareEventOutcomeUnknown(error) ? request : null);
    } finally {
      submissionLock.current = false;
    }
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    await handleSubmit(async (values) => {
    if (!canSave || pendingRequest) return;
    setSavedEvent(null);
    const request: CreateCareEventRequest = {
      babyId,
      clientRequestId: newClientRequestId(),
      event: buildCareEventValue(values),
    };
    await send(request);
    })(event);
  }

  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="선택지로 생활 기록">
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <CareEventFields form={form} disabled={mutation.isPending || pendingRequest !== null} />

          <p className="text-xs text-muted-foreground">실제로 한 일만 저장해 주세요. 선택한 종류와 값 외의 정보를 자동으로 추정하지 않아요.</p>
          <ActionButton
            type="submit"
            disabled={!canSave || mutation.isPending || pendingRequest !== null}
            loading={mutation.isPending}
          >
            {mutation.isPending ? "저장 확인 중" : "기록 저장"}
          </ActionButton>
          {!canSave && <p className="text-xs text-muted-foreground">이 화면은 계약 예시 미리보기예요. 실제 저장은 로그인한 API 환경에서만 할 수 있어요.</p>}
        </form>
      </ScreenSection>

      {saveError !== null && (
        <div className="flex flex-col gap-2">
          <ErrorState label={saveErrorMessage(saveError)} retryable={pendingRequest !== null} />
          {activeSleepId(saveError, babyId) && (
            <Link href={`/babies/${babyId}/care-events/${activeSleepId(saveError, babyId)}`} className="text-sm text-primary underline">
              진행 중인 수면 보기
            </Link>
          )}
          {pendingRequest && (
            <>
              <button
                type="button"
                onClick={() => void send(pendingRequest)}
                disabled={mutation.isPending}
                className="min-h-11 rounded-md border border-border px-4 text-sm text-foreground disabled:opacity-50"
              >
                같은 요청으로 결과 다시 확인
              </button>
              <Link href={`/babies/${babyId}/timeline`} className="text-sm text-primary underline">타임라인에서 저장 여부 살펴보기</Link>
            </>
          )}
        </div>
      )}
      {savedEvent && <CareEventCard event={savedEvent} babyId={babyId} linkToDetail />}
    </div>
  );
}
