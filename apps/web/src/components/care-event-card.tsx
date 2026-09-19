import Link from "next/link";
import { ScreenSection } from "@/components/screen-state";
import { SourceBadge } from "@/components/source-badge";
import type { CareEvent } from "@/lib/api/care-events";

const feedingModeLabel = {
  BREAST: "모유",
  FORMULA: "분유",
  MIXED: "혼합",
  UNSPECIFIED: "방식 모름",
} as const;
const diaperOperationLabel = { CHECK: "확인", CHANGE: "교체" } as const;
const diaperConditionLabel = {
  WET: "소변",
  STOOL: "대변",
  BOTH: "소변·대변",
  CLEAN: "깨끗함",
  UNKNOWN: "상태 모름",
} as const;
const sootheActionLabel = {
  HOLDING: "안기",
  BURPING: "트림 돕기",
  SLEEP_PREPARATION: "재우기",
  ENVIRONMENT_ADJUSTMENT: "환경 조절",
  OTHER: "기타 돌봄",
} as const;

export function careEventTitle(event: CareEvent): string {
  switch (event.event.type) {
    case "FEEDING": return "수유";
    case "SLEEP": return "수면";
    case "DIAPER": return "기저귀";
    case "SOOTHE": return "달래기·기타 돌봄";
  }
}

function eventDescription(event: CareEvent): string {
  switch (event.event.type) {
    case "FEEDING": {
      const { mode, amount_ml: amount, duration_minutes: duration } = event.event.payload;
      return [
        feedingModeLabel[mode],
        amount === null ? "양 모름" : `${amount}mL`,
        duration === null ? "시간 모름" : `${duration}분`,
      ].join(" · ");
    }
    case "SLEEP":
      return event.event.ended_at === null
        ? "진행 중 · 종료 시각은 아직 기록되지 않았어요"
        : `${new Date(event.event.occurred_at).toLocaleString("ko-KR")} ~ ${new Date(event.event.ended_at).toLocaleString("ko-KR")}`;
    case "DIAPER":
      return `${diaperOperationLabel[event.event.payload.operation]} · ${diaperConditionLabel[event.event.payload.condition]}`;
    case "SOOTHE":
      return sootheActionLabel[event.event.payload.action_kind];
  }
}

function personLabel(userId: string, names?: ReadonlyMap<string, string>): string {
  return names?.get(userId) ?? `ID ${userId.slice(0, 8)}`;
}

export function CareEventCard({
  event,
  babyId,
  memberNames,
  linkToDetail = false,
}: Readonly<{
  event: CareEvent;
  babyId: string;
  memberNames?: ReadonlyMap<string, string>;
  linkToDetail?: boolean;
}>) {
  return (
    <ScreenSection title={careEventTitle(event)}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-foreground">
          {event.event.occurred_at === null
            ? "실제 시각 모름"
            : new Date(event.event.occurred_at).toLocaleString("ko-KR")}
        </p>
        <SourceBadge dataOrigin={event.data_origin} />
      </div>
      <p className="text-sm text-foreground">{eventDescription(event)}</p>
      <p className="text-xs text-muted-foreground">
        작성자 {personLabel(event.created_by_user_id, memberNames)} · 수정자 {personLabel(event.updated_by_user_id, memberNames)}
      </p>
      <p className="text-xs text-muted-foreground">
        입력 {new Date(event.recorded_at).toLocaleString("ko-KR")} · version {event.version}
      </p>
      {linkToDetail && (
        <Link href={`/babies/${babyId}/care-events/${event.care_event_id}`} className="text-sm font-medium text-primary">
          기록 자세히 보기
        </Link>
      )}
    </ScreenSection>
  );
}
