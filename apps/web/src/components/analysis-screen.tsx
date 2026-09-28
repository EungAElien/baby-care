"use client";

// A-06 ①: the real analysis request/result/recovery screen for a READY
// (episode, audio) pair handed off from A-05. Reads only server-declared
// values (개발계약 §1·§6·§11) — the product gate, inference_mode/data_origin,
// model/preprocess/label-mapping versions and quality warnings are never
// inferred or replaced with a mock on failure.
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { useRealSession } from "@/lib/auth/real-session";
import { useCapabilitiesQuery } from "@/lib/api/capabilities";
import { AnalysisFlow, type Analysis, type AnalysisView } from "@/lib/analysis/analysis-flow";
import { abstainReasonLabel, failureCodeLabel, qualityReasonLabel } from "@/lib/analysis/labels";
import { SourceBadge } from "@/components/source-badge";
import { EmptyState, ErrorState, LoadingState, PermissionState, ScreenSection } from "@/components/screen-state";

const initialView: AnalysisView = {
  phase: "idle", message: "분석을 시작할 준비가 됐어요.", analysis: null,
  errorKind: null, errorCode: null, retryAfterSeconds: null,
};

function VersionInfo({ analysis }: Readonly<{ analysis: Analysis }>) {
  return (
    <div className="flex flex-col gap-1 border-t border-border pt-2 text-xs text-muted-foreground">
      <p>모델 {analysis.model_version ?? "알 수 없음"} · 전처리 {analysis.preprocess_version ?? "알 수 없음"} · 라벨 매핑 {analysis.label_mapping_version ?? "알 수 없음"}</p>
      <p>실제 모델 호출: {analysis.inference_executed ? "예" : "아니오"}</p>
      {analysis.quality_reasons.length > 0 && (
        <p>품질 경고: {analysis.quality_reasons.map((reason) => qualityReasonLabel[reason] ?? reason).join(", ")}</p>
      )}
    </div>
  );
}

