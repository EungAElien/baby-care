// SC02 홈 — 아기 감지 상태, 최근 확인 상태, 분석/기록 진입점.
import Link from "next/link";
import { isForBaby, mockStateObservation } from "@/lib/mock/fixtures";
import { RecentCareEvent } from "@/components/recent-care-event";
import { SourceBadge } from "@/components/source-badge";
import { EmptyState, ScreenSection } from "@/components/screen-state";

const stateLabel: Record<string, string> = {
  CRYING: "울고 있어요",
  FUSSING: "칭얼거려요",
  CALM: "차분해요",
  SLEEPY_APPEARING: "졸려 보여요",
  ASLEEP: "잠들었어요",
  AWAKE: "깨어 있어요",
  CHEERFUL_APPEARING: "기분이 좋아 보여요",
  NEUTRAL: "확인된 상태 없음",
};

export default async function BabyHomePage({ params }: Readonly<{ params: Promise<{ babyId: string }> }>) {
  const { babyId } = await params;
  const observationFixture = mockStateObservation();
  const observation = isForBaby(babyId, observationFixture) ? observationFixture : null;

  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="지금 상태">
        {observation ? (
          <>
            <div className="flex items-center justify-between">
              <p className="text-base font-medium text-foreground">{stateLabel[observation.visual_state_code]}</p>
              <SourceBadge dataOrigin={observation.data_origin} />
            </div>
            <p className="text-xs text-muted-foreground">
              {new Date(observation.observed_at ?? observation.recorded_at).toLocaleString("ko-KR")}에 보호자가
              확인한 상태예요. 자동으로 추정한 기분이 아니에요.
            </p>
          </>
        ) : (
          <EmptyState label="이 아기의 확인된 상태가 아직 없어요" />
        )}
      </ScreenSection>

      <RecentCareEvent babyId={babyId} />

      <div className="grid grid-cols-2 gap-3">
        <Link
          href={`/babies/${babyId}/detect`}
          className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
        >
          지금 분석 시작
        </Link>
        <Link
          href={`/babies/${babyId}/quick-record`}
          className="flex min-h-11 items-center justify-center rounded-md border border-border px-4 text-sm font-medium text-foreground"
        >
          빠른 기록
        </Link>
      </div>

      <Link href={`/babies/${babyId}/care-team`} className="text-sm text-muted-foreground underline">
        공동양육 구성원 보기
      </Link>
    </div>
  );
}
