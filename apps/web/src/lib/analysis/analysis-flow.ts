// A-06 ①: request/recovery state machine for one logical analysis attempt on
// a READY (episode, audio) pair from A-05. Mirrors lib/audio/intake.ts's
// class-based approach — this flow owns timers (65s initial wait, 2s RUNNING
// poll) and must stop them on dispose, not just on unmount.
//
// 개발계약 §6: A creates analysis_id + client_request_id once per logical
// attempt and reuses the SAME pair as the Idempotency-Key/body until that
// attempt has a known outcome. A lost response is recovered with GET, never
// with a fresh POST. Only an explicit FAILED retry gets a new key.
import type { components } from "@/lib/api/generated";
import type { RealApiClient } from "@/lib/api/real-client";
import { ContractRequestError, NetworkRequestError, RequestCancelledError, idempotencyHeaders } from "@/lib/api/client";
import { ContractApiError, requireData, type ApiErrorKind } from "@/lib/api/errors";
import { ScopeChangedError, type PrivateScope, type PrivateScopeSnapshot } from "@/lib/private-scope";
import { analysisStageLabel } from "@/lib/analysis/labels";

export type Analysis = components["schemas"]["Analysis"];

const INITIAL_WAIT_MS = 65_000;
const POLL_INTERVAL_MS = 2_000;

export type AnalysisPhase =
  | "idle"
  | "requesting"
  | "recovering"
  | "polling"
  | "resolved"
  | "blocked"
  | "error";

export type AnalysisView = Readonly<{
  phase: AnalysisPhase;
  message: string;
  analysis: Analysis | null;
  errorKind: ApiErrorKind | "network" | "unknown" | null;
  errorCode: string | null;
  retryAfterSeconds: number | null;
}>;

const initialView: AnalysisView = {
  phase: "idle",
  message: "분석을 시작할 준비가 됐어요.",
  analysis: null,
  errorKind: null,
  errorCode: null,
  retryAfterSeconds: null,
};

function resolvedMessage(analysis: Analysis): string {
  if (analysis.status === "COMPLETE") return "분석이 끝났어요.";
  if (analysis.status === "ABSTAIN") return "판단을 유보했어요.";
  if (analysis.status === "FAILED") return "분석을 완료하지 못했어요.";
  return "분석 상태를 확인했어요.";
}

