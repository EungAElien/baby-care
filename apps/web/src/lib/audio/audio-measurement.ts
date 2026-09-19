export type WorkletMeasurement = Readonly<{
  samples: number;
  sampleRate: number;
  firstAudioSecond: number;
  lastAudioSecond: number;
}>;

export type RecorderMeasurement = Readonly<{
  bytes: number;
  mimeType: string | null;
  durationSeconds: number | null;
}>;

export type AudioMeasurementState = Readonly<{
  status: "idle" | "requesting" | "listening" | "stopped" | "error";
  reason: string | null;
  worklet: WorkletMeasurement | null;
  recorder: RecorderMeasurement | null;
}>;

const initialState: AudioMeasurementState = {
  status: "idle",
  reason: null,
  worklet: null,
  recorder: null,
};

type ActiveCapture = {
  stream: MediaStream;
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  node: AudioWorkletNode;
  sink: GainNode;
  recorder: MediaRecorder;
  chunks: Blob[];
  timer: ReturnType<typeof setTimeout> | null;
  onEnded: () => void;
};

/** A-02 measurement only: no PCM cache, upload, persistence, or audio playback. */
export class AudioMeasurementSession {
  private state: AudioMeasurementState = initialState;
  private active: ActiveCapture | null = null;
  private generation = 0;
  private disposed = false;

  constructor(private readonly notify: (state: AudioMeasurementState) => void) {}

  snapshot(): AudioMeasurementState {
    return this.state;
  }

  private publish(change: Partial<AudioMeasurementState>): void {
    if (this.disposed) return;
    this.state = { ...this.state, ...change };
    this.notify(this.state);
  }

  async start(): Promise<void> {
    if (this.disposed || this.state.status === "requesting" || this.state.status === "listening") return;
    const generation = ++this.generation;
    this.publish({ ...initialState, status: "requesting" });
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia ||
      typeof AudioContext === "undefined" || typeof AudioWorkletNode === "undefined" ||
      typeof MediaRecorder === "undefined") {
      this.publish({ status: "error", reason: "보안 연결 또는 필요한 오디오 API를 사용할 수 없어요." });
      return;
    }

    let stream: MediaStream | null = null;
    let context: AudioContext | null = null;
    let source: MediaStreamAudioSourceNode | null = null;
    let node: AudioWorkletNode | null = null;
    let sink: GainNode | null = null;
    try {
      // This is the only permission request and is reachable only from the explicit start action.
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (generation !== this.generation || this.disposed || document.visibilityState !== "visible") {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      context = new AudioContext();
      if (!context.audioWorklet) throw new Error("AudioWorklet을 사용할 수 없어요.");
      await context.audioWorklet.addModule("/worklets/audio-measure.js");
      if (generation !== this.generation || this.disposed || document.visibilityState !== "visible") return;
      await context.resume();
      if (generation !== this.generation || this.disposed || document.visibilityState !== "visible") return;
      source = context.createMediaStreamSource(stream);
      node = new AudioWorkletNode(context, "audio-measure");
      sink = context.createGain();
      sink.gain.value = 0;
      source.connect(node);
      node.connect(sink);
      sink.connect(context.destination);
      const recorder = new MediaRecorder(stream);
      const capture: ActiveCapture = {
        stream, context, source, node, sink, recorder, chunks: [], timer: null,
        onEnded: () => this.abort("마이크 트랙이 종료됐어요. 다시 시작하려면 버튼을 눌러 주세요."),
      };
      this.active = capture;
      node.port.onmessage = (event: MessageEvent<unknown>) => {
        if (this.active !== capture) return;
        const value = event.data;
        if (!isWorkletMessage(value)) return;
        this.publish({ worklet: {
          samples: value.samples,
          sampleRate: value.sampleRate,
          firstAudioSecond: value.firstFrame / value.sampleRate,
          lastAudioSecond: value.lastFrameExclusive / value.sampleRate,
        } });
      };
      for (const track of stream.getTracks()) track.addEventListener("ended", capture.onEnded);
      recorder.ondataavailable = (event) => {
        if (this.active === capture && event.data.size > 0) capture.chunks.push(event.data);
      };
      recorder.onerror = () => this.abort("MediaRecorder 오류로 측정을 중단했어요.");
      recorder.start(1000);
      // Recorder measures one short clip; Worklet counting can continue for a 30-minute foreground test.
      capture.timer = setTimeout(() => this.finishRecorder(capture), 3000);
      this.publish({ status: "listening" });
    } catch (error) {
      if (this.active) this.abort("오디오 초기화에 실패했어요.");
      else if (generation === this.generation) {
        this.publish({ status: "error", reason: error instanceof DOMException && error.name === "NotAllowedError"
          ? "마이크 권한이 거부됐어요. 브라우저 설정을 확인한 뒤 직접 다시 시작해 주세요."
          : "오디오 API를 시작할 수 없어요. 이 브라우저와 장치를 확인해 주세요." });
      }
    } finally {
      if (!this.active || this.active.stream !== stream) {
        stream?.getTracks().forEach((track) => track.stop());
        source?.disconnect();
        node?.disconnect();
        node?.port.close();
        sink?.disconnect();
        if (context && context.state !== "closed") void context.close();
      }
    }
  }

