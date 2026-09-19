"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { CareEventCard } from "@/components/care-event-card";
import { ErrorState, LoadingState, PermissionState } from "@/components/screen-state";
import { useCareEventQuery } from "@/lib/api/care-events";
import { ContractApiError } from "@/lib/api/errors";
import { useMembersQuery } from "@/lib/api/members";
import { useRealSession } from "@/lib/auth/real-session";
import { mockCareEvent } from "@/lib/mock/fixtures";

function RealCareEventDetail({ babyId, careEventId }: Readonly<{ babyId: string; careEventId: string }>) {
  const detail = useCareEventQuery(babyId, careEventId, true);
  const members = useMembersQuery(babyId, true);
  const names = new Map(members.data?.items.map((member) => [member.user_id, member.display_name]) ?? []);

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
  return <CareEventCard event={detail.data} babyId={babyId} memberNames={names} />;
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
        <RealCareEventDetail babyId={babyId} careEventId={careEventId} />
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
