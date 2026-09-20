"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { usePrivateScope } from "@/components/app-providers";
import { ScreenSection } from "@/components/screen-state";
import { EmailOtpForm } from "@/app/login/real-login";
import { acceptInvite } from "@/lib/api/shared-care";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useApiClient } from "@/lib/api/real-client";
import { useRealSession } from "@/lib/auth/real-session";
import { approvedConsentPolicy } from "@/lib/consent-policy";
import { consumeInviteFragment } from "@/lib/invite-link";
import { revokeAndSignOut } from "@/lib/auth/session-control";
import type { components } from "@/lib/api/generated";

type Relationship = components["schemas"]["AcceptInvite"]["relationship"];
const relationships: ReadonlyArray<Readonly<{ value: Relationship; label: string }>> = [
  { value: "MOTHER", label: "어머니" }, { value: "FATHER", label: "아버지" },
  { value: "GRANDPARENT", label: "조부모" }, { value: "OTHER", label: "기타" },
];

export default function IssuedInvitePage() {
  const { inviteId } = useParams<{ inviteId: string }>();
  const router = useRouter();
  const real = useRealSession();
  const client = useApiClient();
  const scope = usePrivateScope();
  const consumed = useRef(false);
  const [secret, setSecret] = useState<{ ready: boolean; token: string | null }>({ ready: false, token: null });
  const [relationship, setRelationship] = useState<Relationship>("OTHER");
  const [agreed, setAgreed] = useState(false);
  const [requestId, setRequestId] = useState(() => newClientRequestId());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const policy = approvedConsentPolicy("SHARED_USE");

  useEffect(() => {
    if (consumed.current) return;
    consumed.current = true;
    setSecret({ ready: true, token: consumeInviteFragment(window.location, window.history) });
  }, []);

  async function submit() {
    if (!client || !real.userId || !secret.token || !policy || !agreed) return;
    setBusy(true);
    setError(null);
    try {
      const accepted = await acceptInvite(client, secret.token, policy.version, relationship, requestId);
      // The accepted baby becomes visible only after the server verifies recipient email and token.
      setSecret({ ready: true, token: null });
      scope.set(real.userId, accepted.baby.baby_id);
      router.replace(`/babies/${accepted.baby.baby_id}`);
    } catch (failure) {
      if (failure instanceof ContractApiError) {
        if (failure.envelope.code === "INVITE_EMAIL_MISMATCH") setError("이 계정은 초대받은 이메일과 달라요. 초대받은 계정으로 로그인해 주세요.");
        else if (failure.kind === "gone" || failure.envelope.code === "INVITE_ALREADY_USED") setError("이 링크는 만료되었거나 이미 사용됐어요. 새 초대를 요청해 주세요.");
        else if (failure.kind === "not-found") setError("초대를 찾을 수 없어요. 새 초대를 요청해 주세요.");
        else setError("수락 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.");
      } else setError("수락 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  if (!secret.ready) return null;
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6">
      <h1 className="text-lg font-semibold text-foreground">공동양육 초대</h1>
      {!secret.token || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(inviteId) ? (
        <ScreenSection title="초대 링크를 확인할 수 없어요"><p className="text-sm">관리 보호자에게 새 링크를 요청해 주세요.</p></ScreenSection>
      ) : real.status === "loading" ? (
        <p className="text-sm">로그인 상태를 확인하고 있어요.</p>
      ) : real.status === "signed-out" ? (
        <>
          <p className="text-sm text-muted-foreground">초대받은 이메일로 로그인하면 이 탭에서 수락을 이어갈 수 있어요.</p>
          <EmailOtpForm />
        </>
      ) : (
        <ScreenSection title="공동 사용 범위 확인">
          {policy ? (
            <>
              <p className="whitespace-pre-wrap text-sm text-foreground">{policy.text}</p>
              <p className="text-xs text-muted-foreground">정책 버전: {policy.version}</p>
              <label className="flex flex-col gap-1 text-sm">아기와의 관계
                <select value={relationship} onChange={(event) => setRelationship(event.target.value as Relationship)}
                  className="min-h-11 rounded-md border border-border bg-background px-3">
                  {relationships.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                </select>
              </label>
              <label className="flex gap-2 text-sm"><input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />공동 사용 범위에 동의합니다.</label>
              <button type="button" onClick={() => void submit()} disabled={!agreed || busy || !client}
                className="min-h-11 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-50">
                {busy ? "확인 중…" : error ? "같은 요청으로 다시 확인" : "초대 수락"}
              </button>
            </>
          ) : <p className="text-sm text-muted-foreground">승인된 공동 사용 안내와 정책 버전이 설정되면 수락할 수 있어요.</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {error?.includes("초대받은 이메일") && (
            <button type="button" disabled={busy} onClick={() => void revokeAndSignOut(client, scope, real.signOut, "CURRENT")}
              className="min-h-11 text-sm text-foreground">이 계정에서 로그아웃하고 다시 로그인</button>
          )}
          {error && <button type="button" onClick={() => { setRequestId(newClientRequestId()); setError(null); }} className="min-h-11 text-sm">새 수락 요청으로 시작</button>}
        </ScreenSection>
      )}
    </main>
  );
}
