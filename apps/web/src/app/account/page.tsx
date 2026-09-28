"use client";

// 개발계약 §2: "탈퇴 후에도 본인의 학습 동의 조회·철회와 본인이 신청한
// 삭제 작업 조회·본인 기여자료 삭제 신청은 가능하다." 이 화면은 아기
// 멤버십과 무관하게 열려야 하므로 /babies/[babyId] 가드 밖에 둔다.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivateScope } from "@/components/app-providers";
import { useRealSession } from "@/lib/auth/real-session";
import { useApiClient } from "@/lib/api/real-client";
import { revokeSessions, getSessionRevocation } from "@/lib/api/account-security";
import type { SessionRevocation } from "@/lib/api/account-security";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { revokeAndSignOut } from "@/lib/auth/session-control";
import { useMockSessionOptional } from "@/lib/mock/session";
import { getMockScenario } from "@/lib/mock/fixtures";
import { LoadingState, ScreenSection } from "@/components/screen-state";
import { ConsentPanel } from "@/components/consent-panel";

export default function AccountPage() {
  const real = useRealSession();
  if (real.status === "loading") return <LoadingState label="계정 정보를 확인하고 있어요" />;
  if (real.status === "signed-in") return <RealAccountPage />;
  return <MockAccountPage />;
}

function RealAccountPage() {
  const router = useRouter();
  const real = useRealSession();
  const scope = usePrivateScope();
  const client = useApiClient();
  const [pending, setPending] = useState(false);
  const [othersRequestId, setOthersRequestId] = useState<string | null>(null);
  const [revocation, setRevocation] = useState<SessionRevocation | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [historyBabyId, setHistoryBabyId] = useState<string | null>(null);

  useEffect(() => {
    const value = new URL(window.location.href).searchParams.get("baby_id");
    if (value && /^[0-9a-f-]{36}$/i.test(value)) queueMicrotask(() => setHistoryBabyId(value));
  }, []);

  async function revokeOthers() {
    if (!client) return;
    const requestId = othersRequestId ?? newClientRequestId();
    setOthersRequestId(requestId);
    setPending(true);
    setMessage(null);
    try {
      const result = await revokeSessions(client, "OTHERS", requestId);
      setRevocation(result);
      setOthersRequestId(null);
    } catch (error) {
      if (error instanceof ContractApiError && error.envelope.code === "AUTH_PROVIDER_REVOCATION_FAILED") {
        const id = error.envelope.details.session_revocation_id;
        if (typeof id === "string") {
          try { setRevocation(await getSessionRevocation(client, id)); } catch { /* retain the partial result message */ }
        }
        setMessage("서버 접근은 차단했지만 제공자 세션 회수가 완료되지 않았어요. 상태를 다시 확인해 주세요.");
      } else {
        setMessage("서버 결과를 확인하지 못했어요. 같은 요청으로 다시 확인할 수 있어요.");
      }
    } finally {
      setPending(false);
    }
  }

  async function revokeAll() {
    setPending(true);
    const outcome = await revokeAndSignOut(client, scope, real.signOut, "ALL");
    setPending(false);
    if (outcome === "local-failed") {
      setMessage("이 기기의 로그아웃을 확인하지 못했어요. 다시 시도해 주세요.");
      return;
    }
    router.replace(`/login?logout=${outcome}`);
  }

  async function refreshRevocation() {
    if (!client || !revocation) return;
    setPending(true);
    setMessage(null);
    try {
      setRevocation(await getSessionRevocation(client, revocation.revocation_id));
    } catch {
      setMessage("회수 상태를 확인하지 못했어요. 다시 조회해 주세요.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col gap-4 px-6 py-10">
      <h1 className="text-lg font-semibold text-foreground">내 계정</h1>
      <p className="text-sm text-muted-foreground">세션 회수는 공동 기록 삭제나 학습 동의 철회와 별개예요.</p>
      <ScreenSection title="로그인된 기기 관리">
        <p className="text-sm text-muted-foreground">다른 기기를 회수해도 이 기기는 계속 로그인돼 있어요.</p>
        <button type="button" onClick={revokeOthers} disabled={pending || !client}
          className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
          {othersRequestId ? "같은 요청 다시 확인" : "다른 기기 세션 회수"}
        </button>
        {revocation && (
          <>
            <p role="status" className="text-sm text-foreground">
              회수 상태: {revocation.status === "COMPLETE" ? "완료" : revocation.status === "FAILED" ? "제공자 회수 실패" : "진행 중"}
            </p>
            <button type="button" disabled={pending} onClick={() => void refreshRevocation()}
              className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
              회수 상태 다시 확인
            </button>
          </>
        )}
        <button type="button" onClick={revokeAll} disabled={pending || !client}
          className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive disabled:opacity-50">
          모든 기기에서 로그아웃
        </button>
        {message && <p role="alert" className="text-sm text-destructive">{message}</p>}
      </ScreenSection>
      <ScreenSection title="개인 학습 참여">
        <p className="text-sm text-muted-foreground">탈퇴 뒤에도 본인 동의 이력과 철회는 계속 확인할 수 있어요.</p>
        <label htmlFor="consent-history-baby" className="text-sm text-foreground">이력을 확인할 아기 ID</label>
        <input id="consent-history-baby" type="text" value={historyBabyId ?? ""}
          onChange={(event) => setHistoryBabyId(event.target.value)}
          className="min-h-11 rounded-md border border-border bg-background px-3 text-sm" />
      </ScreenSection>
      {historyBabyId && /^[0-9a-f-]{36}$/i.test(historyBabyId) && (
        <ConsentPanel babyId={historyBabyId} isOwner={false} canGrant={false} />
      )}
    </main>
  );
}

function MockAccountPage() {
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
