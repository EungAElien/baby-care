import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { components } from "../src/lib/api/generated";
import { AudioIntake, audioStatusMessage } from "../src/lib/audio/intake";
import { DirectRecording, TUS_CHUNK_BYTES } from "../src/lib/audio/recording";
import { clearAudioResume, StorageUpload, validateUploadEndpoint } from "../src/lib/audio/storage-upload";
import { PrivateScope } from "../src/lib/private-scope";
import { ContractApiError } from "../src/lib/api/errors";
import type { RealApiClient } from "../src/lib/api/real-client";
import type { PublicConfig } from "../src/lib/public-config";

const config: PublicConfig = { apiBaseUrl: "https://api.example.test/v1", supabaseUrl: "https://project.supabase.co", supabasePublishableKey: "publishable-test" };
const identity = { userId: "user", babyId: "baby", audioId: "audio", uploadId: "upload" };
const auth = { getSession: vi.fn(async () => ({ userId: "user", accessToken: "current-token" })), refreshSession: vi.fn(async () => null) };
const future = () => new Date(Date.now() + 15 * 60_000).toISOString();
const grant = (method: "STANDARD" | "TUS", bytes = 25_000_000): components["schemas"]["UploadGrant"] => ({
  upload_id: "upload", audio_id: "audio", bucket: "baby-audio", object_key: "baby/audio/random",
  method, upload_endpoint: method === "STANDARD"
    ? "https://project.supabase.co/storage/v1/object/baby-audio/baby/audio/random"
    : "https://project.storage.supabase.co/storage/v1/upload/resumable",
  expires_at: future(), max_bytes: bytes,
});
const asset = (status: components["schemas"]["AudioAsset"]["status"] = "ALLOCATED"): components["schemas"]["AudioAsset"] => ({
  audio_id: "audio", baby_id: "baby", episode_id: "episode", created_by_user_id: "user", mime_type: "audio/webm", bytes: 1,
  duration_seconds: 2, checksum_sha256: null, status, retention_until: null, rejection_code: null, data_origin: "USER", version: 1,
  recorded_at: new Date().toISOString(), updated_at: new Date().toISOString(), quality_reasons: [],
});

afterEach(() => { vi.unstubAllGlobals(); clearAudioResume(); vi.restoreAllMocks(); });

