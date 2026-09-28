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

  async start(): Promise<FinishedRecording> {
    if (this.recorder || typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("이 브라우저에서는 직접 녹음할 수 없어요.");
    }
    const supported = supportedRecorderTypes();
    if (supported.length === 0) throw new Error("지원되는 녹음 형식을 찾지 못했어요.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
      recorder.onerror = () => this.abort();
      recorder.onstop = () => {
        this.clearTimer();
        this.stopTracks();
        if (!this.cancelled) {
          // Use the recorder's actual MIME. Never label bytes as a different codec.
          const mimeType = recorder.mimeType;
          const blob = new Blob(this.pieces, { type: mimeType });
          this.finish?.({ blob, mimeType, durationSeconds: (performance.now() - this.startedAt) / 1000, startedAt: this.startedAtWallClock });
        } else {
          this.fail?.(new Error("녹음을 취소했어요."));
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
      this.timer = setTimeout(() => this.stop(), MANUAL_MAX_SECONDS * 1000);
      return completion;
    } catch (error) {
      this.stopTracks();
      this.recorder = null;
      throw error;
    }
  }

  stop(): void {
    if (this.recorder?.state === "recording") this.recorder.stop();
    else this.stopRequested = true;
  }

  abort(): void {
    this.cancelled = true;
    this.clearTimer();
    if (this.recorder?.state === "recording") this.recorder.stop();
    else this.stopTracks();
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
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
