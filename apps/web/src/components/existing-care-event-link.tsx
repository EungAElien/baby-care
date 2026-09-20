"use client";

import { useState } from "react";
import { ErrorState, ScreenSection } from "@/components/screen-state";
import { useLinkExistingCareEventMutation } from "@/lib/api/care-events";
import type { ActionAttempt, CareEvent, LinkCareEventRequest } from "@/lib/api/care-events";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { isCareEventOutcomeUnknown } from "@/lib/care-events/request-outcome";

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function linkErrorMessage(error: unknown): string {
  if (error instanceof ContractApiError) {
    switch (error.envelope.code) {
      case "RESOURCE_NOT_FOUND": return "찾을 수 없거나 접근할 수 없는 사건 또는 기록이에요.";
      case "IDEMPOTENCY_KEY_REUSED": return "같은 요청 키에 다른 내용이 사용됐어요. 자동으로 다시 보내지 않았어요.";
      case "VALIDATION_ERROR": return "사건 ID와 행동 순서를 확인해 주세요.";
      default: return error.envelope.message;
    }
  }
  if (isCareEventOutcomeUnknown(error)) return "연결 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.";
  return "기록을 사건에 연결하지 못했어요. 접속 상태를 확인해 주세요.";
}

export function ExistingCareEventLink({ babyId, event }: Readonly<{ babyId: string; event: CareEvent }>) {
  const mutation = useLinkExistingCareEventMutation();
  const [episodeId, setEpisodeId] = useState("");
  const [sequence, setSequence] = useState("1");
  const [performed, setPerformed] = useState(false);
  const [pendingRequest, setPendingRequest] = useState<LinkCareEventRequest | null>(null);
  const [linkedAction, setLinkedAction] = useState<ActionAttempt | null>(null);
  const [linkError, setLinkError] = useState<unknown>(null);
  const sequenceNumber = Number(sequence);
  const valid = uuidPattern.test(episodeId.trim()) && Number.isSafeInteger(sequenceNumber) && sequenceNumber >= 1;

  async function send(request: LinkCareEventRequest) {
    setLinkError(null);
    try {
      const result = await mutation.mutateAsync(request);
      setLinkedAction(result);
      setPendingRequest(null);
    } catch (error) {
      setLinkError(error);
      setPendingRequest(isCareEventOutcomeUnknown(error) ? request : null);
    }
  }

  return (
    <ScreenSection title="기존 기록을 울음 사건에 연결">
      <p className="text-sm text-muted-foreground">
        기존 기록을 연결해도 생활 기록을 다시 만들지 않아요. 실제로 한 행동에만 사용해 주세요.
        현재 사건 선택 화면은 준비 전이므로, 같은 아기의 기존 사건 ID를 알고 있을 때만 연결할 수 있어요.
      </p>
      <form
        onSubmit={(submitEvent) => {
          submitEvent.preventDefault();
          if (!valid || !performed || pendingRequest || mutation.isPending || linkedAction) return;
          void send({
            babyId, careEventId: event.care_event_id, episodeId: episodeId.trim(),
            clientRequestId: newClientRequestId(), sequence: sequenceNumber,
          });
        }}
        className="mt-3 flex flex-col gap-3"
      >
        <label className="flex flex-col gap-1 text-sm text-foreground">
          기존 울음 사건 ID
          <input
            type="text"
            inputMode="text"
            autoComplete="off"
            value={episodeId}
            onChange={(changeEvent) => { setEpisodeId(changeEvent.target.value); setLinkedAction(null); }}
            aria-invalid={episodeId.length > 0 && !uuidPattern.test(episodeId.trim())}
            disabled={pendingRequest !== null || mutation.isPending}
            className="min-h-11 rounded-md border border-border bg-background px-3"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm text-foreground">
          이 사건에서 행동 순서
          <input
            type="number"
            min="1"
            step="1"
            value={sequence}
            onChange={(changeEvent) => { setSequence(changeEvent.target.value); setLinkedAction(null); }}
            aria-invalid={!Number.isSafeInteger(sequenceNumber) || sequenceNumber < 1}
            disabled={pendingRequest !== null || mutation.isPending}
            className="min-h-11 rounded-md border border-border bg-background px-3"
          />
        </label>
        <label className="flex min-h-11 items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            checked={performed}
            onChange={(changeEvent) => setPerformed(changeEvent.target.checked)}
            disabled={pendingRequest !== null || mutation.isPending}
          />
          이 기록은 실제 수행한 행동이에요
        </label>
        <p className="text-xs text-muted-foreground">수행한 사람과 추천 연결은 확인되지 않은 값으로 남깁니다. 서버가 사건과 기록의 아기 범위를 확인해요.</p>
        <button
          type="submit"
          disabled={!valid || !performed || pendingRequest !== null || mutation.isPending || linkedAction !== null}
          className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50"
        >
          {mutation.isPending ? "연결 확인 중" : linkedAction ? "연결됨" : "기존 기록 연결"}
        </button>
      </form>

      {linkError !== null && (
        <div className="mt-3 flex flex-col gap-2">
          <ErrorState label={linkErrorMessage(linkError)} retryable={pendingRequest !== null} />
          {pendingRequest && (
            <button type="button" onClick={() => void send(pendingRequest)} disabled={mutation.isPending} className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
              같은 연결 요청으로 결과 다시 확인
            </button>
          )}
        </div>
      )}
      {linkedAction && (
        <p className="mt-3 text-sm text-foreground">
          연결을 확인했어요. 행동 ID {linkedAction.action_id} · 생활 기록은 새로 생성하지 않았어요.
        </p>
      )}
    </ScreenSection>
  );
}
