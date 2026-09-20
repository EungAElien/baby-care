import type { components } from "@/lib/api/generated";
import type { RealApiClient } from "@/lib/api/real-client";
import { idempotencyHeaders, newClientRequestId } from "@/lib/api/client";
import { ContractApiError, requireData } from "@/lib/api/errors";
import type { ApiAuthAdapter } from "@/lib/api/client";
import type { PublicConfig } from "@/lib/public-config";
import type { PrivateScope, PrivateScopeSnapshot } from "@/lib/private-scope";
import { clearAudioResume, StorageUpload } from "./storage-upload";
import {
  FILE_MAX_SECONDS,
  MANUAL_MAX_SECONDS,
  MAX_AUDIO_BYTES,
  TUS_CHUNK_BYTES,
  type FinishedRecording,
} from "./recording";

type Episode = components["schemas"]["Episode"];
type Asset = components["schemas"]["AudioAsset"];
type Grant = components["schemas"]["UploadGrant"];
type Source = "MANUAL" | "FILE";
type Stage =
  | "idle"
  | "selected"
  | "creating"
  | "granting"
  | "transferring"
  | "interrupted"
  | "completing"
  | "uncertain"
  | "ready"
  | "rejected"
  | "expired"
  | "cancelling"
  | "cancelled"
  | "error";

export type IntakeView = Readonly<{
  stage: Stage;
  message: string;
  progress: number;
  episode: Episode | null;
  audio: Asset | null;
  grant: Grant | null;
  source: Source | null;
}>;

const reasonText: Record<string, string> = {
  TOO_SHORT: "1초보다 짧아요. 다시 녹음해 주세요.",
  SILENCE: "소리가 거의 없어요. 마이크나 파일을 확인하고 다시 시도해 주세요.",
  CLIPPING: "소리가 잘렸을 수 있어요. 음량을 낮춰 다시 녹음할 수 있습니다.",
  TOO_LONG: "허용 길이를 넘었어요. 짧은 파일을 선택하거나 다시 녹음해 주세요.",
  TOO_LARGE: "25,000,000바이트를 넘었어요. 더 작은 파일을 선택해 주세요.",
  UNSUPPORTED_CODEC:
    "실제 음원 형식을 지원하지 않아요. 지원되는 원본 파일을 선택해 주세요.",
  DECODE_ERROR:
    "음원을 읽을 수 없어요. 손상되지 않은 다른 파일을 선택해 주세요.",
};

export function audioStatusMessage(audio: Asset): string {
  if (audio.status === "READY") {
    const warning = audio.quality_reasons
      .filter((reason) => reason === "CLIPPING")
      .map((reason) => reasonText[reason])
      .join(" ");
    return `업로드·검증 완료. 분석은 아직 시작되지 않았어요.${warning ? ` 경고: ${warning}` : ""}`;
  }
  if (audio.status === "REJECTED") {
    const reasons = audio.quality_reasons.map(
      (reason) => reasonText[reason] ?? "서버가 음원을 거부했어요.",
    );
    return `음원 검증 실패. ${reasons.join(" ") || reasonText[audio.rejection_code ?? ""] || "다른 원본으로 다시 시도해 주세요."}`;
  }
  if (audio.status === "VERIFYING")
    return "서버가 음원을 검증 중이에요. 같은 사건의 상태를 다시 확인해 주세요.";
  return "음원 처리가 끝나지 않았어요. 같은 사건의 상태를 확인해 주세요.";
}

function errorText(error: unknown): string {
  if (error instanceof ContractApiError) {
    if (
      error.kind === "permission" ||
      error.kind === "not-found" ||
      error.kind === "authentication"
    )
      return "현재 아기 접근 권한을 확인할 수 없어요. 다시 로그인하거나 권한을 확인해 주세요.";
    if (error.status === 503 || error.status === 500)
      return "서버에 일시 장애가 있어요. 같은 요청으로 다시 확인해 주세요.";
    if (error.status === 429)
      return `요청이 제한됐어요. ${error.retryAfterSeconds ?? "잠시"}초 뒤 다시 시도해 주세요.`;
    if ([413, 415, 422].includes(error.status))
      return "서버가 원본 음원을 거부했어요. 같은 사건의 검증 상태를 확인해 주세요.";
  }
  return error instanceof Error && !/https?:\/\//.test(error.message)
    ? error.message
    : "연결이 끊겼어요. 같은 요청의 결과를 확인해 주세요.";
}

