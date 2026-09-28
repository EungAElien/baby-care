import Link from "next/link";
import { ScreenSection } from "@/components/screen-state";
import { AudioIntakeScreen } from "@/components/audio-intake";

const resultPreviews = [
  { scenario: "analysis_running", label: "분석 중" },
  { scenario: "analysis_complete_stub", label: "완료 · 후보 확인" },
  { scenario: "analysis_abstain", label: "판단 유보 (저품질)" },
  { scenario: "analysis_no_cry", label: "울음 미확인" },
  { scenario: "analysis_failed", label: "분석 실패" },
] as const;

export default async function DetectPage({ params }: Readonly<{ params: Promise<{ babyId: string }> }>) {
  const { babyId } = await params;
  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="직접 녹음·파일 입력">
        <AudioIntakeScreen babyId={babyId} />
      </ScreenSection>

      <ScreenSection title="결과 화면 미리보기">
        <p className="text-xs text-muted-foreground">
          제공된 목 응답의 네 가지 분석 상태로 SC04 결과 화면을 확인할 수 있어요.
        </p>
        <ul className="flex flex-col gap-2">
          {resultPreviews.map(({ scenario, label }) => (
            <li key={scenario}>
              <Link
                href={`/babies/${babyId}/results/${scenario}`}
                className="flex min-h-11 items-center rounded-md border border-border px-3 text-sm text-foreground hover:border-primary"
              >
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </ScreenSection>
      {process.env.NODE_ENV === "development" && (
        <Link href={`/babies/${babyId}/audio-measure`} className="text-sm text-primary underline">
          개발용 오디오 측정 화면 열기
        </Link>
      )}
    </div>
  );
}
