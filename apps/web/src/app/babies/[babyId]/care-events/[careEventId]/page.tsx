"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { usePrivateScope } from "@/components/app-providers";
import { CareEventCard } from "@/components/care-event-card";
import { CareEventDelete } from "@/components/care-event-delete";
import { CareEventEdit } from "@/components/care-event-edit";
import { ExistingCareEventLink } from "@/components/existing-care-event-link";
import { ErrorState, LoadingState, PermissionState } from "@/components/screen-state";
import { useCareEventQuery } from "@/lib/api/care-events";
import type { CareEvent } from "@/lib/api/care-events";
import { findBabyAccess, useBabiesQuery } from "@/lib/api/babies";
import { ContractApiError } from "@/lib/api/errors";
import { useMembersQuery } from "@/lib/api/members";
import { useRealSession } from "@/lib/auth/real-session";
import { isCareEventValueEditable } from "@/lib/care-events/form";
import { mockCareEvent } from "@/lib/mock/fixtures";

function RealCareEventDetail({ babyId, careEventId }: Readonly<{ babyId: string; careEventId: string }>) {
  const scope = usePrivateScope();
  const real = useRealSession();
  const detail = useCareEventQuery(babyId, careEventId, true);
  const members = useMembersQuery(babyId, true);
  const babies = useBabiesQuery(true);
  const [mode, setMode] = useState<Readonly<{ kind: "edit" | "delete"; baseline: CareEvent }> | null>(null);
  const [accessLost, setAccessLost] = useState(false);
  const names = new Map(members.data?.items.map((member) => [member.user_id, member.display_name]) ?? []);

  function denyAccess() {
    setMode(null);
    setAccessLost(true);
    scope.clearForRevalidation();
  }

  if (accessLost) return <ErrorState label="찾을 수 없거나 접근할 수 없어요." />;
  if (detail.isLoading) return <LoadingState label="기록을 불러오고 있어요" />;
  if (detail.isError) {
    if (detail.error instanceof ContractApiError && detail.error.status === 404) {
      return <ErrorState label="찾을 수 없거나 접근할 수 없어요." />;
    }
    if (detail.error instanceof ContractApiError && detail.error.status === 403) {
      return <PermissionState label="이 기록을 볼 권한이 없어요." />;
    }
    return <ErrorState label="기록을 불러오지 못했어요." retryable />;
  }
  if (!detail.data) return <ErrorState label="기록을 확인할 수 없어요." />;

  const event = detail.data;
  const role = findBabyAccess(babies.data, babyId)?.membership.role;
  const canChange = event.status === "ACTIVE" &&
    (role === "OWNER" || event.created_by_user_id === real.userId);
  const canEditStructured = event.source_entry_id === null && isCareEventValueEditable(event.event);

  if (mode?.kind === "edit") {
    return (
      <CareEventEdit
        babyId={babyId}
        baseline={mode.baseline}
        memberNames={names}
        onDone={() => setMode(null)}
        onCancel={() => setMode(null)}
        onAccessLost={denyAccess}
      />
    );
  }
  if (mode?.kind === "delete") {
    return (
      <CareEventDelete
        babyId={babyId}
        baseline={mode.baseline}
        memberNames={names}
        onCancel={() => setMode(null)}
        onAccessLost={denyAccess}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <CareEventCard event={event} babyId={babyId} memberNames={names} />
      {canChange && (
        <div className="flex flex-col gap-2">
          {canEditStructured ? (
            <button type="button" onClick={() => setMode({ kind: "edit", baseline: event })} className="min-h-11 rounded-md border border-border px-4 text-sm">
              기록 수정
            </button>
          ) : (
            <p className="text-xs text-muted-foreground">이 기록의 원문 또는 상대 시각은 선택지 수정으로 바꾸지 않아요. 수정 초안 확인 흐름에서 다뤄야 해요.</p>
          )}
          <button type="button" onClick={() => setMode({ kind: "delete", baseline: event })} className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive">
            기록 삭제
          </button>
        </div>
      )}
      {!canChange && <p className="text-xs text-muted-foreground">수정·삭제는 최초 작성자 또는 관리 보호자에게만 허용돼요.</p>}
      {event.status === "ACTIVE" && <ExistingCareEventLink babyId={babyId} event={event} />}
    </div>
  );
}

export default function CareEventDetailPage() {
  const { babyId, careEventId } = useParams<{ babyId: string; careEventId: string }>();
  const real = useRealSession();
  const example = mockCareEvent();
  return (
    <div className="flex flex-col gap-4">
      <Link href={`/babies/${babyId}/timeline`} className="text-sm text-primary">← 타임라인</Link>
      <h1 className="text-lg font-semibold text-foreground">생활 기록</h1>
      {real.status === "signed-in" ? (
        <RealCareEventDetail key={careEventId} babyId={babyId} careEventId={careEventId} />
      ) : example.baby_id === babyId && example.care_event_id === careEventId ? (
        <>
          <p className="text-xs text-muted-foreground">계약의 합성 예시이며 서버 조회 결과가 아니에요.</p>
          <CareEventCard event={example} babyId={babyId} />
        </>
      ) : (
        <ErrorState label="찾을 수 없거나 접근할 수 없어요." />
      )}
    </div>
  );
}
