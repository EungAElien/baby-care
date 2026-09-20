import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { ScreenSection } from "@/components/screen-state";
import { PageHeading } from "@/components/page-heading";
import { AudioIntakeScreen } from "@/components/audio-intake";
import { DetectionAvailability } from "@/components/detection-availability";
import { DemoOnly } from "@/components/demo-only";
import { ActionButton } from "@/components/seed-design/ui/action-button";

const examples = [{ scenario: "analysis_running", label: "분석 중" }, { scenario: "analysis_complete_stub", label: "완료" }, { scenario: "analysis_abstain", label: "판단 유보" }, { scenario: "analysis_no_cry", label: "울음 미확인" }, { scenario: "analysis_failed", label: "처리 실패" }];

export default async function DetectPage({ params }: Readonly<{ params: Promise<{ babyId: string }> }>) {
  const { babyId } = await params;
  return <div className="flex flex-col gap-6">
    <ActionButton variant="ghost" size="small" asChild className="self-start"><Link href={`/babies/${babyId}`}><ArrowLeft size={18} aria-hidden="true" />홈으로</Link></ActionButton>
    <PageHeading title="작은 소리에 귀 기울여요" description="아기의 신호를 살펴볼 수 있도록 소리를 남겨 주세요." />
    <div className="detail-grid">
      <ScreenSection title="소리 입력"><AudioIntakeScreen babyId={babyId} /></ScreenSection>
      <div className="flex flex-col gap-5"><DetectionAvailability /><p className="text-sm text-muted-foreground">분석은 돌봄을 돕는 참고 정보예요. 아기의 상태는 보호자가 직접 확인해 주세요.</p></div>
    </div>
    <DemoOnly><details className="preview-tools"><summary>DEMO · 결과 상태 살펴보기</summary><div className="mt-3 flex flex-wrap gap-2">{examples.map((example) => <ActionButton key={example.scenario} variant="neutralWeak" size="small" asChild><Link href={`/babies/${babyId}/results/${example.scenario}`}>{example.label}</Link></ActionButton>)}</div></details></DemoOnly>
  </div>;
}

