"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePrivateScope } from "@/components/app-providers";
import { useApiClient } from "@/lib/api/real-client";
import { useCapabilitiesQuery } from "@/lib/api/capabilities";
import { supabaseAuthAdapter } from "@/lib/auth/api-auth-adapter";
import { useRealSession } from "@/lib/auth/real-session";
import { tryReadPublicConfig } from "@/lib/public-config";
import { AudioIntake, type IntakeView } from "@/lib/audio/intake";
import { DirectRecording, readDuration, supportedRecorderTypes } from "@/lib/audio/recording";

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
  const [recording, setRecording] = useState(false);
  const [preferResumable, setPreferResumable] = useState(false);
  const [localError, setLocalError] = useState("");
  const intake = useRef<AudioIntake | null>(null);
  const recorder = useRef<DirectRecording | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const available = real.status === "signed-in" && client !== null && config !== null &&
    scopeSnapshot.userId === real.userId && scopeSnapshot.babyId === babyId;
  // A-06 ①: the real analysis start action stays hidden until the server's
  // own product gate (capabilities.audio_model.available) is open.
  const capabilities = useCapabilitiesQuery(available && view.stage === "ready");

  useEffect(() => {
    if (!available || !client || !config) return;
    const session = new AudioIntake(babyId, client, config, supabaseAuthAdapter, scope, setView);
    intake.current = session;
    const cleanup = () => { recorder.current?.abort(); recorder.current = null; setRecording(false); session.dispose(); };
    const unregister = scope.registerCleanup(cleanup);
    return () => { unregister(); cleanup(); if (intake.current === session) intake.current = null; };
  }, [available, babyId, client, config, scope, scopeSnapshot.generation, revision]);

  async function startRecording() {
    setLocalError("");
    const session = intake.current;
    const capture = new DirectRecording();
    recorder.current = capture;
    setRecording(true);
    try {
      const result = await capture.start();
      if (recorder.current === capture && intake.current === session) session?.selectRecording(result, preferResumable);
    } catch (error) {
      if (recorder.current === capture) setLocalError(error instanceof Error ? error.message : "녹음에 실패했어요.");
    } finally {
      if (recorder.current === capture) { recorder.current = null; setRecording(false); }
    }
  }

  async function selectFile(file: File | undefined) {
    if (!file) return;
    setLocalError("");
    const session = intake.current;
    try {
      const duration = await readDuration(file);
      if (intake.current === session) session?.select("FILE", file, duration, preferResumable);
    } catch (error) {
      if (intake.current === session) setLocalError(error instanceof Error ? error.message : "파일을 선택하지 못했어요.");
    }
  }

  async function play() {
    if (!player.current) return;
    try { await intake.current?.play(player.current); }
    catch (error) { setLocalError(error instanceof Error ? error.message : "재생할 수 없어요."); }
  }

  if (!available) {
    return <p className="text-sm text-muted-foreground">직접 녹음과 파일 전송은 실제 계정으로 로그인하고 서버가 연결됐을 때 사용할 수 있어요. 개발용 측정값과 목 화면은 업로드 결과가 아닙니다.</p>;
  }

  const canChoose = !recording && (view.stage === "idle" || view.stage === "selected");
  const terminal = ["ready", "rejected", "cancelled"].includes(view.stage);
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">현재 아기에 직접 녹음(최대 30초) 또는 기존 파일(최대 60초)을 등록합니다. 마이크는 시작을 누를 때만 켜집니다.</p>
      <p className="text-xs text-muted-foreground">녹음 후보 지원: {supportedRecorderTypes().join(", ") || "확인되지 않음"}. 실제 형식은 녹음 완료 후 확인합니다.</p>
      <label className="flex min-h-11 items-center gap-2 text-sm">
        <input type="checkbox" checked={preferResumable} disabled={view.stage !== "idle" || recording} onChange={(event) => setPreferResumable(event.target.checked)} />
        연결이 불안정해 재개 전송 사용
      </label>
      <div className="flex flex-wrap gap-2">
        {!recording ? (
          <button type="button" disabled={!canChoose} onClick={startRecording} className="min-h-11 rounded-md border border-border px-4 text-sm disabled:opacity-50">직접 녹음 시작</button>
        ) : (
          <button type="button" onClick={() => recorder.current?.stop()} className="min-h-11 rounded-md border border-border px-4 text-sm">녹음 끝내기</button>
        )}
        <label className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm">
          기존 파일 선택
          <input aria-label="기존 음원 파일" type="file" accept="audio/*" disabled={!canChoose} className="sr-only" onChange={(event) => { void selectFile(event.target.files?.[0]); event.target.value = ""; }} />
        </label>
      </div>
      {localError && <p role="alert" className="text-sm text-destructive">{localError}</p>}
      <div role="status" aria-live="polite" className="rounded-md border border-border p-3 text-sm">
        <p>{view.message}</p>
        {view.source === "FILE" && <p className="mt-2 text-muted-foreground">파일의 녹음 시각을 모르면 현재 상태와 연결하는 해석이 제한됩니다. 이는 음질 거부가 아닙니다.</p>}
        {view.stage === "transferring" && <progress aria-label="업로드 진행률" className="mt-3 w-full" max={100} value={view.progress} />}
      </div>
      <div className="flex flex-wrap gap-2">
        {view.stage === "selected" && <button type="button" onClick={() => void intake.current?.start()} className="min-h-11 rounded-md bg-primary px-4 text-sm text-primary-foreground">업로드 시작</button>}
        {view.stage === "interrupted" && <>
          <button type="button" onClick={() => void intake.current?.start()} className="min-h-11 rounded-md border px-4 text-sm">같은 승인으로 전송 재개</button>
          <button type="button" onClick={() => void intake.current?.complete()} className="min-h-11 rounded-md border px-4 text-sm">전송 결과 확인</button>
        </>}
        {view.stage === "expired" && <button type="button" onClick={() => void intake.current?.renew()} className="min-h-11 rounded-md border px-4 text-sm">같은 음원 재승인</button>}
        {view.stage === "uncertain" && <>
          <button type="button" onClick={() => void intake.current?.refresh()} className="min-h-11 rounded-md border px-4 text-sm">서버 최종 상태 조회</button>
          <button type="button" onClick={() => void intake.current?.retry()} className="min-h-11 rounded-md border px-4 text-sm">같은 요청 재시도</button>
        </>}
        {view.grant && !terminal && <button type="button" onClick={() => void intake.current?.cancel()} className="min-h-11 rounded-md border border-destructive px-4 text-sm text-destructive">전송 중단·취소</button>}
        {terminal && <button type="button" onClick={() => { setView(initialView); setLocalError(""); setRevision((value) => value + 1); }} className="min-h-11 rounded-md border px-4 text-sm">새 음원 시작</button>}
      </div>
      {view.stage === "ready" && <div className="flex flex-col gap-2">
        {capabilities.isLoading && <p className="text-sm text-muted-foreground">분석 가능 여부를 확인하고 있어요.</p>}
        {capabilities.isError && <p className="text-sm text-destructive">분석 가능 여부를 확인하지 못했어요. 같은 음원으로 다시 확인해 주세요.</p>}
        {capabilities.data && view.episode && view.audio && (
          capabilities.data.audio_model.available ? (
            <Link
              href={`/babies/${babyId}/episodes/${view.episode.episode_id}/analysis?audioId=${view.audio.audio_id}`}
              className="flex min-h-11 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              분석 시작
            </Link>
          ) : (
            <button type="button" disabled className="min-h-11 rounded-md border px-4 text-sm opacity-50">
              분석 시작 · 서버가 아직 모델을 준비하지 못했어요 (MODEL_NOT_READY)
            </button>
          )
        )}
        <button type="button" onClick={() => void play()} className="min-h-11 rounded-md border px-4 text-sm">보관 동의 확인 후 재생</button>
        <audio ref={player} controls controlsList="nodownload" onEnded={() => intake.current?.clearPlayback()} />
      </div>}
    </div>
  );
}
