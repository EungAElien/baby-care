export const MANUAL_MAX_SECONDS = 30;
export const FILE_MAX_SECONDS = 60;
export const MAX_AUDIO_BYTES = 25_000_000;
export const TUS_CHUNK_BYTES = 6 * 1024 * 1024;

const recorderCandidates = ["audio/webm;codecs=opus", "audio/mp4;codecs=mp4a.40.2", "audio/mp4", "audio/webm"];

export function supportedRecorderTypes(): string[] {
  if (typeof MediaRecorder === "undefined") return [];
  return recorderCandidates.filter((candidate) => MediaRecorder.isTypeSupported(candidate));
}

export type FinishedRecording = Readonly<{ blob: Blob; mimeType: string; durationSeconds: number; startedAt: string }>;

/** Owns one user-started capture. The stop event fires after its final dataavailable event. */
export class DirectRecording {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pieces: Blob[] = [];
  private startedAt = 0;
  private startedAtWallClock = "";
  private finish: ((value: FinishedRecording) => void) | null = null;
  private fail: ((reason: Error) => void) | null = null;
  private cancelled = false;
  private stopRequested = false;
  private failureMessage = "녹음을 취소했어요.";
  private removeListeners: (() => void) | null = null;
  private cancelPermission: ((reason: Error) => void) | null = null;

  constructor(private readonly onState?: (state: "requesting" | "recording") => void) {}

  async start(): Promise<FinishedRecording> {
    if (this.recorder || typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("이 브라우저에서는 직접 녹음할 수 없어요.");
    }
    const supported = supportedRecorderTypes();
    if (supported.length === 0) throw new Error("지원되는 녹음 형식을 찾지 못했어요.");
    if (document.hidden) throw new Error("이 화면을 다시 연 뒤 녹음을 시작해 주세요.");
    if (navigator.onLine === false) throw new Error("연결을 확인한 뒤 녹음을 시작해 주세요.");
    this.onState?.("requesting");
    const hidden = () => { if (document.hidden) this.abort("화면이 숨겨져 녹음을 중단했어요. 화면을 연 뒤 다시 녹음해 주세요."); };
    const offline = () => this.abort("연결이 끊겨 녹음을 중단했어요. 연결을 확인한 뒤 다시 녹음해 주세요.");
    const pageHide = () => this.abort("화면을 떠나 녹음을 중단했어요. 다시 녹음하려면 직접 시작해 주세요.");
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("offline", offline);
    window.addEventListener("pagehide", pageHide);
    this.removeListeners = () => {
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("offline", offline);
      window.removeEventListener("pagehide", pageHide);
    };
    let stream: MediaStream;
    try {
      stream = await new Promise<MediaStream>((resolve, reject) => {
        this.cancelPermission = reject;
        void navigator.mediaDevices.getUserMedia({ audio: true }).then((result) => {
          if (this.cancelled || this.stopRequested) {
            result.getTracks().forEach((track) => track.stop());
            reject(new Error(this.failureMessage));
          } else resolve(result);
        }, reject);
      });
    } catch (error) {
      this.removeListeners?.();
      if (error instanceof DOMException && error.name === "NotAllowedError") throw new Error("마이크 권한이 거부됐어요. 브라우저의 사이트 권한에서 마이크를 허용한 뒤 다시 시작하거나 파일을 선택해 주세요.");
      throw error;
    } finally { this.cancelPermission = null; }
    if (this.cancelled || this.stopRequested) {
      stream.getTracks().forEach((track) => track.stop());
      throw new Error("녹음을 취소했어요.");
    }
    this.stream = stream;
    try {
      const recorder = new MediaRecorder(stream, { mimeType: supported[0] });
      this.recorder = recorder;
      this.pieces = [];
      recorder.ondataavailable = (event) => { if (event.data.size > 0) this.pieces.push(event.data); };
      recorder.onerror = () => this.abort("마이크 오류로 녹음을 중단했어요. 장치를 확인한 뒤 다시 녹음해 주세요.");
      for (const track of stream.getTracks()) {
        track.onended = () => this.abort("마이크 연결이 끝났어요. 장치를 연결한 뒤 다시 녹음해 주세요.");
        track.onmute = () => this.abort("마이크 입력이 중단됐어요. 장치를 확인한 뒤 다시 녹음해 주세요.");
      }
      recorder.onstop = () => {
        this.clearTimer();
        this.removeListeners?.();
        this.stopTracks();
        if (!this.cancelled) {
          // Use the recorder's actual MIME. Never label bytes as a different codec.
          const mimeType = recorder.mimeType;
          const blob = new Blob(this.pieces, { type: mimeType });
          this.finish?.({ blob, mimeType, durationSeconds: (performance.now() - this.startedAt) / 1000, startedAt: this.startedAtWallClock });
        } else {
          this.fail?.(new Error(this.failureMessage));
        }
        this.finish = null;
        this.fail = null;
        this.recorder = null;
        this.pieces = [];
      };
      const completion = new Promise<FinishedRecording>((resolve, reject) => {
        this.finish = resolve;
        this.fail = reject;
      });
      this.startedAt = performance.now();
      this.startedAtWallClock = new Date().toISOString();
      recorder.start();
      this.onState?.("recording");
      this.timer = setTimeout(() => this.stop(), MANUAL_MAX_SECONDS * 1000);
      return completion;
    } catch (error) {
      this.stopTracks();
      this.removeListeners?.();
      this.recorder = null;
      throw error;
    }
  }

  stop(): void {
    if (this.recorder?.state === "recording") this.recorder.stop();
    else { this.stopRequested = true; this.abort(); }
  }

  abort(message = "녹음을 취소했어요."): void {
    this.cancelled = true;
    this.failureMessage = message;
    this.cancelPermission?.(new Error(message));
    this.clearTimer();
    this.removeListeners?.();
    if (this.recorder?.state === "recording") this.recorder.stop();
    else this.stopTracks();
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => { track.onended = null; track.onmute = null; track.stop(); });
    this.stream = null;
  }
}

export async function readDuration(blob: Blob): Promise<number | null> {
  const url = URL.createObjectURL(blob);
  const audio = document.createElement("audio");
  audio.preload = "metadata";
  try {
    return await new Promise<number | null>((resolve) => {
      const timer = setTimeout(() => resolve(null), 5000);
      audio.onloadedmetadata = () => { clearTimeout(timer); resolve(Number.isFinite(audio.duration) ? audio.duration : null); };
      audio.onerror = () => { clearTimeout(timer); resolve(null); };
      audio.src = url;
    });
  } finally {
    audio.removeAttribute("src");
    audio.load();
    URL.revokeObjectURL(url);
  }
}
