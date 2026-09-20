"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { CareEventCard } from "@/components/care-event-card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PermissionState,
  ScreenSection,
} from "@/components/screen-state";
import { useTimelineQuery } from "@/lib/api/care-events";
import type { TimelineItem } from "@/lib/api/care-events";
import { ContractApiError } from "@/lib/api/errors";
import { useMembersQuery } from "@/lib/api/members";
import { useRealSession } from "@/lib/auth/real-session";
import { isForBaby, mockCareEvent } from "@/lib/mock/fixtures";
import { PageHeading } from "@/components/page-heading";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import { SourceBadge } from "@/components/source-badge";
import { observationLabel } from "@/components/home-observation";

function TimelineRecord({
  item,
  babyId,
  names,
}: Readonly<{
  item: TimelineItem;
  babyId: string;
  names: ReadonlyMap<string, string>;
}>) {
  if (item.kind === "CARE_EVENT" && "care_event_id" in item.resource) {
    return (
      <CareEventCard
        event={item.resource}
        babyId={babyId}
        memberNames={names}
        linkToDetail
      />
    );
  }
  const label = item.kind === "EPISODE" ? "울음 사건" : "상태 관찰";
  return (
    <ScreenSection title={label}>
      <p className="text-sm text-muted-foreground">
        {item.occurred_at === null
          ? "실제 시각 모름"
          : new Date(item.occurred_at).toLocaleString("ko-KR")}
      </p>
      {"visual_state_code" in item.resource && (
        <p className="text-lg font-semibold">
          {observationLabel[item.resource.visual_state_code]}
        </p>
      )}
      <p className="text-sm text-muted-foreground">
        작성자 {names.get(item.resource.created_by_user_id) ?? "이름 미확인"}
      </p>
      <SourceBadge dataOrigin={item.resource.data_origin} />
    </ScreenSection>
  );
}

function RealTimeline({ babyId }: Readonly<{ babyId: string }>) {
  const timeline = useTimelineQuery(babyId, true);
  const members = useMembersQuery(babyId, true);
  const names = new Map(
    members.data?.items.map((member) => [
      member.user_id,
      member.display_name,
    ]) ?? [],
  );

  if (timeline.isLoading)
    return <LoadingState label="기록을 불러오고 있어요" />;
  if (timeline.isError) {
    if (
      timeline.error instanceof ContractApiError &&
      timeline.error.status === 404
    ) {
      return <ErrorState label="찾을 수 없거나 접근할 수 없어요." />;
    }
    if (
      timeline.error instanceof ContractApiError &&
      timeline.error.status === 403
    ) {
      return <PermissionState label="이 아기의 기록을 볼 권한이 없어요." />;
    }
    return (
      <div className="flex flex-col gap-3">
        <ErrorState label="기록을 불러오지 못했어요." retryable />
        <button
          type="button"
          onClick={() => void timeline.refetch()}
          className="min-h-11 rounded-md border border-border px-4 text-sm"
        >
          다시 조회
        </button>
      </div>
    );
  }

  const items = timeline.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className="flex flex-col gap-3">
      <Link href={`/babies/${babyId}/entries`} className="text-sm underline">
        내 개인 초안 작성·복구
      </Link>
      {items.length === 0 ? (
        <EmptyState
          label="이 아기의 확정 기록이 아직 없어요"
          action={
            <Link
              href={`/babies/${babyId}/quick-record`}
              className="text-sm text-primary"
            >
              기록하러 가기
            </Link>
          }
        />
      ) : (
        items.map((item) => (
          <TimelineRecord
            key={`${item.kind}:${item.resource_id}`}
            item={item}
            babyId={babyId}
            names={names}
          />
        ))
      )}
      {timeline.hasNextPage && (
        <button
          type="button"
          onClick={() => void timeline.fetchNextPage()}
          disabled={timeline.isFetchingNextPage}
          className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50"
        >
          {timeline.isFetchingNextPage ? "더 불러오는 중" : "더 보기"}
        </button>
      )}
    </div>
  );
}

function MockTimeline({ babyId }: Readonly<{ babyId: string }>) {
  const example = mockCareEvent();
  return (
    <div className="flex flex-col gap-3">
      {isForBaby(babyId, example) ? (
        <CareEventCard event={example} babyId={babyId} linkToDetail />
      ) : (
        <EmptyState label="이 아기의 예시 기록이 없어요" />
      )}
    </div>
  );
}

export default function TimelinePage() {
  const { babyId } = useParams<{ babyId: string }>();
  const real = useRealSession();
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PageHeading
          title="함께 이어온 하루"
          description="돌봄과 관찰, 누가 언제 남겼는지 한눈에 확인해요."
        />
        <ActionButton asChild>
          <Link href={`/babies/${babyId}/quick-record`}>기록 추가</Link>
        </ActionButton>
      </div>
      {real.status === "signed-in" ? (
        <RealTimeline babyId={babyId} />
      ) : (
        <MockTimeline babyId={babyId} />
      )}
    </div>
  );
}
