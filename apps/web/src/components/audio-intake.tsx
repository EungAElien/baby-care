"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Mic, Square, Upload, FileAudio } from "lucide-react";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { supabaseAuthAdapter } from "@/lib/auth/api-auth-adapter";
import { useRealSession } from "@/lib/auth/real-session";
import { tryReadPublicConfig } from "@/lib/public-config";
import { AudioIntake, type IntakeView } from "@/lib/audio/intake";
import { DirectRecording, readDuration } from "@/lib/audio/recording";
import { ActionButton } from "./seed-design/ui/action-button";
import { Callout } from "./seed-design/ui/callout";
import { ErrorState } from "./screen-state";

const initialView: IntakeView = { stage: "idle", message: "녹음하거나 파일을 선택해 주세요.", progress: 0, episode: null, audio: null, grant: null, source: null };

export function AudioIntakeScreen({ babyId }: Readonly<{ babyId: string }>) {
  const scope = usePrivateScope();
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope]);
  const getSnapshot = useCallback(() => scope.snapshot(), [scope]);
  const scopeSnapshot = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const client = useApiClient();
  const real = useRealSession();
  const config = useMemo(() => tryReadPublicConfig(), []);
  const [revision, setRevision] = useState(0);
  const [view, setView] = useState<IntakeView>(initialView);
  const [captureState, setCaptureState] = useState<"idle" | "requesting" | "recording">("idle");
  const [busy, setBusy] = useState(false);
  const [preferResumable, setPreferResumable] = useState(false);
  const [localError, setLocalError] = useState("");
  const intake = useRef<AudioIntake | null>(null);
  const recorder = useRef<DirectRecording | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const fileInput = useRef<HTMLInputElement | null>(null);
  const selection = useRef(0);
  const operation = useRef(false);
  const cancellation = useRef(false);
  const available = real.status === "signed-in" && client !== null && config !== null &&
    scopeSnapshot.userId === real.userId && scopeSnapshot.babyId === babyId;

  useEffect(() => {
    if (!available || !client || !config) return;
    const session = new AudioIntake(babyId, client, config, supabaseAuthAdapter, scope, setView);
    intake.current = session;
    const cleanup = () => {
      selection.current += 1;
      recorder.current?.abort(); recorder.current = null;
      setCaptureState("idle"); session.dispose();
      operation.current = false; cancellation.current = false; setBusy(false);
      if (intake.current === session) intake.current = null;
    };
    const unregister = scope.registerCleanup(cleanup);
    return () => { unregister(); cleanup(); };
  }, [available, babyId, client, config, scope, scopeSnapshot.generation, revision]);

  async function startRecording() {
    if (recorder.current || operation.current || !intake.current) return;
    selection.current += 1;
    setLocalError("");
    const session = intake.current;
    const capture = new DirectRecording(setCaptureState);
    recorder.current = capture;
    try {
      const result = await capture.start();
      if (recorder.current === capture && intake.current === session) session.selectRecording(result, preferResumable);
    } catch (error) {
      if (recorder.current === capture && intake.current === session) setLocalError(error instanceof Error ? error.message : "녹음에 실패했어요.");
    } finally {
      if (recorder.current === capture) { recorder.current = null; setCaptureState("idle"); }
    }
  }
  async function selectFile(file: File | undefined) {
    if (!file || recorder.current || operation.current || !intake.current) return;
    const current = ++selection.current;
    setLocalError("");
    const session = intake.current;
    try {
      const duration = await readDuration(file);
      if (intake.current === session && selection.current === current) session.select("FILE", file, duration, preferResumable);
    } catch (error) {
      if (intake.current === session && selection.current === current) setLocalError(error instanceof Error ? error.message : "파일을 선택하지 못했어요.");
    }
  }
  async function run(action: (session: AudioIntake) => Promise<void>) {
    const session = intake.current;
    if (!session || operation.current) return;
    selection.current += 1;
    operation.current = true; setBusy(true); setLocalError("");
    try { await action(session); }
    catch (error) { if (intake.current === session) setLocalError(error instanceof Error ? error.message : "처리하지 못했어요."); }
    finally { if (intake.current === session) { operation.current = false; setBusy(false); } }
  }

  async function cancel() {
    const session = intake.current;
    if (!session || cancellation.current) return;
    cancellation.current = true;
    try { await session.cancel(); }
    catch { if (intake.current === session) setLocalError("취소 결과를 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요."); }
    finally { if (intake.current === session) cancellation.current = false; }
  }

  const recording = captureState !== "idle";
  const canChoose = available && !recording && !busy && (view.stage === "idle" || view.stage === "selected");
  const terminal = ["ready", "rejected", "cancelled"].includes(view.stage);
  return (
    <div className="flex flex-col gap-5">
      <p>직접 녹음은 최대 30초, 파일은 최대 60초까지.<br />시작을 누를 때만 마이크가 켜져요.</p>
      {!available && <Callout prefixIcon={<Mic />} title="로그인 후 사용할 수 있어요" description="현재 화면에서는 녹음과 전송이 시작되지 않아요. 실제 계정과 서버 연결이 필요해요." />}
      <div className="capture-panel" role="status" aria-live="polite">
        <span className="capture-icon"><Mic size={30} aria-hidden="true" /></span>
        <strong>{captureState === "requesting" ? "마이크 권한 확인 중" : captureState === "recording" ? "직접 녹음 중" : "녹음 대기"}</strong>
        <p className="text-sm text-muted-foreground">{captureState === "requesting" ? "브라우저의 마이크 권한 요청을 확인해 주세요." : captureState === "recording" ? "최대 30초 뒤 자동으로 녹음이 끝나요." : "입력한 소리만 전송해요. 자동 감지는 아직 시작되지 않았어요."}</p>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row">
        {!recording ? <ActionButton disabled={!canChoose} onClick={() => void startRecording()} className="flex-1"><Mic size={20} aria-hidden="true" />{localError ? "다시 녹음하기" : "직접 녹음 시작"}</ActionButton> :
          <ActionButton onClick={() => recorder.current?.stop()} className="flex-1"><Square size={18} aria-hidden="true" />{captureState === "requesting" ? "권한 요청 취소" : "녹음 끝내기"}</ActionButton>}
        <ActionButton variant="neutralWeak" disabled={!canChoose} onClick={() => fileInput.current?.click()} className="flex-1"><FileAudio size={20} aria-hidden="true" />파일 선택</ActionButton>
        <input ref={fileInput} aria-label="기존 음원 파일" type="file" accept="audio/*" disabled={!canChoose} className="sr-only" tabIndex={-1} onChange={(event) => { void selectFile(event.target.files?.[0]); event.target.value = ""; }} />
      </div>
      <details><summary className="min-h-11 cursor-pointer py-2 text-sm text-muted-foreground">연결이 불안정한가요?</summary><label className="flex min-h-11 items-center gap-3 text-sm"><input type="checkbox" checked={preferResumable} disabled={!canChoose || view.stage !== "idle"} onChange={(event) => setPreferResumable(event.target.checked)} />끊긴 지점에서 재개할 수 있는 전송 사용</label></details>
      {localError && <ErrorState label={localError} />}
      {view.stage !== "idle" && <div role="status" aria-live="polite" className="status-note">
        <p>{view.message}</p>
        {view.source === "FILE" && <p className="mt-2 text-sm text-muted-foreground">녹음 시각을 모르는 파일은 아기의 현재 상태와 연결해 해석하기 어려워요.</p>}
        {view.stage === "transferring" && <progress aria-label="업로드 진행률" className="mt-3 w-full" max={100} value={view.progress} />}
      </div>}
      <div className="flex flex-wrap gap-2">
        {view.stage === "selected" && <ActionButton loading={busy} disabled={busy} onClick={() => void run((s) => s.start())}><Upload size={19} aria-hidden="true" />업로드 시작</ActionButton>}
        {view.stage === "interrupted" && <><ActionButton disabled={busy} onClick={() => void run((s) => s.start())}>같은 승인으로 전송 재개</ActionButton><ActionButton variant="neutralWeak" disabled={busy} onClick={() => void run((s) => s.complete())}>전송 결과 확인</ActionButton></>}
        {view.stage === "expired" && <ActionButton disabled={busy} onClick={() => void run((s) => s.renew())}>같은 음원 재승인</ActionButton>}
        {view.stage === "uncertain" && <><ActionButton disabled={busy} onClick={() => void run((s) => s.refresh())}>서버 최종 상태 조회</ActionButton><ActionButton variant="neutralWeak" disabled={busy} onClick={() => void run((s) => s.retry())}>같은 요청 재시도</ActionButton></>}
        {view.grant && !terminal && <ActionButton variant="ghost" disabled={view.stage === "cancelling"} onClick={() => void cancel()}>전송 중단·취소</ActionButton>}
        {terminal && <ActionButton variant="neutralWeak" onClick={() => { setView(initialView); setLocalError(""); setRevision((value) => value + 1); }}>새 음원 시작</ActionButton>}
      </div>
      {view.stage === "ready" && <div className="flex flex-col gap-3"><Callout title="음원 준비 완료" description="분석 연결을 준비하고 있어요. 아직 모델 분석 결과는 없어요." /><ActionButton variant="neutralWeak" disabled={busy} onClick={() => void run(async (s) => { if (player.current) await s.play(player.current); })}>보관 동의 확인 후 재생</ActionButton><audio ref={player} controls controlsList="nodownload" aria-label="등록한 음원 재생" onEnded={() => intake.current?.clearPlayback()} /></div>}
    </div>
  );
}