describe("server-granted Storage boundary", () => {
  it("rejects wrong origins, credentials, query strings, paths and methods before a token is read", async () => {
    const altered = [
      "https://project.supabase.co.evil.test/storage/v1/object/baby-audio/baby/audio/random",
      "https://user@project.supabase.co/storage/v1/object/baby-audio/baby/audio/random",
      "https://project.supabase.co/storage/v1/object/baby-audio/baby/audio/other",
      "https://project.supabase.co/storage/v1/object/baby-audio/baby/audio/random?token=bad",
    ];
    for (const upload_endpoint of altered) {
      const invalid = { ...grant("STANDARD"), upload_endpoint };
      expect(() => validateUploadEndpoint(invalid, config)).toThrow();
      await expect(new StorageUpload(invalid, identity, config, auth).send(new Blob(["x"], { type: "audio/webm" }), () => {})).rejects.toThrow();
    }
    expect(() => validateUploadEndpoint({ ...grant("TUS"), upload_endpoint: "https://project.storage.supabase.co/storage/v1/upload/resumable/forged" }, config)).toThrow();
    expect(auth.getSession).not.toHaveBeenCalled();
  });

  it("uses 6 MiB chunks except the last, current token and no sensitive TUS metadata", async () => {
    const requests: Array<{ method: string; headers: Headers; size: number }> = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      const headers = new Headers(init.headers);
      const size = init.body instanceof Blob ? init.body.size : 0;
      requests.push({ method: init.method ?? "", headers, size });
      if (init.method === "POST") return new Response(null, { status: 201, headers: { Location: "/storage/v1/upload/resumable/session" } });
      const previous = requests.filter((request) => request.method === "PATCH").slice(0, -1).reduce((sum, request) => sum + request.size, 0);
      return new Response(null, { status: 204, headers: { "Upload-Offset": String(previous + size) } });
    }));
    const blob = new Blob([new Uint8Array(TUS_CHUNK_BYTES), new Uint8Array(5)], { type: "audio/webm" });
    const progress: number[] = [];
    await new StorageUpload(grant("TUS"), identity, config, auth).send(blob, (uploaded) => progress.push(uploaded));
    expect(requests.filter((request) => request.method === "PATCH").map((request) => request.size)).toEqual([TUS_CHUNK_BYTES, 5]);
    expect(requests.every((request) => request.headers.get("Authorization") === "Bearer current-token" && request.headers.get("x-upsert") === "false")).toBe(true);
    expect(requests[0]?.headers.get("Upload-Metadata")).not.toMatch(/fileName|device|original/i);
    expect(progress.at(-1)).toBe(blob.size);
  });

  it("keeps the resumable URL only for the same tab identity after an interrupted PATCH", async () => {
    let patches = 0;
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      if (init.method === "POST") return new Response(null, { status: 201, headers: { Location: "/storage/v1/upload/resumable/session" } });
      if (init.method === "HEAD") return new Response(null, { status: 200, headers: { "Upload-Offset": "0" } });
      patches += 1;
      if (patches === 1) throw new Error("offline");
      return new Response(null, { status: 204, headers: { "Upload-Offset": "1" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const blob = new Blob(["x"], { type: "audio/webm" });
    await expect(new StorageUpload(grant("TUS"), identity, config, auth).send(blob, () => {})).rejects.toThrow();
    await new StorageUpload(grant("TUS"), identity, config, auth).send(blob, () => {});
    expect(fetchMock.mock.calls.map((call) => call[1].method)).toEqual(["POST", "PATCH", "HEAD", "PATCH"]);
  });
});

describe("capture and intake", () => {
  it("includes the final dataavailable fragment emitted during normal stop and uses actual MIME and size", async () => {
    class Recorder {
      static isTypeSupported = () => true;
      mimeType = "audio/webm;codecs=opus";
      state = "inactive";
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      onerror: (() => void) | null = null;
      start() { this.state = "recording"; this.ondataavailable?.({ data: new Blob(["first"]) }); }
      stop() { this.ondataavailable?.({ data: new Blob(["last"]) }); this.state = "inactive"; this.onstop?.(); }
    }
    vi.stubGlobal("MediaRecorder", Recorder);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop: vi.fn() }] }) } });
    const capture = new DirectRecording();
    const finished = capture.start();
    await Promise.resolve();
    capture.stop();
    const result = await finished;
    expect(await result.blob.text()).toBe("firstlast");
    expect(result.mimeType).toBe("audio/webm;codecs=opus");
    expect(result.blob.type).toBe("audio/webm;codecs=opus");
    expect(result.blob.size).toBe(9);
  });

  it("sends the completed Blob MIME/bytes and requests TUS only above 6 MiB", async () => {
    for (const size of [TUS_CHUNK_BYTES, TUS_CHUNK_BYTES + 1]) {
      const scope = new PrivateScope(new QueryClient());
      scope.set("user", "baby");
      const body: Array<Record<string, unknown>> = [];
      const api = {
        POST: vi.fn(async (path: string, options: { body: Record<string, unknown> }) => {
          body.push(options.body);
          if (path === "/episodes") return { data: { episode_id: "episode" }, response: { ok: true } };
          if (path === "/episodes/{episode_id}/uploads") throw new Error("stop after grant request");
          return { data: {}, response: { ok: true } };
        }),
      } as unknown as RealApiClient;
      const session = new AudioIntake("baby", api, config, auth, scope, () => {});
      session.select("FILE", new Blob([new Uint8Array(size)], { type: "audio/mp4" }), null, false);
      await session.start();
      expect(body[1]).toMatchObject({ mime_type: "audio/mp4", bytes: size, prefer_resumable: size > TUS_CHUNK_BYTES });
      session.dispose();
    }
  });

  it("keeps CLIPPING as a READY warning and explains server rejection reasons", () => {
    const base = { status: "READY", quality_reasons: ["CLIPPING"], rejection_code: null } as components["schemas"]["AudioAsset"];
    expect(audioStatusMessage(base)).toContain("업로드·검증 완료");
    expect(audioStatusMessage(base)).toContain("경고");
    expect(audioStatusMessage({ ...base, status: "REJECTED", quality_reasons: ["TOO_SHORT", "SILENCE"] })).toContain("1초보다 짧아요");
  });

  it("recovers a lost complete response using the same logical request and server final state", async () => {
    const scope = new PrivateScope(new QueryClient());
    scope.set("user", "baby");
    const audio = asset();
    const views: Array<{ stage: string; message: string }> = [];
    const completeIds: string[] = [];
    let completeCount = 0;
    const api = {
      POST: vi.fn(async (path: string, options: { body: Record<string, unknown> }) => {
        if (path === "/episodes") return { data: { episode_id: "episode" }, response: { ok: true } };
        if (path === "/episodes/{episode_id}/uploads") return { data: { audio, upload: grant("STANDARD") }, response: { ok: true } };
        if (path === "/uploads/{upload_id}/complete") {
          completeIds.push(String(options.body.client_request_id));
          completeCount++;
          if (completeCount === 1) throw new Error("response lost");
          return { data: { ...audio, status: "READY" }, response: { ok: true } };
        }
        throw new Error("unexpected mutation");
      }),
      GET: vi.fn(async () => ({ data: { audio_assets: [{ ...audio, status: completeCount > 1 ? "READY" : "ALLOCATED" }] }, response: { ok: true } })),
    } as unknown as RealApiClient;
    vi.spyOn(StorageUpload.prototype, "send").mockResolvedValue();
    const session = new AudioIntake("baby", api, config, auth, scope, (view) => views.push({ stage: view.stage, message: view.message }));
    session.select("MANUAL", new Blob(["x"], { type: "audio/webm" }), 2, false);
    await session.start();
    expect(views.at(-1)?.stage).toBe("uncertain");
    await session.retry();
    expect(completeIds).toHaveLength(2);
    expect(completeIds[0]).toBe(completeIds[1]);
    expect(views.at(-1)?.stage).toBe("ready");
    expect(views.at(-1)?.message).toContain("분석은 아직 시작되지 않았어요");
    session.dispose();
  });

  it("uses a fresh complete key only after a known transient 503", async () => {
    const scope = new PrivateScope(new QueryClient());
    scope.set("user", "baby");
    const audio = asset();
    const ids: string[] = [];
    let completeCount = 0;
    const api = {
      POST: vi.fn(async (path: string, options: { body: Record<string, unknown> }) => {
        if (path === "/episodes") return { data: { episode_id: "episode" }, response: { ok: true } };
        if (path === "/episodes/{episode_id}/uploads") return { data: { audio, upload: grant("STANDARD") }, response: { ok: true } };
        ids.push(String(options.body.client_request_id));
        completeCount++;
        if (completeCount === 1) throw new ContractApiError(503, {
          code: "SERVICE_UNAVAILABLE", message: "Temporary decoder failure", retryable: true, request_id: "request", field_errors: [], details: {},
        }, new Headers());
        return { data: { ...audio, status: "READY" }, response: { ok: true } };
      }),
      GET: vi.fn(async () => ({ data: { audio_assets: [{ ...audio, status: completeCount > 1 ? "READY" : "ALLOCATED" }] }, response: { ok: true } })),
    } as unknown as RealApiClient;
    vi.spyOn(StorageUpload.prototype, "send").mockResolvedValue();
    const session = new AudioIntake("baby", api, config, auth, scope, () => {});
    session.select("FILE", new Blob(["x"], { type: "audio/webm" }), 2, false);
    await session.start();
    await session.retry();
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
    session.dispose();
  });

  it("aborts transport before server cancellation, and uses same object key on expired renewal", async () => {
    const scope = new PrivateScope(new QueryClient());
    scope.set("user", "baby");
    const audio = asset();
    const events: string[] = [];
    let rejectTransfer: ((error: Error) => void) | null = null;
    const send = vi.spyOn(StorageUpload.prototype, "send").mockImplementation(() => new Promise<void>((_resolve, reject) => { rejectTransfer = reject; }));
    vi.spyOn(StorageUpload.prototype, "abort").mockImplementation(() => { events.push("abort"); rejectTransfer?.(new Error("aborted")); });
    const expiredGrant = { ...grant("STANDARD"), expires_at: new Date(Date.now() - 1).toISOString() };
    const api = {
      POST: vi.fn(async (path: string) => {
        if (path === "/episodes") return { data: { episode_id: "episode" }, response: { ok: true } };
        if (path === "/episodes/{episode_id}/uploads") return { data: { audio, upload: grant("STANDARD") }, response: { ok: true } };
        if (path === "/uploads/{upload_id}/cancel") { events.push("cancel"); return { data: audio, response: { ok: true } }; }
        if (path === "/audio-assets/{audio_id}/uploads") return { data: { audio, upload: { ...grant("STANDARD"), upload_id: "new-upload" } }, response: { ok: true } };
        throw new Error("unexpected mutation");
      }),
    } as unknown as RealApiClient;
    const stages: string[] = [];
    const session = new AudioIntake("baby", api, config, auth, scope, (view) => stages.push(view.stage));
    session.select("FILE", new Blob(["x"], { type: "audio/webm" }), 2, false);
    const running = session.start();
    await vi.waitFor(() => expect(send).toHaveBeenCalled());
    await session.cancel();
    await running;
    expect(events.slice(0, 2)).toEqual(["abort", "cancel"]);
    expect(stages.at(-1)).toBe("cancelled");
    session.dispose();

    // A separate expired logical intake may renew only the same object key.
    const another = new AudioIntake("baby", api, config, auth, scope, (view) => stages.push(view.stage));
    another.select("FILE", new Blob(["x"], { type: "audio/webm" }), 2, false);
    (api.POST as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => ({ data: { episode_id: "episode" }, response: { ok: true } }))
      .mockImplementationOnce(async () => ({ data: { audio, upload: expiredGrant }, response: { ok: true } }));
    await another.start();
    expect(stages.at(-1)).toBe("expired");
    await another.renew();
    expect(stages.at(-1)).toBe("selected");
    another.dispose();
  });

  it("cleans an in-flight transfer when the baby or account scope changes", async () => {
    for (const [userId, babyId] of [["user", "other-baby"], ["other-user", null]] as const) {
      const scope = new PrivateScope(new QueryClient());
      scope.set("user", "baby");
      const audio = asset();
      const api = { POST: vi.fn(async (path: string) => path === "/episodes"
        ? { data: { episode_id: "episode" }, response: { ok: true } }
        : { data: { audio, upload: grant("TUS") }, response: { ok: true } }) } as unknown as RealApiClient;
      let started: (() => void) | null = null;
      vi.spyOn(StorageUpload.prototype, "send").mockImplementation(() => new Promise<void>((resolve) => { started = resolve; }));
      const abort = vi.spyOn(StorageUpload.prototype, "abort").mockImplementation(() => { started?.(); });
      const session = new AudioIntake("baby", api, config, auth, scope, () => {});
      scope.registerCleanup(() => session.dispose());
      session.select("FILE", new Blob(["x"], { type: "audio/webm" }), 2, true);
      const running = session.start();
      await vi.waitFor(() => expect(started).not.toBeNull());
      scope.set(userId, babyId);
      await running;
      expect(abort).toHaveBeenCalled();
      vi.restoreAllMocks();
    }
  });

  it("checks current retention and clears downloaded playback on scope change", async () => {
    const scope = new PrivateScope(new QueryClient());
    scope.set("user", "baby");
    const audio = asset("READY");
    const api = {
      POST: vi.fn(async (path: string) => path === "/episodes"
        ? { data: { episode_id: "episode" }, response: { ok: true } }
        : path === "/episodes/{episode_id}/uploads"
          ? { data: { audio: asset(), upload: grant("STANDARD") }, response: { ok: true } }
          : { data: audio, response: { ok: true } }),
      GET: vi.fn(async (path: string) => path === "/episodes/{episode_id}"
        ? { data: { audio_assets: [audio] }, response: { ok: true } }
        : path === "/consents"
          ? { data: { items: [{ baby_id: "baby", scope: "AUDIO_RETENTION", status: "GRANTED", version: 2 }] }, response: { ok: true } }
          : { data: { audio_id: "audio", playback_url: "https://project.supabase.co/storage/v1/object/sign/baby-audio/baby/audio/random?token=private", expires_at: new Date(Date.now() + 40_000).toISOString() }, response: { ok: true } }),
    } as unknown as RealApiClient;
    vi.spyOn(StorageUpload.prototype, "send").mockResolvedValue();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Blob(["audio"]), { status: 200 })));
    const create = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:private-playback");
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const element = {
      src: "", onended: null, play: vi.fn(async () => {}), pause: vi.fn(), removeAttribute: vi.fn(), load: vi.fn(),
    } as unknown as HTMLAudioElement;
    const session = new AudioIntake("baby", api, config, auth, scope, () => {});
    scope.registerCleanup(() => session.dispose());
    session.select("FILE", new Blob(["x"], { type: "audio/webm" }), 2, false);
    await session.start();
    await session.play(element);
    expect(create).toHaveBeenCalled();
    expect(element.src).toBe("blob:private-playback");
    expect(fetch).toHaveBeenCalledWith(expect.any(URL), expect.objectContaining({ cache: "no-store" }));
    scope.set("user", "other-baby");
    expect(element.pause).toHaveBeenCalled();
    expect(element.removeAttribute).toHaveBeenCalledWith("src");
    expect(revoke).toHaveBeenCalledWith("blob:private-playback");
  });
});
