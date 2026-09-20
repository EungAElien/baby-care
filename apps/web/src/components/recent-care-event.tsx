"use client";

import Link from "next/link";
import { careEventTitle } from "@/components/care-event-card";
import { EmptyState, ErrorState, LoadingState, ScreenSection } from "@/components/screen-state";
import { SourceBadge } from "@/components/source-badge";
import { useTimelineQuery } from "@/lib/api/care-events";
import { useRealSession } from "@/lib/auth/real-session";
import { isForBaby, mockCareEvent } from "@/lib/mock/fixtures";

function RealRecentCareEvent({ babyId }: Readonly<{ babyId: string }>) {
  const timeline = useTimelineQuery(babyId, true);
  const item = timeline.data?.pages[0]?.items.find((entry) => entry.kind === "CARE_EVENT" && "care_event_id" in entry.resource);
  const event = item?.kind === "CARE_EVENT" && "care_event_id" in item.resource ? item.resource : null;
  return (
    <ScreenSection title="최근 기록">
      {timeline.isLoading ? <LoadingState label="최근 기록을 불러오고 있어요" /> :
        timeline.isError ? <ErrorState label="최근 기록을 불러오지 못했어요." retryable /> :
          event ? (
            <>
              <p className="text-sm text-foreground">{careEventTitle(event)} 기록</p>
              <div className="flex items-center justify-between">
                <p className="text-xs text-muted-foreground">
                  {event.event.occurred_at === null ? "실제 시각 모름" : new Date(event.event.occurred_at).toLocaleString("ko-KR")}
                </p>
                <SourceBadge dataOrigin={event.data_origin} />
              </div>
            </>
          ) : <EmptyState label="이 아기의 최근 기록이 아직 없어요" />}
      <Link href={`/babies/${babyId}/timeline`} className="text-sm font-medium text-primary">타임라인에서 모두 보기</Link>
    </ScreenSection>
  );
}

function MockRecentCareEvent({ babyId }: Readonly<{ babyId: string }>) {
  const example = mockCareEvent();
  const event = isForBaby(babyId, example) ? example : null;
  return (
    <ScreenSection title="최근 기록 예시">
      {event ? (
        <>
          <p className="text-sm text-foreground">{careEventTitle(event)} 기록</p>
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {event.event.occurred_at === null ? "실제 시각 모름" : new Date(event.event.occurred_at).toLocaleString("ko-KR")}
            </p>
            <SourceBadge dataOrigin={event.data_origin} />
          </div>
        </>
      ) : <EmptyState label="이 아기의 예시 기록이 아직 없어요" />}
      <Link href={`/babies/${babyId}/timeline`} className="text-sm font-medium text-primary">타임라인에서 모두 보기</Link>
    </ScreenSection>
  );
}

export function RecentCareEvent({ babyId }: Readonly<{ babyId: string }>) {
  const real = useRealSession();
  return real.status === "signed-in" ? <RealRecentCareEvent babyId={babyId} /> : <MockRecentCareEvent babyId={babyId} />;
}
