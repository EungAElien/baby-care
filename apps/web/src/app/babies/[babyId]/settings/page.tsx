"use client";

// SC08 설정 — 프로필·동의·보관·데이터 삭제·공동양육 진입점. OWNER/CAREGIVER
// 조작 구분은 개발계약 §2 권한표를 따른다. 실제 저장·삭제는 A-03에서 연결한다.
import Link from "next/link";
import { useSearchParams, useParams } from "next/navigation";
import { mockBabyLabel, mockDeletionJob } from "@/lib/mock/fixtures";
import { useMockSession } from "@/lib/mock/session";
import { ScreenSection, ErrorState } from "@/components/screen-state";

const consentScopes = ["SERVICE_PROCESSING", "AUDIO_RETENTION", "BABY_TRAINING"] as const;
const consentLabel: Record<(typeof consentScopes)[number], string> = {
  SERVICE_PROCESSING: "서비스 분석과 기록 처리",
  AUDIO_RETENTION: "음원 재생을 위한 보관",
  BABY_TRAINING: "공통 모델 개선 참여",
};

const deletionScenarioByState = {
  accepted: "deletion_accepted",
  failed: "deletion_failed",
  complete: "deletion_complete",
} as const;

export default function SettingsPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const searchParams = useSearchParams();
  const session = useMockSession();
  const membership = session.membershipFor(babyId);
  const deletionState = (searchParams.get("deletion") ?? "accepted") as keyof typeof deletionScenarioByState;
  const deletionJob = mockDeletionJob(deletionScenarioByState[deletionState] ?? "deletion_accepted");

  if (!membership) return null;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">설정</h1>

      <ScreenSection title="프로필">
        <p className="text-sm text-foreground">{mockBabyLabel(babyId)}</p>
        <p className="text-xs text-muted-foreground">공통 값 변경은 관리 보호자만 할 수 있어요.</p>
      </ScreenSection>

      <ScreenSection title="동의">
        {consentScopes.map((scope) => (
          <div key={scope} className="flex items-center justify-between">
            <span className="text-sm text-foreground">{consentLabel[scope]}</span>
            <span className="text-xs text-muted-foreground">기본 꺼짐 (NOT_GRANTED)</span>
          </div>
        ))}
      </ScreenSection>

      <ScreenSection title="데이터 삭제 진행 상태">
        {deletionJob.status === "FAILED" ? (
          <ErrorState label="일부 자료 정리 중이에요" retryable={deletionJob.failure?.retryable} />
        ) : (
          <p className="text-sm text-foreground">
            {deletionJob.status === "COMPLETE" ? "삭제를 완료했어요" : "삭제를 진행하고 있어요"}
          </p>
        )}
        {deletionJob.pending_categories.length > 0 && (
          <p className="text-xs text-muted-foreground">남은 항목: {deletionJob.pending_categories.join(", ")}</p>
        )}
        <div className="flex gap-2 text-xs">
          {(Object.keys(deletionScenarioByState) as (keyof typeof deletionScenarioByState)[]).map((key) => (
            <Link
              key={key}
              href={`/babies/${babyId}/settings?deletion=${key}`}
              className="underline text-muted-foreground"
            >
              {key}
            </Link>
          ))}
        </div>
      </ScreenSection>

      <Link href={`/babies/${babyId}/care-team`} className="text-sm font-medium text-primary">
        공동양육 관리로 이동
      </Link>

      {membership.role === "OWNER" ? (
        <button type="button" disabled className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive">
          아기 전체 삭제 요청 (확인 화면 이후 작업에서 연결)
        </button>
      ) : (
        <button type="button" disabled className="min-h-11 rounded-md border border-border px-4 text-sm text-muted-foreground">
          이 아기에서 나가기
        </button>
      )}
    </div>
  );
}