function apiErrorMessage(error: ContractApiError): string {
  const code = error.envelope.code;
  if (code === "MODEL_NOT_READY") return "분석 모델을 아직 제품에 사용할 수 없어요. 기록하거나 나중에 다시 시도해 주세요.";
  if (error.kind === "authentication") return "로그인이 만료됐어요. 다시 로그인해 주세요.";
  if (error.kind === "reauth-required") return "이 작업에는 다시 확인이 필요해요.";
  if (error.kind === "permission") return "이 아기의 분석 권한을 확인할 수 없어요.";
  if (error.kind === "not-found") return "분석을 찾을 수 없거나 접근할 수 없어요.";
  if (error.kind === "gone") return "원본 음원이나 분석 자료가 삭제됐어요.";
  if (error.kind === "idempotency-conflict") return "이전 요청과 내용이 달라요. 새로 시작해야 해요.";
  if (error.kind === "rate-limit") {
    return error.retryAfterSeconds !== null ? `요청이 제한됐어요. ${error.retryAfterSeconds}초 뒤 다시 시도할 수 있어요.` : "요청이 제한됐어요. 잠시 뒤 다시 시도해 주세요.";
  }
  if (error.kind === "service") return "서버에 일시 장애가 있어요. 같은 분석으로 다시 확인해 주세요.";
  if (error.kind === "version-conflict") return "분석 상태가 이미 바뀌었어요. 최신 상태를 다시 확인해 주세요.";
  return "요청을 처리하지 못했어요.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** One tab-local logical analysis attempt for a given (episode, audio) pair. */
export class AnalysisFlow {
  private view: AnalysisView = initialView;
  private listener: (view: AnalysisView) => void;
  private analysisId: string | null = null;
  private clientRequestId: string | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private waitTimer: ReturnType<typeof setTimeout> | null = null;
  private generation = 0;
  private disposed = false;
  private lastAction: (() => void) | null = null;
  private readonly snapshot: PrivateScopeSnapshot;

  constructor(
    private readonly babyId: string,
    private readonly episodeId: string,
    private readonly audioId: string,
    private readonly client: RealApiClient,
    private readonly scope: PrivateScope,
    listener: (view: AnalysisView) => void,
  ) {
    this.snapshot = scope.snapshot();
    this.listener = listener;
    listener(this.view);
  }

  private set(patch: Partial<AnalysisView>): void {
    if (this.disposed) return;
    this.view = { ...this.view, ...patch };
    this.listener(this.view);
  }

  private assertCurrent(): void {
    this.scope.assertCurrent(this.snapshot);
    if (!this.snapshot.userId || this.snapshot.babyId !== this.babyId) {
      throw new ContractRequestError("현재 아기 범위가 바뀌었어요.");
    }
  }

  private clearTimers(): void {
    if (this.pollTimer) { clearTimeout(this.pollTimer); this.pollTimer = null; }
    if (this.waitTimer) { clearTimeout(this.waitTimer); this.waitTimer = null; }
  }

  /** Begins a brand-new logical analysis attempt with a fresh analysis_id/client_request_id. */
  start(): void {
    this.assertCurrent();
    this.analysisId = crypto.randomUUID();
    this.clientRequestId = crypto.randomUUID();
    this.lastAction = () => void this.sendCreate();
    this.lastAction();
  }

  /** Only valid from a stored FAILED result: same analysis_id, a fresh key, current attempt_no. */
  retryFailed(): void {
    this.assertCurrent();
    const analysis = this.view.analysis;
    if (!analysis || analysis.status !== "FAILED" || analysis.analysis_id !== this.analysisId) {
      throw new ContractRequestError("FAILED 상태의 같은 분석에서만 재시도할 수 있어요.");
    }
    this.clientRequestId = crypto.randomUUID();
    const expectedAttempt = analysis.attempt_no;
    this.lastAction = () => void this.sendRetry(expectedAttempt);
    this.lastAction();
  }

  /** User-triggered continuation from a blocked (429/503) or error phase — reuses the last logical action. */
  resume(): void {
    if (this.disposed) return;
    if (this.view.phase !== "blocked" && this.view.phase !== "error") return;
    this.lastAction?.();
  }

  private async sendCreate(): Promise<void> {
    const generation = ++this.generation;
    const analysisId = this.analysisId;
    const clientRequestId = this.clientRequestId;
    if (!analysisId || !clientRequestId) return;
    this.set({
      phase: "requesting", message: "분석을 요청했어요. 서버 응답을 기다리고 있어요.",
      errorKind: null, errorCode: null, retryAfterSeconds: null,
    });
    this.waitTimer = setTimeout(() => {
      if (this.generation !== generation || this.disposed) return;
      this.lastAction = () => void this.pollOnce();
      void this.recover("최초 65초 동안 응답을 받지 못했어요. 같은 분석을 다시 확인하고 있어요.");
    }, INITIAL_WAIT_MS);
    try {
      const analysis = requireData(await this.client.POST("/episodes/{episode_id}/analyses", {
        params: { path: { episode_id: this.episodeId }, header: idempotencyHeaders(clientRequestId) },
        body: { client_request_id: clientRequestId, analysis_id: analysisId, audio_id: this.audioId },
      }));
      if (this.generation !== generation || this.disposed) return;
      if (this.waitTimer) { clearTimeout(this.waitTimer); this.waitTimer = null; }
      this.assertCurrent();
      this.applyAnalysis(analysis);
    } catch (error) {
      if (this.generation !== generation || this.disposed) return;
      if (this.waitTimer) { clearTimeout(this.waitTimer); this.waitTimer = null; }
      this.handleRequestError(error);
    }
  }

  private async sendRetry(expectedAttempt: number): Promise<void> {
    const generation = ++this.generation;
    const analysisId = this.analysisId;
    const clientRequestId = this.clientRequestId;
    if (!analysisId || !clientRequestId) return;
    this.set({
      phase: "requesting", message: "실패한 분석을 같은 ID로 다시 시도하고 있어요.",
      errorKind: null, errorCode: null, retryAfterSeconds: null,
    });
    this.waitTimer = setTimeout(() => {
      if (this.generation !== generation || this.disposed) return;
      this.lastAction = () => void this.pollOnce();
      void this.recover("최초 65초 동안 응답을 받지 못했어요. 같은 분석을 다시 확인하고 있어요.");
    }, INITIAL_WAIT_MS);
    try {
      const analysis = requireData(await this.client.POST("/analyses/{analysis_id}/retry", {
        params: { path: { analysis_id: analysisId }, header: idempotencyHeaders(clientRequestId) },
        body: { client_request_id: clientRequestId, expected_attempt: expectedAttempt },
      }));
      if (this.generation !== generation || this.disposed) return;
      if (this.waitTimer) { clearTimeout(this.waitTimer); this.waitTimer = null; }
      this.assertCurrent();
      this.applyAnalysis(analysis);
    } catch (error) {
      if (this.generation !== generation || this.disposed) return;
      if (this.waitTimer) { clearTimeout(this.waitTimer); this.waitTimer = null; }
      this.handleRequestError(error);
    }
  }

  private async recover(reason: string): Promise<void> {
    if (this.disposed) return;
    this.set({ phase: "recovering", message: reason, errorKind: null, errorCode: null, retryAfterSeconds: null });
    await this.pollOnce();
  }

  private async pollOnce(): Promise<void> {
    if (this.disposed) return;
    // Bumping generation here (not just in sendCreate/sendRetry) fences a
    // stale in-flight POST that resolves after the 65s wait already switched
    // this attempt over to GET-based recovery.
    const generation = ++this.generation;
    const analysisId = this.analysisId;
    if (!analysisId) return;
    try {
      this.assertCurrent();
      const analysis = requireData(await this.client.GET("/analyses/{analysis_id}", {
        params: { path: { analysis_id: analysisId } },
      }));
      if (this.generation !== generation || this.disposed) return;
      this.assertCurrent();
      this.applyAnalysis(analysis);
    } catch (error) {
      if (this.generation !== generation || this.disposed) return;
      this.handleRequestError(error);
    }
  }

  private applyAnalysis(analysis: Analysis): void {
    if (analysis.analysis_id !== this.analysisId || analysis.baby_id !== this.babyId || analysis.episode_id !== this.episodeId) {
      throw new ContractRequestError("분석 응답이 요청한 아기·사건과 일치하지 않아요.");
    }
    if (this.pollTimer) { clearTimeout(this.pollTimer); this.pollTimer = null; }
    this.lastAction = null;
    if (analysis.status === "RUNNING") {
      this.set({
        phase: "polling", analysis,
        message: `분석 중이에요 (${analysisStageLabel[analysis.stage] ?? analysis.stage})`,
        errorKind: null, errorCode: null, retryAfterSeconds: null,
      });
      this.pollTimer = setTimeout(() => { void this.pollOnce(); }, POLL_INTERVAL_MS);
      return;
    }
    this.set({ phase: "resolved", analysis, message: resolvedMessage(analysis), errorKind: null, errorCode: null, retryAfterSeconds: null });
  }

  private handleRequestError(error: unknown): void {
    if (this.disposed) return;
    if (error instanceof RequestCancelledError || error instanceof ScopeChangedError) return;
    if (error instanceof NetworkRequestError) {
      this.lastAction = () => void this.pollOnce();
      void this.recover("응답을 받지 못했어요. 같은 분석을 다시 확인하고 있어요.");
      return;
    }
    if (error instanceof ContractApiError) {
      this.applyApiError(error);
      return;
    }
    this.set({
      phase: "error", message: "알 수 없는 오류가 발생했어요. 같은 화면에서 다시 확인해 주세요.",
      errorKind: "unknown", errorCode: null, retryAfterSeconds: null,
    });
  }

  private applyApiError(error: ContractApiError): void {
    const code = error.envelope.code;
    // Duplicate-in-flight and a stale attempt fence must recover the real
    // resource via GET, never spawn a new analysis_id (개발계약 §8).
    if (code === "ANALYSIS_IN_PROGRESS" || code === "VERSION_CONFLICT") {
      const existingId = isRecord(error.envelope.details) ? error.envelope.details.existing_analysis_id : null;
      if (code === "ANALYSIS_IN_PROGRESS" && typeof existingId === "string") this.analysisId = existingId;
      if (this.analysisId) {
        this.lastAction = () => void this.pollOnce();
        void this.recover(code === "ANALYSIS_IN_PROGRESS" ? "이미 진행 중인 같은 분석을 조회하고 있어요." : "분석 상태가 바뀌었어요. 최신 상태를 다시 확인하고 있어요.");
        return;
      }
    }
    const blockable = error.kind === "rate-limit" || error.kind === "service";
    this.set({
      phase: blockable ? "blocked" : "error",
      message: apiErrorMessage(error),
      errorKind: error.kind,
      errorCode: code,
      retryAfterSeconds: error.retryAfterSeconds,
    });
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.generation++;
    this.clearTimers();
    this.listener = () => {};
  }
}
