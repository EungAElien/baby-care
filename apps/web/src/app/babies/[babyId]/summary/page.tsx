// SC07 요약과 준비 — 기록 합계·표본 수·범위·준비 알림. `state` 쿼리로
// 기록 없음/양 모름 두 목 화면을 오갈 수 있다. 실제 계산은 B-08·B-11 연동 후.
import Link from "next/link";
import { mockDailySummary, mockPatterns } from "@/lib/mock/fixtures";
import { EmptyState, ScreenSection } from "@/components/screen-state";

export default async function SummaryPage({
  params,
  searchParams,
}: Readonly<{ params: Promise<{ babyId: string }>; searchParams: Promise<{ state?: string }> }>) {
  const { babyId } = await params;
  const { state } = await searchParams;
  const summary = mockDailySummary(state === "unknown-amount" ? "summary_unknown_amount" : "summary_empty");
  const patterns = mockPatterns();

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">오늘 요약</h1>

      {!summary.has_records ? (
        <EmptyState
          label="기록이 없어요"
          action={
            <Link
              href={`/babies/${babyId}/quick-record`}
              className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              지금 기록하기
            </Link>
          }
        />
      ) : (
        <ScreenSection title="수유">
          <p className="text-sm text-foreground">{summary.feeding.record_count}회</p>
          {summary.feeding.unknown_amount_count > 0 && (
            <p className="text-xs text-muted-foreground">양을 모르는 기록 {summary.feeding.unknown_amount_count}회</p>
          )}
        </ScreenSection>
      )}

      <ScreenSection title="준비 패턴">
        {patterns.items.map((item) => (
          <p key={item.kind} className="text-sm text-muted-foreground">
            {item.status === "ON_HOLD"
              ? `유효 기록이 부족해 보류 중이에요 (${item.valid_days}일, ${item.interval_count}개 간격)`
              : "준비 시점을 안내할 수 있어요"}
          </p>
        ))}
        <p className="text-xs text-muted-foreground">준비 알림은 기본 꺼짐이며 개인별로 설정합니다.</p>
      </ScreenSection>

      <div className="flex gap-2 text-xs">
        <Link href={`/babies/${babyId}/summary`} className="underline text-muted-foreground">
          기록 없음 보기
        </Link>
        <Link href={`/babies/${babyId}/summary?state=unknown-amount`} className="underline text-muted-foreground">
          양 모름 보기
        </Link>
      </div>
    </div>
  );
}
