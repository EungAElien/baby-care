import Link from "next/link";
import { notFound } from "next/navigation";
import { Check, CircleHelp, CircleX, ArrowLeft } from "lucide-react";
import {
  isForBaby,
  mockAnalysis,
  type MockAnalysisScenarioName,
} from "@/lib/mock/fixtures";
import { SourceBadge } from "@/components/source-badge";
import {
  EmptyState,
  ScreenSection,
  LoadingState,
} from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { DemoOnly } from "@/components/demo-only";
import { ActionButton } from "@/components/seed-design/ui/action-button";
import { Callout } from "@/components/seed-design/ui/callout";
import { List, ListItem } from "@/components/seed-design/ui/list";
import { isMockNavEnabled } from "@/lib/mock/config";

const abstainLabels: Record<string, string> = {
  NO_CRY: "울음이 확인되지 않았어요",
  LOW_QUALITY: "음질이 낮아 판단하기 어려워요",
  INSUFFICIENT_AUDIO: "입력이 너무 짧아요",
  LOW_CONFIDENCE: "확신할 수 있는 후보가 없어요",
  UNSUPPORTED_SCOPE: "아직 지원하지 않는 상황이에요",
};
const valid = new Set<string>([
  "analysis_running",
  "analysis_complete_stub",
  "analysis_abstain",
  "analysis_no_cry",
  "analysis_failed",
]);

export default async function ResultPage({
  params,
}: Readonly<{ params: Promise<{ babyId: string; scenario: string }> }>) {
  const { babyId, scenario } = await params;
  if (!isMockNavEnabled() || !valid.has(scenario)) notFound();
  const analysis = mockAnalysis(scenario as MockAnalysisScenarioName);
  if (!isForBaby(babyId, analysis))
    return <EmptyState label="이 아기의 분석 자료가 없어요" />;
  const title =
    analysis.status === "COMPLETE"
      ? "분석을 마쳤어요"
      : analysis.status === "ABSTAIN"
        ? "판단을 유보했어요"
        : "분석을 완료하지 못했어요";
  const Icon =
    analysis.status === "COMPLETE"
      ? Check
      : analysis.status === "ABSTAIN"
        ? CircleHelp
        : CircleX;
  return (
    <DemoOnly
      fallback={<EmptyState label="이 경로는 데모 결과 미리보기예요" />}
    >
      <div className="flex flex-col gap-6">
        <ActionButton
          variant="ghost"
          size="small"
          className="self-start"
          asChild
        >
          <Link href={`/babies/${babyId}/detect`}>
            <ArrowLeft size={18} aria-hidden="true" />
            소리 입력으로
          </Link>
        </ActionButton>
        <PageHeading
          title="소리에서 찾은 단서"
          description="아기의 모습을 함께 살펴보고 다음 돌봄을 결정해요."
        />
        <SourceBadge
          inferenceMode={analysis.inference_mode}
          inferenceExecuted={analysis.inference_executed}
          dataOrigin={analysis.data_origin}
          className="self-start"
        />
        {analysis.status === "RUNNING" ? (
          <LoadingState label="분석 중이에요. 아직 결과가 없어요." />
        ) : (
          <div
            className="result-banner"
            data-state={analysis.status}
            role="status"
          >
            <Icon size={30} className="shrink-0" aria-hidden="true" />
            <div>
              <h2 className="text-xl font-bold">{title}</h2>
              <p className="mt-2">
                {analysis.status === "COMPLETE"
                  ? "아래 후보는 모델의 추정이에요. 확정된 원인이나 진단이 아니에요."
                  : analysis.status === "ABSTAIN"
                    ? (abstainLabels[analysis.abstain_reason ?? ""] ??
                      "판단할 근거가 충분하지 않아요.")
                    : (analysis.failure?.message ??
                      "처리 중 문제가 생겼어요. 결과가 만들어지지 않았어요.")}
              </p>
            </div>
          </div>
        )}
        {analysis.status === "COMPLETE" && (
          <div className="detail-grid">
            <ScreenSection title="살펴볼 원인 후보">
              <List>
                {analysis.audio_candidates.slice(0, 3).map((candidate, i) => (
                  <ListItem
                    key={candidate.code}
                    prefix={<span className="candidate-number">{i + 1}</span>}
                    title={candidate.label}
                    detail="모델 추정 · 보호자의 확인이 필요해요"
                  />
                ))}
              </List>
              <p className="text-sm text-muted-foreground">
                순서는 모델의 후보 순위예요. 실제 원인일 확률을 뜻하지 않아요.
              </p>
            </ScreenSection>
            <ScreenSection title="확인해 볼 돌봄">
              {analysis.recommendation?.actions.map((action) => (
                <p key={action.text}>{action.text}</p>
              ))}
              <ActionButton asChild>
                <Link href={`/babies/${babyId}/quick-record`}>
                  실제로 한 돌봄 기록
                </Link>
              </ActionButton>
            </ScreenSection>
          </div>
        )}
        {analysis.status === "ABSTAIN" && (
          <div className="flex flex-col gap-3 sm:flex-row">
            <ActionButton asChild>
              <Link href={`/babies/${babyId}/detect`}>다시 녹음하기</Link>
            </ActionButton>
            <ActionButton variant="neutralWeak" asChild>
              <Link href={`/babies/${babyId}/quick-record`}>직접 기록하기</Link>
            </ActionButton>
          </div>
        )}
        {analysis.status === "FAILED" && (
          <>
            <Callout
              title={
                analysis.failure?.retryable
                  ? "다시 시도할 수 있어요"
                  : "입력을 다시 확인해 주세요"
              }
              description="데모 실패 상태예요. 실제 분석 연결에서는 같은 분석 ID의 결과를 먼저 확인해 중복 분석을 방지해요."
            />
            <ActionButton asChild>
              <Link href={`/babies/${babyId}/detect`}>
                소리 입력으로 돌아가기
              </Link>
            </ActionButton>
          </>
        )}
      </div>
    </DemoOnly>
  );
}
