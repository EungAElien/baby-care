"use client";

import { useRef, useState } from "react";
import { usePrivateScope } from "@/components/app-providers";
import { ErrorState, ScreenSection } from "@/components/screen-state";
import { createReauthenticationChallenge, createReauthenticationProof } from "@/lib/api/account-security";
import type { ReauthenticationChallenge, ReauthenticationOperation } from "@/lib/api/account-security";
import { newClientRequestId } from "@/lib/api/client";
import { ContractApiError } from "@/lib/api/errors";
import { useApiClient } from "@/lib/api/real-client";
import { useRealSession } from "@/lib/auth/real-session";

type Stage = "start" | "otp" | "retry" | "done";

function safeError(error: unknown): string {
  if (error instanceof ContractApiError) {
    if (error.kind === "reauth-required") return "새 인증이 필요해요. 다시 시작해 주세요.";
    if (error.kind === "rate-limit") return "요청이 제한됐어요. 안내된 시간이 지난 뒤 다시 시도해 주세요.";
    return error.envelope.message;
  }
  return "요청 결과를 확인하지 못했어요. 같은 작업을 다시 확인하거나 처음부터 시작해 주세요.";
}

/** Proof stays in this component's memory and is passed directly to its bound mutation. */
export function ReauthenticationPanel({ babyId, operation, title, onProof }: Readonly<{
  babyId: string;
  operation: ReauthenticationOperation;
  title: string;
  onProof: (proofToken: string) => Promise<void>;
}>) {
  const client = useApiClient();
  const real = useRealSession();
  const scope = usePrivateScope();
  const [stage, setStage] = useState<Stage>("start");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const challenge = useRef<ReauthenticationChallenge | null>(null);
  const proof = useRef<string | null>(null);
  const expectedUserId = useRef(real.userId);

  function stillInScope(): boolean {
    const now = scope.snapshot();
    return real.userId !== null && real.userId === expectedUserId.current &&
      now.userId === real.userId && now.babyId === babyId;
  }

  async function start() {
    if (!client || !real.email || !stillInScope()) return;
    setBusy(true);
    setError(null);
    challenge.current = null;
    proof.current = null;
    try {
      const created = await createReauthenticationChallenge(client, operation, babyId, newClientRequestId());
      if (!stillInScope()) return;
      challenge.current = created;
      const sent = await real.requestOtp(real.email);
      if (!sent.ok) throw new Error(sent.error ?? "인증코드를 보내지 못했어요.");
      if (stillInScope()) setStage("otp");
    } catch (failure) {
      if (stillInScope()) setError(safeError(failure));
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: React.FormEvent) {
    event.preventDefault();
    if (!client || !real.email || !challenge.current || !stillInScope()) return;
    setBusy(true);
    setError(null);
    try {
      const verified = await real.verifyOtp(real.email, code);
      setCode("");
      if (!verified.ok) throw new Error(verified.error ?? "인증코드가 올바르지 않아요.");
      if (!stillInScope()) return;
      proof.current = await createReauthenticationProof(client, challenge.current, newClientRequestId());
      if (!stillInScope()) return;
      await onProof(proof.current);
      proof.current = null;
      setStage("done");
    } catch (failure) {
      if (stillInScope()) {
        setError(safeError(failure));
        setStage(proof.current ? "retry" : "start");
      }
    } finally {
      setBusy(false);
    }
  }

  async function retry() {
    if (!proof.current || !stillInScope()) return;
    setBusy(true);
    setError(null);
    try {
      await onProof(proof.current);
      proof.current = null;
      setStage("done");
    } catch (failure) {
      if (stillInScope()) setError(safeError(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ScreenSection title={title}>
      <p className="text-sm text-muted-foreground">
        이 작업은 새 이메일 인증이 필요해요. 인증은 10분 안에 마치고, 확인 후 바로 작업을 보냅니다.
      </p>
      {stage === "start" && (
        <button type="button" onClick={start} disabled={busy || !client || !real.email}
          className="min-h-11 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-50">
          {busy ? "준비 중…" : "새 인증코드 받기"}
        </button>
      )}
      {stage === "otp" && (
        <form onSubmit={verify} className="flex flex-col gap-2">
          <label className="text-sm text-foreground" htmlFor="reauth-code">이메일 인증코드</label>
          <input id="reauth-code" type="text" inputMode="numeric" required autoComplete="one-time-code"
            value={code} onChange={(event) => setCode(event.target.value)}
            className="min-h-11 rounded-md border border-border bg-background px-3 text-sm" />
          <button type="submit" disabled={busy || !code}
            className="min-h-11 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-50">
            {busy ? "확인 중…" : "인증하고 작업 실행"}
          </button>
        </form>
      )}
      {stage === "retry" && (
        <button type="button" onClick={retry} disabled={busy}
          className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">
          같은 요청 결과 다시 확인
        </button>
      )}
      {stage === "done" && <p className="text-sm text-primary">요청을 처리했어요.</p>}
      {error && <ErrorState label={error} retryable={stage === "retry"} />}
    </ScreenSection>
  );
}