  private finishRecorder(capture: ActiveCapture): void {
    if (this.active !== capture || capture.recorder.state === "inactive") return;
    if (capture.timer) clearTimeout(capture.timer);
    capture.timer = null;
    const expectedGeneration = this.generation;
    capture.recorder.onstop = () => {
      capture.recorder.ondataavailable = null;
      capture.recorder.onstop = null;
      if (expectedGeneration !== this.generation) {
        capture.chunks.length = 0;
        return;
      }
      // Use the emitted data type, not the requested/selected recorder MIME as a guess.
      const reportedType = capture.chunks.find((chunk) => chunk.type)?.type ?? "";
      const blob = new Blob(capture.chunks, { type: reportedType });
      capture.chunks.length = 0;
      void this.readRecorderMetadata(blob, expectedGeneration);
    };
    capture.recorder.stop();
  }

  private async readRecorderMetadata(blob: Blob, expectedGeneration: number): Promise<void> {
    const bytes = blob.size;
    const mimeType = blob.type || null;
    const durationSeconds = await readDuration(blob);
    if (expectedGeneration === this.generation) this.publish({ recorder: { bytes, mimeType, durationSeconds } });
  }

  stop(): void {
    const capture = this.active;
    if (!capture) {
      this.generation++;
      if (this.state.status === "requesting") this.publish({ status: "stopped", reason: "권한 요청 중 중지했어요." });
      return;
    }
    // Explicit stop preserves only numeric measurements. The recorder clip has already been bounded to 3 seconds.
    if (capture.recorder.state !== "inactive") {
      // Keep only the recorder's short final chunk until its stop event. All live microphone resources end now.
      capture.recorder.ondataavailable = (event) => {
        if (event.data.size > 0) capture.chunks.push(event.data);
      };
      this.finishRecorder(capture);
    }
    this.release(capture, false, true);
    this.publish({ status: "stopped", reason: "사용자가 중지했어요." });
  }

  abort(reason: string): void {
    this.generation++;
    const capture = this.active;
    if (capture) this.release(capture, true);
    this.publish({ status: "stopped", reason, recorder: null });
  }

  clearScope(): void {
    this.abort("계정 또는 아기 범위가 바뀌어 측정을 정리했어요.");
    this.publish({ worklet: null, recorder: null });
  }

  dispose(): void {
    this.abort("화면을 떠나 측정을 정리했어요.");
    this.disposed = true;
  }

  private release(capture: ActiveCapture, discard: boolean, preserveRecorder = false): void {
    if (this.active !== capture) return;
    this.active = null;
    if (capture.timer) clearTimeout(capture.timer);
    if (!preserveRecorder) {
      capture.recorder.ondataavailable = null;
      capture.recorder.onstop = null;
    }
    capture.recorder.onerror = null;
    if (!preserveRecorder && capture.recorder.state !== "inactive") capture.recorder.stop();
    if (!preserveRecorder) capture.chunks.length = 0;
    for (const track of capture.stream.getTracks()) {
      track.removeEventListener("ended", capture.onEnded);
      track.stop();
    }
    capture.node.port.onmessage = null;
    capture.node.port.close();
    capture.source.disconnect();
    capture.node.disconnect();
    capture.sink.disconnect();
    void capture.context.close();
    if (discard) this.publish({ recorder: null });
  }
}

function isWorkletMessage(value: unknown): value is {
  samples: number; sampleRate: number; firstFrame: number; lastFrameExclusive: number;
} {
  if (!value || typeof value !== "object") return false;
  const data = value as Record<string, unknown>;
  return [data.samples, data.sampleRate, data.firstFrame, data.lastFrameExclusive]
    .every((item) => typeof item === "number" && Number.isFinite(item)) && Number(data.sampleRate) > 0;
}

function readDuration(blob: Blob): Promise<number | null> {
  if (blob.size === 0) return Promise.resolve(null);
  return new Promise((resolve) => {
    const url = URL.createObjectURL(blob);
    const audio = document.createElement("audio");
    const timeout = setTimeout(() => finish(null), 4000);
    let finished = false;
    function finish(value: number | null) {
      if (finished) return;
      finished = true;
      clearTimeout(timeout);
      audio.removeAttribute("src");
      audio.load();
      URL.revokeObjectURL(url);
      resolve(value);
    }
    audio.onloadedmetadata = () => finish(Number.isFinite(audio.duration) && audio.duration >= 0 ? audio.duration : null);
    audio.onerror = () => finish(null);
    audio.preload = "metadata";
    audio.src = url;
  });
}
