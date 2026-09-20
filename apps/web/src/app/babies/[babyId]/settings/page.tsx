"use client";

// SC08 설정 — 프로필·동의·보관·데이터 삭제·공동양육 진입점. OWNER/CAREGIVER
// 조작 구분은 개발계약 §2 권한표를 따른다. 실제 저장·삭제는 A-03에서 연결한다.
import Link from "next/link";
import { useState } from "react";
import { useSearchParams, useParams } from "next/navigation";
import { isForBaby, mockBabyLabel, mockDeletionJob, mockErrorEnvelope } from "@/lib/mock/fixtures";
import { useMockSessionOptional } from "@/lib/mock/session";
import { useRealSession } from "@/lib/auth/real-session";
import { ScreenSection, ErrorState, LoadingState, PermissionState } from "@/components/screen-state";
import { ConsentPanel } from "@/components/consent-panel";
import { DeleteBabyPanel } from "@/components/delete-baby-panel";
import { findBabyAccess, useBabiesQuery } from "@/lib/api/babies";

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

type ProfileAttempt = "IDLE" | "DENIED" | "ACCEPTED";

export default function SettingsPage() {
  const { babyId } = useParams<{ babyId: string }>();
  const real = useRealSession();
  if (real.status === "loading") return <LoadingState label="계정을 확인하고 있어요" />;
  if (real.status === "signed-in") return <RealSettings babyId={babyId} />;
  return <MockSettings babyId={babyId} />;
}

function RealSettings({ babyId }: Readonly<{ babyId: string }>) {
  const babies = useBabiesQuery(true);
  const access = findBabyAccess(babies.data, babyId);
  if (babies.isLoading) return <LoadingState label="설정을 불러오고 있어요" />;
  if (babies.isError || !access) return <ErrorState label="설정을 불러오지 못했어요." retryable />;
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">설정</h1>
      <ScreenSection title="아기 설정">
        <p className="text-sm text-muted-foreground">
          프로필 변경 화면은 아직 실제 API와 연결되지 않았어요.
        </p>
        <Link href={`/babies/${babyId}/care-team`} className="text-sm font-medium text-primary">
          공동양육 관리로 이동
        </Link>
      </ScreenSection>
      <ConsentPanel babyId={babyId} isOwner={access.membership.role === "OWNER"} />
      {access.membership.role === "OWNER" && <DeleteBabyPanel baby={access.baby} />}
      <Link href="/account" className="text-sm font-medium text-primary">
        내 계정으로 이동
      </Link>
    </div>
  );
}

function MockSettings({ babyId }: Readonly<{ babyId: string }>) {
  const searchParams = useSearchParams();
  const session = useMockSessionOptional();
  const membership = session?.membershipFor(babyId);
  const deletionState = (searchParams.get("deletion") ?? "accepted") as keyof typeof deletionScenarioByState;
  const deletionJobFixture = mockDeletionJob(deletionScenarioByState[deletionState] ?? "deletion_accepted");
  const deletionJob = isForBaby(babyId, deletionJobFixture) ? deletionJobFixture : null;
  const [profileAttempt, setProfileAttempt] = useState<ProfileAttempt>("IDLE");
  const ownerOnlyError = mockErrorEnvelope("owner_required");

  if (!membership) return null;

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">설정</h1>

      <ScreenSection title="프로필">
        <p className="text-sm text-foreground">{mockBabyLabel(babyId)}</p>
        <p className="text-xs text-muted-foreground">공통 값 변경은 관리 보호자만 할 수 있어요.</p>
        <button
          type="button"
          onClick={() => setProfileAttempt(membership.role === "OWNER" ? "ACCEPTED" : "DENIED")}
          className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-foreground"
        >
          별칭 저장 시도 (예시)
        </button>
        {profileAttempt === "DENIED" && <PermissionState label={ownerOnlyError.message} />}
        {profileAttempt === "ACCEPTED" && (
          <p className="text-sm text-primary">요청을 보냈어요 (예시 — 실제 저장은 연결되지 않았어요)</p>
        )}
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
        {!deletionJob ? (
          <p className="text-sm text-muted-foreground">이 아기의 삭제 작업이 없어요.</p>
        ) : deletionJob.status === "FAILED" ? (
          <ErrorState label="일부 자료 정리 중이에요" retryable={deletionJob.failure?.retryable} />
        ) : (
          <p className="text-sm text-foreground">
            {deletionJob.status === "COMPLETE" ? "삭제를 완료했어요" : "삭제를 진행하고 있어요"}
          </p>
        )}
        {deletionJob && deletionJob.pending_categories.length > 0 && (
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
