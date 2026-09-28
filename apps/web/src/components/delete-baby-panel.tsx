"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { usePrivateScope } from "@/components/app-providers";
import { ReauthenticationPanel } from "@/components/reauthentication-panel";
import { ScreenSection } from "@/components/screen-state";
import { useApiClient } from "@/lib/api/real-client";
import { deleteBabyData } from "@/lib/api/baby-deletion";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useRealSession } from "@/lib/auth/real-session";
import type { components } from "@/lib/api/generated";

export function DeleteBabyPanel({ baby }: Readonly<{ baby: components["schemas"]["Baby"] }>) {
  const router = useRouter();
  const real = useRealSession();
  const scope = usePrivateScope();
  const client = useApiClient();
  const [confirm, setConfirm] = useState(false);
  const [intent, setIntent] = useState<{ requestId: string; version: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <ScreenSection title="아기 전체 자료 삭제">
      <p className="text-sm text-muted-foreground">삭제 요청을 받으면 공유 자료 접근이 즉시 차단됩니다. 정리 완료 여부는 별도 작업 화면에서 확인합니다.</p>
      <label className="flex gap-2 text-sm"><input type="checkbox" checked={confirm} onChange={(event) => setConfirm(event.target.checked)} />이 아기의 전체 자료 삭제를 요청하겠습니다.</label>
      <button type="button" disabled={!confirm || !!intent || !client} onClick={() => {
        setError(null);
        setIntent({ requestId: newClientRequestId(), version: baby.version });
      }} className="min-h-11 rounded-md border border-destructive/40 px-4 text-sm text-destructive disabled:opacity-50">
        새 이메일 인증으로 삭제 요청 준비
      </button>
      {intent && (
        <ReauthenticationPanel key={intent.requestId} babyId={baby.baby_id} operation="DELETE_BABY" title="전체 삭제 재인증"
          onProof={async (proofToken) => {
            if (!client || !real.userId) return;
            try {
              const job = await deleteBabyData(client, baby.baby_id, real.userId,
                intent.version, proofToken, intent.requestId);
              scope.set(real.userId, null);
              router.replace(`/account/baby-deletions/${job.deletion_job_id}`);
            } catch (failure) {
              if (failure instanceof ContractApiError && failure.kind === "version-conflict") {
                setIntent(null);
                setConfirm(false);
                setError("아기 정보 버전이 변경됐어요. 최신 설정을 다시 열어 확인한 뒤 새 요청을 시작해 주세요.");
              }
              throw failure;
            }
          }} />
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </ScreenSection>
  );
}