/** One tab-local logical intake. Mutation IDs survive retries while this instance lives. */
export class AudioIntake {
  private view: IntakeView = {
    stage: "idle",
    message: "녹음하거나 파일을 선택해 주세요.",
    progress: 0,
    episode: null,
    audio: null,
    grant: null,
    source: null,
  };
  private listener: (view: IntakeView) => void;
  private blob: Blob | null = null;
  private duration: number | null = null;
  private startedAt: string | null = null;
  private preferResumable = false;
  private transfer: StorageUpload | null = null;
  private ids = {
    episode: newClientRequestId(),
    grant: newClientRequestId(),
    complete: newClientRequestId(),
    renew: newClientRequestId(),
    cancel: newClientRequestId(),
  };
  private snapshot: PrivateScopeSnapshot;
  private disposed = false;
  private operationRevision = 0;
  private playbackElement: HTMLAudioElement | null = null;
  private playbackTimer: ReturnType<typeof setTimeout> | null = null;
  private playbackObjectUrl: string | null = null;
  private playbackController: AbortController | null = null;
  private lastStep: "episode" | "grant" | "transfer" | "complete" | "cancel" =
    "episode";

  constructor(
    private readonly babyId: string,
    private readonly client: RealApiClient,
    private readonly config: PublicConfig,
    private readonly auth: ApiAuthAdapter,
    private readonly scope: PrivateScope,
    listener: (view: IntakeView) => void,
  ) {
    this.snapshot = scope.snapshot();
    this.listener = listener;
    listener(this.view);
  }

  private set(patch: Partial<IntakeView>): void {
    if (this.disposed) return;
    this.view = { ...this.view, ...patch };
    this.listener(this.view);
  }

  private assertCurrent(): string {
    this.scope.assertCurrent(this.snapshot);
    if (!this.snapshot.userId || this.snapshot.babyId !== this.babyId)
      throw new Error("현재 아기 범위가 바뀌었어요.");
    return this.snapshot.userId;
  }

  select(
    source: Source,
    blob: Blob,
    duration: number | null,
    preferResumable: boolean,
  ): void {
    this.assertCurrent();
    if (this.view.episode)
      throw new Error("진행 중인 사건을 먼저 완료하거나 취소해 주세요.");
    if (!blob.type)
      throw new Error(
        "원본 파일의 MIME 형식을 확인할 수 없어요. 다른 파일을 선택해 주세요.",
      );
    if (blob.size === 0 || blob.size > MAX_AUDIO_BYTES)
      throw new Error("파일은 1~25,000,000바이트여야 해요.");
    const limit = source === "MANUAL" ? MANUAL_MAX_SECONDS : FILE_MAX_SECONDS;
    if (duration !== null && duration > limit)
      throw new Error(`${limit}초 이내 음원만 사용할 수 있어요.`);
    this.blob = blob;
    this.duration = duration;
    this.startedAt = null;
    this.preferResumable = preferResumable;
    this.set({
      stage: "selected",
      source,
      progress: 0,
      message: `원본 ${blob.size.toLocaleString()}바이트 · 실제 MIME ${blob.type}. ${duration === null ? "길이는 서버가 확인합니다." : `${duration.toFixed(1)}초.`}`,
    });
  }

  selectRecording(
    recording: FinishedRecording,
    preferResumable: boolean,
  ): void {
    this.select(
      "MANUAL",
      recording.blob,
      recording.durationSeconds,
      preferResumable,
    );
    this.startedAt = recording.startedAt;
  }

