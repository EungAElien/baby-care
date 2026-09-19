"use client";

// 개발계약 §2: "탈퇴 후에도 본인의 학습 동의 조회·철회와 본인이 신청한
// 삭제 작업 조회·본인 기여자료 삭제 신청은 가능하다." 이 화면은 아기
// 멤버십과 무관하게 열려야 하므로 /babies/[babyId] 가드 밖에 둔다.
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMockSessionOptional } from "@/lib/mock/session";
import { getMockScenario } from "@/lib/mock/fixtures";
import { ScreenSection } from "@/components/screen-state";

export default function AccountPage() {
  const router = useRouter();
  const session = useMockSessionOptional();
  const alias = session?.alias ?? null;

  useEffect(() => {
    if (!alias) router.replace("/login");
  }, [alias, router]);

  if (!alias) return null;

  const isRemoved = alias === "removed_a";
  const revoked = isRemoved
    ? (getMockScenario("consent_revoke_after_leave").response.body as {
        status: string;
        revoked_at: string | null;
      })
    : null;

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-4 px-6 py-10">
      <h1 className="text-lg font-semibold text-foreground">내 계정</h1>
      <p className="text-sm text-muted-foreground">
        아기 접근 권한과 별개로 본인의 학습 참여·삭제 신청만 관리하는 화면이에요. 공동 기록은 다시 열리지 않아요.
      </p>

      <ScreenSection title="개인 학습 참여 (CONTRIBUTOR_TRAINING)">
        {revoked ? (
          <>
            <p className="text-sm text-foreground">상태: {revoked.status}</p>
            <p className="text-xs text-muted-foreground">
              {revoked.revoked_at && `${new Date(revoked.revoked_at).toLocaleString("ko-KR")}에 철회했어요.`} 이후
              신규 수집·학습 내보내기가 차단돼요.
            </p>
          </>
        ) : (
          <>
            <p className="text-sm text-foreground">기본 꺼짐 (NOT_GRANTED)</p>
            <button type="button" disabled className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-muted-foreground">
              철회 (참여 중이 아니에요)
            </button>
          </>
        )}
      </ScreenSection>

      <ScreenSection title="본인 기여자료 삭제">
        <p className="text-sm text-muted-foreground">
          본인이 작성한 자료의 삭제를 신청할 수 있어요. 다른 사람의 독립 기록은 유지돼요.
        </p>
        <button type="button" disabled className="min-h-11 w-fit rounded-md border border-border px-4 text-sm text-muted-foreground">
          삭제 신청 (실제 API 연동 이후)
        </button>
      </ScreenSection>
    </main>
  );
}