export function ResolvedAnalysis({
  babyId, analysis, onRetryFailed,
}: Readonly<{ babyId: string; analysis: Analysis; onRetryFailed: () => void }>) {
  if (analysis.status === "COMPLETE") {
    return (
      <ScreenSection title="가능성 있는 원인">
        <ul className="flex flex-col gap-2">
          {analysis.audio_candidates.slice(0, 3).map((candidate) => (
            <li key={candidate.code} className="rounded-md border border-border px-3 py-2 text-sm text-foreground">
              {candidate.label} <span className="text-xs text-muted-foreground">가능성</span>
            </li>
          ))}
        </ul>
        {analysis.recommendation && analysis.recommendation.status !== "HELP_REQUIRED" && (
          <div className="flex flex-col gap-2 border-t border-border pt-3">
            <p className="text-sm font-medium text-foreground">확인해볼 행동</p>
            {analysis.recommendation.actions.map((action) => (
              <p key={action.text} className="text-sm text-muted-foreground">{action.text}</p>
            ))}
          </div>
        )}
        {analysis.recommendation?.status === "HELP_REQUIRED" && analysis.recommendation.help_action && (
          <p className="text-sm text-foreground">{analysis.recommendation.help_action.text}</p>
        )}
        <Link
          href={`/babies/${babyId}/quick-record`}
          className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
        >
          실제 수행한 행동 기록하기
        </Link>
        <VersionInfo analysis={analysis} />
      </ScreenSection>
    );
  }

  if (analysis.status === "ABSTAIN") {
    return (
      <ScreenSection title="판단을 유보했어요">
        <p className="text-sm text-foreground">
          {analysis.abstain_reason ? abstainReasonLabel[analysis.abstain_reason] ?? analysis.abstain_reason : "사유 없음"}
        </p>
        <p className="text-xs text-muted-foreground">후보를 만들지 않았어요.</p>
        <div className="flex gap-2">
          <Link href={`/babies/${babyId}/detect`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
            다시 녹음하기
          </Link>
          <Link href={`/babies/${babyId}/quick-record`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
            직접 기록하기
          </Link>
        </div>
        <VersionInfo analysis={analysis} />
      </ScreenSection>
    );
  }

  // FAILED
  const failure = analysis.failure;
  return (
    <ScreenSection title="분석을 완료하지 못했어요">
      <ErrorState label={failure ? (failureCodeLabel[failure.code] ?? failure.message) : "알 수 없는 오류"} retryable={failure?.retryable} />
      {failure?.retryable && (
        <button type="button" onClick={onRetryFailed} className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground">
          같은 분석 다시 시도하기 (시도 {analysis.attempt_no}회차)
        </button>
      )}
      <div className="flex gap-2">
        <Link href={`/babies/${babyId}/detect`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
          다시 녹음하기
        </Link>
        <Link href={`/babies/${babyId}/quick-record`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
          직접 기록하기
        </Link>
      </div>
      <VersionInfo analysis={analysis} />
    </ScreenSection>
  );
}

export function AnalysisScreen({
  babyId, episodeId, audioId,
}: Readonly<{ babyId: string; episodeId: string; audioId: string }>) {
  const scope = usePrivateScope();
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope]);
  const getSnapshot = useCallback(() => scope.snapshot(), [scope]);
  const scopeSnapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const client = useApiClient();
  const real = useRealSession();
  const capabilities = useCapabilitiesQuery(real.status === "signed-in");
  const [view, setView] = useState<AnalysisView>(initialView);
  const flow = useRef<AnalysisFlow | null>(null);
  const available = real.status === "signed-in" && client !== null &&
    scopeSnapshot.userId === real.userId && scopeSnapshot.babyId === babyId;

  useEffect(() => {
    if (!available || !client) return;
    const instance = new AnalysisFlow(babyId, episodeId, audioId, client, scope, setView);
    flow.current = instance;
    const cleanup = () => instance.dispose();
    const unregister = scope.registerCleanup(cleanup);
    return () => { unregister(); cleanup(); if (flow.current === instance) flow.current = null; };
  }, [available, babyId, episodeId, audioId, client, scope]);

  if (!available) {
    return <p className="text-sm text-muted-foreground">실제 분석은 로그인과 서버 연결이 있을 때만 시작할 수 있어요.</p>;
  }

  if (capabilities.isLoading) return <LoadingState label="분석 가능 여부를 확인하고 있어요" />;
  if (capabilities.isError) return <ErrorState label="분석 가능 여부를 확인하지 못했어요." retryable />;

  const gateOpen = capabilities.data?.audio_model.available === true;

  return (
    <div className="flex flex-col gap-4">
      {view.analysis && (
        <div className="flex items-center justify-between">
          <SourceBadge inferenceMode={view.analysis.inference_mode} dataOrigin={view.analysis.data_origin} />
          <span className="text-xs text-muted-foreground">시도 {view.analysis.attempt_no}회차</span>
        </div>
      )}

      {view.phase === "idle" && !gateOpen && (
        <ScreenSection title="아직 분석을 시작할 수 없어요">
          <p className="text-sm text-foreground">
            서버가 이 모델을 아직 제품 분석에 사용할 준비를 마치지 않았어요 (MODEL_NOT_READY).
          </p>
          <p className="text-xs text-muted-foreground">장애가 아니라 준비 상태이며, 실제 API 장애를 목 결과로 대체하지 않아요.</p>
          <div className="flex gap-2">
            <Link href={`/babies/${babyId}/quick-record`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
              직접 기록하기
            </Link>
            <Link href={`/babies/${babyId}/detect`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
              다시 녹음하기
            </Link>
          </div>
        </ScreenSection>
      )}

      {view.phase === "idle" && gateOpen && (
        <button
          type="button"
          onClick={() => flow.current?.start()}
          className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
        >
          실제 분석 시작
        </button>
      )}

      {(view.phase === "requesting" || view.phase === "recovering" || view.phase === "polling") && (
        <LoadingState label={view.message} />
      )}

      {view.phase === "blocked" && (
        <ScreenSection title="지금은 진행할 수 없어요">
          <ErrorState label={view.message} retryable />
          {view.retryAfterSeconds !== null && (
            <p className="text-xs text-muted-foreground">서버가 안내한 대기 시간: {view.retryAfterSeconds}초</p>
          )}
          <div className="flex gap-2">
            <button type="button" onClick={() => flow.current?.resume()} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
              다시 시도
            </button>
            <Link href={`/babies/${babyId}/quick-record`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
              직접 기록하기
            </Link>
          </div>
        </ScreenSection>
      )}

      {view.phase === "error" && view.errorKind === "permission" && (
        <PermissionState label={view.message} />
      )}
      {view.phase === "error" && view.errorKind === "not-found" && (
        <EmptyState label={view.message} />
      )}
      {view.phase === "error" && view.errorKind !== "permission" && view.errorKind !== "not-found" && (
        <ScreenSection title="문제가 발생했어요">
          <ErrorState label={view.message} />
          <button type="button" onClick={() => flow.current?.resume()} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
            같은 분석 다시 확인
          </button>
        </ScreenSection>
      )}

      {view.phase === "resolved" && view.analysis && (
        <ResolvedAnalysis babyId={babyId} analysis={view.analysis} onRetryFailed={() => flow.current?.retryFailed()} />
      )}
    </div>
  );
}