  async start(): Promise<void> {
    const revision = this.operationRevision;
    const userId = this.assertCurrent();
    const source = this.view.source;
    if (!this.blob || !source) return;
    try {
      let episode = this.view.episode;
      if (!episode) {
        this.lastStep = "episode";
        this.set({ stage: "creating", message: "사건을 만들고 있어요." });
        episode = requireData(
          await this.client.POST("/episodes", {
            params: { header: idempotencyHeaders(this.ids.episode) },
            body: {
              client_request_id: this.ids.episode,
              baby_id: this.babyId,
              source,
              timing_status: source === "FILE" ? "UNKNOWN" : "KNOWN",
              started_at: source === "FILE" ? null : this.startedAt,
              observation_session_id: null,
              data_origin: "USER",
            },
          }),
        );
        this.assertCurrent();
        if (revision !== this.operationRevision) return;
        this.set({
          episode,
          message:
            source === "FILE"
              ? "파일의 시각을 알 수 없어 현재 맥락 결합이 제한돼요. 음질 실패는 아닙니다."
              : "사건을 만들었어요.",
        });
      }
      let grant = this.view.grant;
      if (!grant) {
        this.lastStep = "grant";
        this.set({
          stage: "granting",
          message: "업로드 승인을 요청하고 있어요.",
        });
        const allocated = requireData(
          await this.client.POST("/episodes/{episode_id}/uploads", {
            params: {
              path: { episode_id: episode.episode_id },
              header: idempotencyHeaders(this.ids.grant),
            },
            body: {
              client_request_id: this.ids.grant,
              mime_type: this.blob.type,
              bytes: this.blob.size,
              duration_seconds: this.duration,
              checksum_sha256: null,
              prefer_resumable:
                this.preferResumable || this.blob.size > TUS_CHUNK_BYTES,
            },
          }),
        );
        this.assertCurrent();
        if (revision !== this.operationRevision) return;
        if (
          allocated.audio.baby_id !== this.babyId ||
          allocated.audio.episode_id !== episode.episode_id ||
          allocated.upload.audio_id !== allocated.audio.audio_id ||
          allocated.audio.bytes !== this.blob.size ||
          allocated.audio.mime_type !== this.blob.type ||
          (this.blob.size > TUS_CHUNK_BYTES &&
            allocated.upload.method !== "TUS")
        )
          throw new Error("서버 업로드 승인이 원본과 일치하지 않아요.");
        grant = allocated.upload;
        this.set({ audio: allocated.audio, grant });
      }
      if (Date.now() >= Date.parse(grant.expires_at)) {
        this.set({
          stage: "expired",
          message:
            "15분 업로드 승인이 만료됐어요. 같은 음원으로 재승인을 요청해 주세요.",
        });
        return;
      }
      this.set({
        stage: "transferring",
        message: `${grant.method} 방식으로 전송 중이에요.`,
      });
      this.lastStep = "transfer";
      const audio = this.view.audio;
      if (!audio) throw new Error("서버 음원 ID가 없어요.");
      this.transfer = new StorageUpload(
        grant,
        {
          userId,
          babyId: this.babyId,
          audioId: audio.audio_id,
          uploadId: grant.upload_id,
        },
        this.config,
        this.auth,
      );
      await this.transfer.send(this.blob, (uploaded, total) => {
        if (revision === this.operationRevision)
          this.set({ progress: Math.round((uploaded / total) * 100) });
      });
      this.assertCurrent();
      if (revision !== this.operationRevision) return;
      await this.complete();
    } catch (error) {
      if (
        this.disposed ||
        revision !== this.operationRevision ||
        this.view.stage === "cancelling" ||
        this.view.stage === "cancelled"
      )
        return;
      const expired =
        this.view.grant && Date.now() >= Date.parse(this.view.grant.expires_at);
      this.set({
        stage: expired
          ? "expired"
          : this.view.stage === "transferring"
            ? "interrupted"
            : "uncertain",
        message: errorText(error),
      });
    } finally {
      this.transfer = null;
    }
  }

