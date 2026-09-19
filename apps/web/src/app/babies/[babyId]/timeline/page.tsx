// SC06 타임라인 — 생활 기록·행동·반응 수정 이력. 실제 조회·수정·삭제는
// A-04에서 연결하고, 여기서는 저장 성공/충돌 두 목 상태만 나열한다.
import { mockCareEvent, getMockScenario } from "@/lib/mock/fixtures";
import type { components } from "@/lib/api/generated";
import { SourceBadge } from "@/components/source-badge";
import { ScreenSection } from "@/components/screen-state";

const typeLabel: Record<string, string> = { FEEDING: "수유", SLEEP: "수면", DIAPER: "기저귀", SOOTHE: "달래기" };

export default function TimelinePage() {
  const savedEvent = mockCareEvent();
  const conflictEvent = getMockScenario("edit_conflict").response.body as {
    details: { current_resource: components["schemas"]["CareEvent"] };
  };
  const events = [savedEvent, conflictEvent.details.current_resource];

  return (
    <div className="flex flex-col gap-3">
      <h1 className="text-lg font-semibold text-foreground">타임라인</h1>
      {events.map((event) => (
        <ScreenSection key={event.care_event_id} title={typeLabel[event.event.type] ?? event.event.type}>
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {new Date(event.event.occurred_at ?? event.recorded_at).toLocaleString("ko-KR")}
              {event.event.ended_at === null && event.event.type === "SLEEP" && " · 진행 중"}
            </p>
            <SourceBadge dataOrigin={event.data_origin} />
          </div>
          <p className="text-xs text-muted-foreground">version {event.version} · 수정 시 서버 버전과 비교해요.</p>
        </ScreenSection>
      ))}
    </div>
  );
}
