import { isMockNavEnabled } from "@/lib/mock/config";
import { RealSummary } from "@/components/real-summary";
import Link from "next/link";
import { isForBaby, mockDailySummary, mockPatterns } from "@/lib/mock/fixtures";
import { ScreenSection } from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { SummaryOverview } from "@/components/summary-overview";
import { DemoOnly } from "@/components/demo-only";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import { Callout } from "@/components/seed-design/ui/callout";

export default async function SummaryPage({
  params,
  searchParams,
}: Readonly<{
  params: Promise<{ babyId: string }>;
  searchParams: Promise<{ state?: string }>;
}>) {
  const { babyId } = await params;
  const { state } = await searchParams;
  if (!isMockNavEnabled())
    return (
      <div className="flex flex-col gap-6">
        <PageHeading
          title="오늘 요약"
          description="남긴 만큼만 살펴봐요. 빈 기록을 0으로 계산하지 않아요."
        />
        <RealSummary babyId={babyId} />
      </div>
    );
  const fixture = mockDailySummary(
    state === "unknown-amount" ? "summary_unknown_amount" : "summary_empty",
  );
  const summary = isForBaby(babyId, fixture) ? fixture : null;
  const patternFixture = mockPatterns();
  const patterns = isForBaby(babyId, patternFixture) ? patternFixture : null;
  return (
    <div className="flex flex-col gap-6">
      <PageHeading
        title="오늘 요약"
        description="남긴 만큼만 살펴봐요. 빈 기록을 0으로 계산하지 않아요."
      />
      <DemoOnly fallback={<RealSummary babyId={babyId} />}>
        <SummaryOverview summary={summary} babyId={babyId} />
        <ScreenSection title="다음 돌봄 준비">
          {patterns?.items.map((item) => (
            <div key={item.kind} className="status-note">
              <p className="font-semibold">
                {item.status === "ON_HOLD"
                  ? "패턴을 살펴볼 기록이 더 필요해요"
                  : "준비 시점을 참고할 수 있어요"}
              </p>
              <p className="text-sm text-muted-foreground">
                유효 기록 {item.valid_days}일 · {item.interval_count}개 간격
              </p>
            </div>
          )) ?? <p>이 아기의 패턴 자료가 아직 없어요.</p>}
          <p className="text-sm text-muted-foreground">
            준비 알림은 기본 꺼짐이에요. 예측을 확정된 일정으로 해석하지 마세요.
          </p>
        </ScreenSection>
        <details className="preview-tools">
          <summary>DEMO · 요약 상태 살펴보기</summary>
          <div className="flex flex-wrap gap-2">
            <ActionButton variant="neutralWeak" size="small" asChild>
              <Link href={`/babies/${babyId}/summary`}>기록 없음</Link>
            </ActionButton>
            <ActionButton variant="neutralWeak" size="small" asChild>
              <Link href={`/babies/${babyId}/summary?state=unknown-amount`}>
                양 정보 부족
              </Link>
            </ActionButton>
          </div>
        </details>
      </DemoOnly>
      <Callout
        title="기록 없음 · 정보 부족 · 실제 0"
        description="기록이 없으면 ‘기록 없음’, 값이 빠지면 ‘정보 부족’으로 표시해요. 확인한 값이 0일 때만 숫자 0을 보여줘요."
      />
    </div>
  );
}