  async complete(): Promise<void> {
    const revision = this.operationRevision;
    this.assertCurrent();
    const grant = this.view.grant;
    if (!grant) return;
    this.lastStep = "complete";
    this.set({
      stage: "completing",
      message: "서버가 실제 원본을 검증하고 있어요.",
    });
    try {
      requireData(
        await this.client.POST("/uploads/{upload_id}/complete", {
          params: {
            path: { upload_id: grant.upload_id },
            header: idempotencyHeaders(this.ids.complete),
          },
          body: { client_request_id: this.ids.complete, checksum_sha256: null },
        }),
      );
      if (revision === this.operationRevision) await this.refresh();
    } catch (error) {
      if (this.disposed || revision !== this.operationRevision) return;
      // B-05 marks a known transient verification failure as retryable with a NEW
      // key. A lost response keeps the original key until its outcome is found.
      if (error instanceof ContractApiError && error.status === 503)
        this.ids.complete = newClientRequestId();
      this.set({
        stage: "uncertain",
        message: `${errorText(error)} 같은 사건의 최종 상태를 조회해 주세요.`,
      });
    }
  }

  async retry(): Promise<void> {
    if (this.lastStep === "cancel") {
      await this.cancel();
      return;
    }
    if (this.lastStep !== "complete") {
      await this.start();
      return;
    }
    await this.refresh();
    if (this.view.audio?.status === "ALLOCATED") await this.complete();
  }

  async refresh(): Promise<void> {
    const revision = this.operationRevision;
    this.assertCurrent();
    const episode = this.view.episode;
    if (!episode) {
      await this.start();
      return;
    }
    try {
      const detail = requireData(
        await this.client.GET("/episodes/{episode_id}", {
          params: { path: { episode_id: episode.episode_id } },
        }),
      );
      this.assertCurrent();
      if (revision !== this.operationRevision) return;
      const audio =
        detail.audio_assets.find(
          (item) => item.audio_id === this.view.audio?.audio_id,
        ) ?? null;
      if (!audio) {
        this.set({
          stage: "uncertain",
          message:
            "서버 음원 상태가 아직 보이지 않아요. 같은 요청으로 다시 확인해 주세요.",
        });
        return;
      }
      this.set({
        audio,
        stage:
          audio.status === "READY"
            ? "ready"
            : audio.status === "REJECTED"
              ? "rejected"
              : "uncertain",
        message: audioStatusMessage(audio),
      });
      if (audio.status === "READY" || audio.status === "REJECTED")
        this.blob = null;
    } catch (error) {
      if (revision !== this.operationRevision) return;
      this.set({ stage: "uncertain", message: errorText(error) });
    }
  }

  async renew(): Promise<void> {
    this.assertCurrent();
    const audio = this.view.audio;
    const oldGrant = this.view.grant;
    if (
      !audio ||
      !oldGrant ||
      !this.blob ||
      audio.status !== "ALLOCATED" ||
      Date.now() < Date.parse(oldGrant.expires_at)
    )
      return;
    try {
      const renewed = requireData(
        await this.client.POST("/audio-assets/{audio_id}/uploads", {
          params: {
            path: { audio_id: audio.audio_id },
            header: idempotencyHeaders(this.ids.renew),
          },
          body: { client_request_id: this.ids.renew, version: audio.version },
        }),
      );
      this.assertCurrent();
      if (
        renewed.upload.object_key !== oldGrant.object_key ||
        renewed.upload.bucket !== oldGrant.bucket ||
        renewed.audio.audio_id !== audio.audio_id
      )
        throw new Error("재승인 객체 경로가 달라졌어요.");
      clearAudioResume({
        userId: this.snapshot.userId!,
        babyId: this.babyId,
        audioId: audio.audio_id,
        uploadId: oldGrant.upload_id,
      });
      this.set({
        audio: renewed.audio,
        grant: renewed.upload,
        stage: "selected",
        progress: 0,
        message: "같은 음원 경로로 재승인받았어요. 전송을 다시 시작해 주세요.",
      });
    } catch (error) {
      this.set({ stage: "expired", message: errorText(error) });
    }
  }

