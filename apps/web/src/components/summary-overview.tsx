import Link from "next/link";
import { Milk, Moon, Baby } from "lucide-react";
import type { components } from "@/lib/api/generated";
import { ScreenSection, EmptyState } from "./screen-state";
import { ActionButton } from "./seed-design/ui/action-button";
type Summary = components["schemas"]["DailySummary"];

/** null means unknown; 0 is a measured/recorded zero only when its coverage exists. */
export function feedingAmount(summary: Summary): string {
  if (summary.feeding.record_count === 0) return "기록 없음";
  if (summary.feeding.total_recorded_ml === null) return "양 정보 부족";
  return `${summary.feeding.total_recorded_ml.toLocaleString()} mL`;
}
export function SummaryOverview({ summary, babyId }: Readonly<{ summary: Summary | null; babyId: string }>) {
  if (!summary?.has_records) return <EmptyState label="아직 요약할 기록이 없어요" action={<ActionButton asChild><Link href={`/babies/${babyId}/quick-record`}>첫 돌봄 기록하기</Link></ActionButton>} />;
  return <>
    <p className="text-sm text-muted-foreground">{summary.date} · {summary.timezone}<br />{new Date(summary.as_of).toLocaleString("ko-KR")} 기준</p>
    <div className="summary-grid">
      <ScreenSection title="수유"><Milk aria-hidden="true" /><p className="metric-value">{feedingAmount(summary)}</p><p>{summary.feeding.record_count}회 기록</p>{summary.feeding.unknown_amount_count > 0 && <p className="text-sm text-muted-foreground">양을 모르는 기록 {summary.feeding.unknown_amount_count}회는 합계에 포함하지 않아요.</p>}</ScreenSection>
      <ScreenSection title="수면"><Moon aria-hidden="true" /><p className="metric-value">{summary.sleep.record_count === 0 ? "기록 없음" : summary.sleep.has_unknown_duration ? "시간 정보 부족" : `${summary.sleep.recorded_minutes_in_day}분`}</p><p className="text-sm text-muted-foreground">{summary.sleep.active_sleep_id ? "아직 끝나지 않은 수면이 있어요." : "기록된 수면만 합산해요."}</p></ScreenSection>
      <ScreenSection title="기저귀"><Baby aria-hidden="true" /><p className="metric-value">{summary.diaper.check_count + summary.diaper.change_count === 0 ? "기록 없음" : `${summary.diaper.change_count}회 교체`}</p><p className="text-sm text-muted-foreground">확인 {summary.diaper.check_count}회 · 교체 {summary.diaper.change_count}회</p></ScreenSection>
    </div>
  </>;
}
