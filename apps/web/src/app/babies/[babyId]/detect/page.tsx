// SC03 감지와 녹음 — 마이크 상태 셸. 실제 오디오 캡처는 A-02/A-05에서 연결한다.
// 여기서는 개발계약 §6 상태표에 맞춘 결과 미리보기로 SC04 이동만 확인한다.
import Link from "next/link";
import { ScreenSection } from "@/components/screen-state";

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
      <ScreenSection title="마이크 상태">
        <p className="text-sm text-foreground">IDLE · 아직 시작하지 않았어요</p>
        <p className="text-xs text-muted-foreground">
          시작 버튼을 누르면 권한을 요청한 뒤 최근 3초 버퍼만 유지하며 듣기 시작합니다(실제 캡처는 이후 작업).
        </p>
        <button
          type="button"
          disabled
          className="min-h-11 rounded-md border border-border px-4 text-sm font-medium text-muted-foreground"
        >
          지금 분석 시작 (실제 캡처 미연결)
        </button>
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
    </div>
  );
}
