"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { usePrivateScope } from "@/components/app-providers";
import { ScreenSection } from "@/components/screen-state";
import { AudioMeasurementSession, type AudioMeasurementState } from "@/lib/audio/audio-measurement";

function numberOrUnknown(value: number | null, unit: string): string {
  return value === null ? "확인할 수 없음" : `${value.toLocaleString()} ${unit}`;
}

export function AudioMeasureScreen() {
  const scope = usePrivateScope();
  const [state, setState] = useState<AudioMeasurementState>({
    status: "idle", reason: null, worklet: null, recorder: null,
  });
  const sessionRef = useRef<AudioMeasurementSession | null>(null);
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope]);
  const getGeneration = useCallback(() => scope.snapshot().generation, [scope]);
  const scopeGeneration = useSyncExternalStore(subscribe, getGeneration, getGeneration);

  useEffect(() => {
    // An effect setup owns one session. Strict Mode may clean up and set up again
    // without recreating component state, so a render-owned session would stay disposed.
    const session = new AudioMeasurementSession(setState);
    sessionRef.current = session;
    const stopForVisibility = () => {
      if (document.visibilityState === "hidden") session.abort("화면이 숨겨져 측정을 중단했어요. 복귀 후 직접 다시 시작해 주세요.");
    };
    const stopForPageExit = () => session.abort("화면 이탈로 측정을 중단했어요.");
    document.addEventListener("visibilitychange", stopForVisibility);
    window.addEventListener("pagehide", stopForPageExit);
    return () => {
      document.removeEventListener("visibilitychange", stopForVisibility);
      window.removeEventListener("pagehide", stopForPageExit);
      sessionRef.current = null;
      session.dispose();
    };
  }, []);

  useEffect(() => {
    const session = sessionRef.current;
    if (!session) return;
    return scope.registerCleanup(() => session.clearScope());
  }, [scope, scopeGeneration]);

  const running = state.status === "requesting" || state.status === "listening";
  return (
    <div className="flex flex-col gap-4">
      <ScreenSection title="A-02 개발용 오디오 측정">
        <p className="text-sm text-foreground">실제 울음 감지나 업로드를 하지 않는 시험 화면입니다.</p>
        <p className="text-xs text-muted-foreground">
          시작을 누른 뒤에만 마이크 권한을 요청합니다. MediaRecorder는 시작 시 짧은 약 3초 클립 한 개만 만들고,
          AudioWorklet은 중지할 때까지 샘플 수를 셉니다. 원음과 Blob은 저장하거나 전송하지 않습니다.
        </p>
        <div className="flex gap-2">
          <button type="button" onClick={() => void sessionRef.current?.start()} disabled={running}
            className="min-h-11 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50">
            측정 시작
          </button>
          <button type="button" onClick={() => sessionRef.current?.stop()} disabled={!running}
            className="min-h-11 rounded-md border border-border px-4 text-sm font-medium disabled:opacity-50">
            측정 중지
          </button>
        </div>
        <p role="status" className="text-sm text-foreground">상태: {state.status} {state.reason}</p>
      </ScreenSection>

      <ScreenSection title="AudioWorklet 관측값">
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt>처리한 샘플 수</dt><dd>{numberOrUnknown(state.worklet?.samples ?? null, "samples")}</dd>
          <dt>실제 AudioContext 샘플레이트</dt><dd>{numberOrUnknown(state.worklet?.sampleRate ?? null, "Hz")}</dd>
          <dt>첫 샘플의 오디오 시계</dt><dd>{numberOrUnknown(state.worklet?.firstAudioSecond ?? null, "초")}</dd>
          <dt>마지막 샘플 직후 오디오 시계</dt><dd>{numberOrUnknown(state.worklet?.lastAudioSecond ?? null, "초")}</dd>
        </dl>
        <p className="text-xs text-muted-foreground">
          시각은 기기의 벽시계가 아니라 AudioContext 시작 이후의 오디오 시계입니다. 값은 Worklet이 실제 처리해 보고한 프레임만 반영합니다.
        </p>
      </ScreenSection>

      <ScreenSection title="MediaRecorder 결과물 관측값">
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <dt>크기</dt><dd>{numberOrUnknown(state.recorder?.bytes ?? null, "bytes")}</dd>
          <dt>Blob이 보고한 형식</dt><dd>{state.recorder?.mimeType ?? "확인할 수 없음"}</dd>
          <dt>브라우저가 읽은 녹음 길이</dt><dd>{numberOrUnknown(state.recorder?.durationSeconds ?? null, "초")}</dd>
        </dl>
        <p className="text-xs text-muted-foreground">
          길이는 Blob의 미디어 메타데이터에서 확인된 경우만 표시합니다. 브라우저가 읽지 못하면 확인할 수 없음으로 남습니다.
          Worklet 샘플 수를 녹음 길이로 환산하지 않습니다.
        </p>
      </ScreenSection>

      <ScreenSection title="중단과 기기 시험 안내">
        <p className="text-xs text-muted-foreground">
          숨김, 화면 이탈, 트랙 종료, 계정·아기 범위 변경 시 중단합니다. 복귀해도 자동 재개하지 않습니다.
          잠금·통화·연결 중단은 브라우저가 모두 직접 감지한다고 보장할 수 없으므로 아래 시험표에 따라 실제 기기에서 트랙 종료와 권한 표시를 확인해야 합니다.
        </p>
      </ScreenSection>
    </div>
  );
}
