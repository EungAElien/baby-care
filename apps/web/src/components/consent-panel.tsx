"use client";
import { ActionButton } from "@/components/seed-design/ui/action-button";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScreenSection } from "@/components/screen-state";
import { useRealSession } from "@/lib/auth/real-session";
import { useApiClient } from "@/lib/api/real-client";
import { getChildDataVerification, listConsents, latestConsent, setBabyConsent, setMyTrainingConsent } from "@/lib/api/shared-care";
import type { ConsentScope } from "@/lib/api/shared-care";
import { approvedConsentPolicy } from "@/lib/consent-policy";
import { ReauthenticationPanel } from "@/components/reauthentication-panel";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";

const labels: Record<ConsentScope, string> = {
  SERVICE_PROCESSING: "서비스 처리",
  AUDIO_RETENTION: "음원 보관",
  BABY_TRAINING: "아기 자료 학습 참여",
  CONTRIBUTOR_TRAINING: "내 기여자료 학습 참여",
  SHARED_USE: "공동 사용 수락",
};
const babyScopes = ["SERVICE_PROCESSING", "AUDIO_RETENTION", "BABY_TRAINING"] as const;

type PendingRevocation = Readonly<{
  scope: (typeof babyScopes)[number] | "CONTRIBUTOR_TRAINING";
  version: number;
  policyVersion: string;
  requestId: string;
}>;

export function ConsentPanel({ babyId, isOwner, canGrant = true }: Readonly<{
  babyId: string; isOwner: boolean; canGrant?: boolean;
}>) {
  const client = useApiClient();
  const real = useRealSession();
  const [pending, setPending] = useState<PendingRevocation | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [trainingRequest, setTrainingRequest] = useState<{ requestId: string; version: number; policyVersion: string } | null>(null);
  const query = useQuery({
    queryKey: ["private", real.userId, babyId, "consents"],
    queryFn: () => {
      if (!client) throw new Error("Real API client is not configured.");
      return listConsents(client, babyId);
    },
    enabled: !!client && real.status === "signed-in",
  });
  const verification = useQuery({
    queryKey: ["private", real.userId, babyId, "child-data-verification"],
    queryFn: () => {
      if (!client) throw new Error("Real API client is not configured.");
      return getChildDataVerification(client, babyId);
    },
    enabled: !!client && real.status === "signed-in" && isOwner && canGrant,
  });

  async function grant(scope: (typeof babyScopes)[number] | "CONTRIBUTOR_TRAINING", version: number, policyVersion: string, requestId: string) {
    if (!client) return;
    setBusy(true);
    setMessage(null);
    try {
      if (scope === "CONTRIBUTOR_TRAINING") await setMyTrainingConsent(client, babyId, true, policyVersion, version, requestId);
      else await setBabyConsent(client, babyId, scope, true, policyVersion, version, requestId);
      setMessage("동의 상태를 저장했어요. 최신 상태를 다시 확인합니다.");
      void query.refetch();
    } catch (error) {
      setMessage(error instanceof ContractApiError && error.kind === "version-conflict"
        ? "동의 상태가 변경됐어요. 최신 상태를 다시 확인해 주세요."
        : "요청 결과를 확인하지 못했어요. 같은 요청으로 다시 확인할 수 있어요.");
      throw error;
    } finally {
      setBusy(false);
    }
  }

  async function revoke(request: PendingRevocation) {
    if (!client) return;
    setPending(request);
    setBusy(true);
    setMessage(null);
    try {
      if (request.scope === "CONTRIBUTOR_TRAINING") {
        await setMyTrainingConsent(client, babyId, false, request.policyVersion, request.version, request.requestId);
      } else {
        await setBabyConsent(client, babyId, request.scope, false, request.policyVersion, request.version, request.requestId);
      }
      setPending(null);
      setMessage("철회 요청을 저장했어요. 처리 상태를 다시 확인합니다.");
      void query.refetch();
    } catch (error) {
      setMessage(error instanceof ContractApiError && error.kind === "version-conflict"
        ? "동의 상태가 변경됐어요. 최신 상태를 다시 확인해 주세요."
        : "요청 결과를 확인하지 못했어요. 같은 요청으로 다시 확인할 수 있어요.");
    } finally {
      setBusy(false);
    }
  }

  const rows = query.data?.items ?? [];
  const personal = latestConsent(rows, "CONTRIBUTOR_TRAINING", real.userId ?? undefined);
  const sharedUse = latestConsent(rows, "SHARED_USE", real.userId ?? undefined);

  return (
    <ScreenSection title="동의와 보관">
      {query.isLoading && <p className="text-sm text-muted-foreground">동의 상태를 불러오고 있어요.</p>}
      {query.isError && (
        <ActionButton variant="neutralWeak" type="button" onClick={() => void query.refetch()} className="min-h-11 text-sm text-destructive">
          동의 상태를 불러오지 못했어요. 다시 조회
        </ActionButton>
      )}
      {query.isSuccess && (
        <>
          {babyScopes.map((scope) => {
            const current = latestConsent(rows, scope);
            return (
              <div key={scope} className="flex flex-col gap-1 border-b border-border py-2 text-sm">
                <span className="font-medium text-foreground">{labels[scope]}</span>
                <span className="text-muted-foreground">{current?.status ?? "NOT_GRANTED"}</span>
                {isOwner && current?.status === "GRANTED" && (
                  <ActionButton variant="neutralWeak" type="button" disabled={busy} onClick={() => void revoke({
                    scope, version: current.version, policyVersion: current.policy_version, requestId: newClientRequestId(),
                  })} className="min-h-11 w-fit rounded-md border border-border px-3 text-sm disabled:opacity-50">
                    이 동의 철회
                  </ActionButton>
                )}
                {isOwner && canGrant && current?.status !== "GRANTED" && scope !== "BABY_TRAINING" && (
                  <GrantControl scope={scope} disabled={busy} onGrant={(policyVersion, requestId) =>
                    grant(scope, current?.version ?? 0, policyVersion, requestId)} />
                )}
                {isOwner && canGrant && current?.status !== "GRANTED" && scope === "BABY_TRAINING" && (
                  <>
                    {verification.data?.production_processing_allowed ? (
                      <GrantControl scope={scope} disabled={busy} onGrant={(policyVersion, requestId) => {
                        setTrainingRequest({ policyVersion, requestId, version: current?.version ?? 0 });
                        return Promise.resolve();
                      }} />
                    ) : <p className="text-xs text-muted-foreground">아동 자료 확인과 운영 처리 승인이 완료되기 전에는 참여할 수 없어요.</p>}
                  </>
                )}
              </div>
            );
          })}
          <div className="flex flex-col gap-1 border-b border-border py-2 text-sm">
            <span className="font-medium text-foreground">{labels.CONTRIBUTOR_TRAINING}</span>
            <span className="text-muted-foreground">{personal?.status ?? "NOT_GRANTED"}</span>
            {personal?.status === "GRANTED" && (
              <ActionButton variant="neutralWeak" type="button" disabled={busy} onClick={() => void revoke({
                scope: "CONTRIBUTOR_TRAINING", version: personal.version,
                policyVersion: personal.policy_version, requestId: newClientRequestId(),
              })} className="min-h-11 w-fit rounded-md border border-border px-3 text-sm disabled:opacity-50">
                내 학습 참여 철회
              </ActionButton>
            )}
            {canGrant && personal?.status !== "GRANTED" && (
              <GrantControl scope="CONTRIBUTOR_TRAINING" disabled={busy}
                onGrant={(policyVersion, requestId) => grant("CONTRIBUTOR_TRAINING", personal?.version ?? 0, policyVersion, requestId)} />
            )}
          </div>
          <p className="text-sm text-muted-foreground">{labels.SHARED_USE}: {sharedUse?.status ?? "NOT_GRANTED"}</p>
          <p className="text-xs text-muted-foreground">
            새 동의는 승인된 안내 문구와 정책 버전을 확인한 뒤 받을 수 있어요. 이메일 인증이나 공동 사용 수락은 학습 동의가 아니에요.
          </p>
        </>
      )}
      {pending && (
        <ActionButton variant="neutralWeak" type="button" disabled={busy} onClick={() => void revoke(pending)}
          className="min-h-11 rounded-md border border-border px-3 text-sm disabled:opacity-50">
          같은 철회 요청 다시 확인
        </ActionButton>
      )}
      {trainingRequest && (
        <ReauthenticationPanel key={trainingRequest.requestId} babyId={babyId} operation="ENABLE_BABY_TRAINING"
          title="아기 자료 학습 참여 재인증" onProof={async (proofToken) => {
            if (!client) return;
            await setBabyConsent(client, babyId, "BABY_TRAINING", true, trainingRequest.policyVersion,
              trainingRequest.version, trainingRequest.requestId, proofToken);
            setTrainingRequest(null);
            void query.refetch();
          }} />
      )}
      {message && <p role="status" className="text-sm text-foreground">{message}</p>}
    </ScreenSection>
  );
}