  async cancel(): Promise<void> {
    this.operationRevision += 1;
    this.transfer?.abort();
    this.assertCurrent();
    this.lastStep = "cancel";
    this.set({
      stage: "cancelling",
      message: "전송을 중단하고 서버 승인을 취소하고 있어요.",
    });
    const grant = this.view.grant;
    if (!grant) {
      this.dispose();
      return;
    }
    try {
      requireData(
        await this.client.POST("/uploads/{upload_id}/cancel", {
          params: {
            path: { upload_id: grant.upload_id },
            header: idempotencyHeaders(this.ids.cancel),
          },
          body: { client_request_id: this.ids.cancel },
        }),
      );
      this.blob = null;
      clearAudioResume({
        userId: this.snapshot.userId!,
        babyId: this.babyId,
        audioId: grant.audio_id,
        uploadId: grant.upload_id,
      });
      this.set({
        stage: "cancelled",
        message:
          "전송을 중단하고 서버 승인을 취소했어요. 다시 보내려면 새 음원으로 시작해 주세요.",
      });
    } catch (error) {
      this.set({
        stage: "uncertain",
        message: `전송은 중단됐지만 서버 취소 결과가 불명확해요. ${errorText(error)}`,
      });
    }
  }

  async play(element: HTMLAudioElement): Promise<void> {
    this.assertCurrent();
    if (this.view.audio?.status !== "READY") return;
    this.clearPlayback();
    const consents = requireData(
      await this.client.GET("/consents", {
        params: { query: { baby_id: this.babyId } },
      }),
    );
    this.assertCurrent();
    const retention = consents.items
      .filter(
        (item) =>
          item.baby_id === this.babyId && item.scope === "AUDIO_RETENTION",
      )
      .sort((left, right) => right.version - left.version)[0];
    if (retention?.status !== "GRANTED") {
      this.set({ message: "보관 동의가 없어 재생 링크를 발급하지 않았어요." });
      return;
    }
    const playback = requireData(
      await this.client.GET("/audio-assets/{audio_id}/playback", {
        params: { path: { audio_id: this.view.audio.audio_id } },
      }),
    );
    this.assertCurrent();
    const remaining = Date.parse(playback.expires_at) - Date.now();
    if (
      playback.audio_id !== this.view.audio.audio_id ||
      remaining <= 0 ||
      remaining > 60_000
    )
      throw new Error("재생 링크의 범위를 확인할 수 없어요.");
    const signed = new URL(playback.playback_url);
    if (
      signed.origin !== new URL(this.config.supabaseUrl).origin ||
      !signed.pathname.startsWith("/storage/v1/object/sign/baby-audio/") ||
      signed.username ||
      signed.password
    ) {
      throw new Error("재생 링크의 Storage 범위를 확인할 수 없어요.");
    }
    const controller = new AbortController();
    this.playbackController = controller;
    const response = await fetch(signed, {
      cache: "no-store",
      signal: controller.signal,
    });
    this.assertCurrent();
    if (!response.ok) throw new Error("보관 음원을 내려받지 못했어요.");
    const content = await response.blob();
    this.assertCurrent();
    if (controller.signal.aborted) return;
    const playbackRemaining = Date.parse(playback.expires_at) - Date.now();
    if (playbackRemaining <= 0)
      throw new Error("재생 링크가 만료됐어요. 다시 요청해 주세요.");
    this.playbackElement = element;
    this.playbackObjectUrl = URL.createObjectURL(content);
    element.src = this.playbackObjectUrl;
    element.onended = () => this.clearPlayback();
    this.playbackTimer = setTimeout(
      () => this.clearPlayback(),
      playbackRemaining,
    );
    await element.play();
  }

  clearPlayback(): void {
    this.playbackController?.abort();
    this.playbackController = null;
    if (this.playbackTimer) clearTimeout(this.playbackTimer);
    this.playbackTimer = null;
    if (this.playbackElement) {
      this.playbackElement.pause();
      this.playbackElement.removeAttribute("src");
      this.playbackElement.load();
      this.playbackElement.onended = null;
      this.playbackElement = null;
    }
    if (this.playbackObjectUrl) URL.revokeObjectURL(this.playbackObjectUrl);
    this.playbackObjectUrl = null;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.transfer?.abort();
    this.clearPlayback();
    clearAudioResume();
    this.blob = null;
    this.listener = () => {};
  }
}
