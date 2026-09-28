// SC04 결과 — 개발계약 §6 상태표(RUNNING/COMPLETE/ABSTAIN/FAILED)를 그대로
// 네 개의 목 화면으로 나눈다. `scenario`는 실제 analysis_id가 아니라 이 셸
// 안에서만 쓰는 미리보기 키다(실제 연동은 A-06).
import Link from "next/link";
import { notFound } from "next/navigation";
import { isForBaby, mockAnalysis, type MockAnalysisScenarioName } from "@/lib/mock/fixtures";
import { SourceBadge } from "@/components/source-badge";
import { EmptyState, ScreenSection, LoadingState, ErrorState } from "@/components/screen-state";

const abstainReasonLabel: Record<string, string> = {
  NO_CRY: "울음이 확인되지 않았어요",
  LOW_QUALITY: "음질이 낮아 판단할 수 없어요",
  INSUFFICIENT_AUDIO: "입력이 너무 짧아요",
  LOW_CONFIDENCE: "확신할 수 있는 후보가 없어요",
  UNSUPPORTED_SCOPE: "아직 지원하지 않는 상황이에요",
};

const validScenarios = new Set<MockAnalysisScenarioName>([
  "analysis_running",
  "analysis_complete_stub",
  "analysis_abstain",
  "analysis_no_cry",
  "analysis_failed",
]);

function isMockAnalysisScenario(value: string): value is MockAnalysisScenarioName {
  return validScenarios.has(value as MockAnalysisScenarioName);
}

export default async function ResultPage({
  params,
}: Readonly<{ params: Promise<{ babyId: string; scenario: string }> }>) {
  const { babyId, scenario } = await params;
  if (!isMockAnalysisScenario(scenario)) notFound();

  const analysis = mockAnalysis(scenario);
  const isCurrentBaby = isForBaby(babyId, analysis);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold text-foreground">분석 결과</h1>
        {isCurrentBaby && <SourceBadge inferenceMode={analysis.inference_mode} dataOrigin={analysis.data_origin} />}
      </div>

      {!isCurrentBaby && <EmptyState label="이 아기의 분석 자료가 없어요" />}

      {isCurrentBaby && analysis.status === "RUNNING" && <LoadingState label={`분석 중이에요 (${analysis.stage})`} />}

      {isCurrentBaby && analysis.status === "COMPLETE" && (
        <ScreenSection title="가능성 있는 원인">
          <ul className="flex flex-col gap-2">
            {analysis.audio_candidates.slice(0, 3).map((candidate) => (
              <li key={candidate.code} className="rounded-md border border-border px-3 py-2 text-sm text-foreground">
                {candidate.label} <span className="text-xs text-muted-foreground">가능성</span>
              </li>
            ))}
          </ul>
          {analysis.recommendation && (
            <div className="flex flex-col gap-2 border-t border-border pt-3">
              <p className="text-sm font-medium text-foreground">확인해볼 행동</p>
              {analysis.recommendation.actions.map((action) => (
                <p key={action.text} className="text-sm text-muted-foreground">
                  {action.text}
                </p>
              ))}
            </div>
          )}
          <Link
            href={`/babies/${babyId}/quick-record`}
            className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
          >
            실제 수행한 행동 기록하기
          </Link>
        </ScreenSection>
      )}

      {isCurrentBaby && analysis.status === "ABSTAIN" && (
        <ScreenSection title="판단을 유보했어요">
          <p className="text-sm text-foreground">
            {analysis.abstain_reason ? abstainReasonLabel[analysis.abstain_reason] : "사유 없음"}
          </p>
          <div className="flex gap-2">
            <Link
              href={`/babies/${babyId}/detect`}
              className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground"
            >
              다시 녹음하기
            </Link>
            <Link
              href={`/babies/${babyId}/quick-record`}
              className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground"
            >
              직접 기록하기
            </Link>
          </div>
        </ScreenSection>
      )}

      {isCurrentBaby && analysis.status === "FAILED" && (
        <ScreenSection title="분석을 완료하지 못했어요">
          <ErrorState label={analysis.failure?.message ?? "알 수 없는 오류"} retryable={analysis.failure?.retryable} />
          {analysis.failure?.retryable && (
            <Link
              href={`/babies/${babyId}/detect`}
              className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              다시 시도하기
            </Link>
          )}
        </ScreenSection>
      )}
    </div>
  );
}