function GrantControl({ scope, disabled, onGrant }: Readonly<{
  scope: ConsentScope; disabled: boolean;
  onGrant: (policyVersion: string, requestId: string) => Promise<void>;
}>) {
  const policy = approvedConsentPolicy(scope);
  const [checked, setChecked] = useState(false);
  const [requestId, setRequestId] = useState(() => newClientRequestId());
  const [retry, setRetry] = useState(false);
  if (!policy) return <p className="text-xs text-muted-foreground">승인된 안내 문구와 정책 버전 설정을 기다리고 있어요.</p>;
  return (
    <div className="flex flex-col gap-2">
      <p className="whitespace-pre-wrap text-sm text-foreground">{policy.text}</p>
      <p className="text-xs text-muted-foreground">정책 버전: {policy.version}</p>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />이 범위에 동의합니다.</label>
      <ActionButton variant="neutralWeak" type="button" disabled={disabled || !checked} onClick={() => {
        void onGrant(policy.version, requestId).then(() => setRetry(false), () => setRetry(true));
      }} className="min-h-11 w-fit rounded-md border border-border px-3 text-sm disabled:opacity-50">
        {retry ? "같은 요청 다시 확인" : "동의하기"}
      </ActionButton>
      {retry && <ActionButton variant="neutralWeak" type="button" onClick={() => { setRequestId(newClientRequestId()); setRetry(false); }} className="min-h-11 w-fit text-xs">새 요청으로 시작</ActionButton>}
    </div>
  );
}
