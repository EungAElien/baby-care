import type { components } from "@/lib/api/generated";
import type { ApiAuthAdapter } from "@/lib/api/client";
import type { PublicConfig } from "@/lib/public-config";
import { TUS_CHUNK_BYTES } from "./recording";

type Grant = components["schemas"]["UploadGrant"];
type Identity = Readonly<{ userId: string; babyId: string; audioId: string; uploadId: string }>;
type Progress = (uploaded: number, total: number) => void;
const resumeUrls = new Map<string, string>();

function key(identity: Identity): string {
  return [identity.userId, identity.babyId, identity.audioId, identity.uploadId].join(":");
}

export function clearAudioResume(identity?: Identity): void {
  if (identity) resumeUrls.delete(key(identity));
  else resumeUrls.clear();
}

function storageOrigin(config: PublicConfig): string {
  if (config.supabaseStorageUrl) return new URL(config.supabaseStorageUrl).origin;
  const url = new URL(config.supabaseUrl);
  if (url.hostname.endsWith(".supabase.co") && !url.hostname.endsWith(".storage.supabase.co")) {
    url.hostname = url.hostname.replace(/\.supabase\.co$/, ".storage.supabase.co");
  }
  return url.origin;
}

function safeUrl(raw: string): URL {
  const url = new URL(raw);
  if (url.username || url.password || url.search || url.hash ||
      (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))) {
    throw new Error("허용되지 않은 Storage 업로드 주소예요.");
  }
  return url;
}

/** Validate origin and the complete server-granted object path before attaching a JWT. */
export function validateUploadEndpoint(grant: Grant, config: PublicConfig): URL {
  const url = safeUrl(grant.upload_endpoint);
  if (grant.bucket !== "baby-audio" || !grant.object_key || grant.object_key.includes("..") || grant.object_key.startsWith("/")) {
    throw new Error("서버 업로드 경로가 올바르지 않아요.");
  }
  if (grant.method === "STANDARD") {
    const expected = `/storage/v1/object/${grant.bucket}/${grant.object_key}`;
    if (url.origin !== new URL(config.supabaseUrl).origin || decodeURIComponent(url.pathname) !== expected) {
      throw new Error("허용되지 않은 Storage 업로드 주소예요.");
    }
  } else if (grant.method === "TUS") {
    if (url.origin !== storageOrigin(config) || url.pathname !== "/storage/v1/upload/resumable") {
      throw new Error("허용되지 않은 TUS 주소예요.");
    }
  } else {
    throw new Error("지원되지 않는 업로드 방법이에요.");
  }
  return url;
}

function validateResumeUrl(raw: string, endpoint: URL): URL {
  const url = safeUrl(raw);
  if (url.origin !== endpoint.origin || !url.pathname.startsWith(`${endpoint.pathname}/`)) {
    throw new Error("허용되지 않은 TUS 재개 주소예요.");
  }
  return url;
}

function metadata(grant: Grant, mimeType: string): string {
  // Provider-required keys only. No original filename, device name, or user metadata.
  const entries: Array<[string, string]> = [
    ["bucketName", grant.bucket], ["objectName", grant.object_key], ["contentType", mimeType],
  ];
  return entries.map(([name, value]) => `${name} ${btoa(value)}`).join(",");
}

export class StorageUpload {
  private controller: AbortController | null = null;
  private xhr: XMLHttpRequest | null = null;
  private cancelled = false;

  constructor(
    private readonly grant: Grant,
    private readonly identity: Identity,
    private readonly config: PublicConfig,
    private readonly auth: ApiAuthAdapter,
  ) {}

  abort(): void {
    this.cancelled = true;
    this.controller?.abort();
    this.xhr?.abort();
  }

  private async token(): Promise<string> {
    const session = await this.auth.getSession();
    if (this.cancelled) throw new Error("전송을 중단했어요.");
    if (!session || session.userId !== this.identity.userId || !session.accessToken) {
      throw new Error("현재 로그인 권한을 확인할 수 없어요.");
    }
    return session.accessToken;
  }

  private assertGrant(): URL {
    if (this.cancelled) throw new Error("전송을 중단했어요.");
    const expiry = Date.parse(this.grant.expires_at);
    if (!Number.isFinite(expiry) || Date.now() >= expiry) throw new Error("업로드 승인이 만료됐어요.");
    return validateUploadEndpoint(this.grant, this.config);
  }

  async send(blob: Blob, progress: Progress): Promise<void> {
    const endpoint = this.assertGrant();
    if (blob.size > this.grant.max_bytes) throw new Error("서버가 허용한 크기를 초과했어요.");
    if (this.grant.method === "STANDARD") return this.standard(endpoint, blob, progress);
    return this.tus(endpoint, blob, progress);
  }

  private async standard(endpoint: URL, blob: Blob, progress: Progress): Promise<void> {
    const token = await this.token();
    this.assertGrant();
    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      this.xhr = xhr;
      xhr.open("POST", endpoint.href);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.setRequestHeader("apikey", this.config.supabasePublishableKey);
      xhr.setRequestHeader("Content-Type", blob.type);
      xhr.setRequestHeader("x-upsert", "false");
      xhr.upload.onprogress = (event) => progress(event.loaded, blob.size);
      xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error("Storage 전송이 거부됐어요. 권한과 만료 상태를 확인해 주세요."));
      xhr.onerror = () => reject(new Error("연결이 끊겼어요. 같은 음원의 전송 상태를 확인해 주세요."));
      xhr.onabort = () => reject(new Error("전송을 중단했어요."));
      xhr.send(blob);
    }).finally(() => { this.xhr = null; });
  }

  private async tusRequest(url: URL, method: string, headers: Record<string, string>, body?: Blob): Promise<Response> {
    const token = await this.token();
    this.assertGrant();
    const controller = new AbortController();
    this.controller = controller;
    try {
      return await fetch(url, {
        method,
        headers: { ...headers, Authorization: `Bearer ${token}`, apikey: this.config.supabasePublishableKey, "x-upsert": "false", "Tus-Resumable": "1.0.0" },
        body,
        signal: controller.signal,
        cache: "no-store",
      });
    } finally {
      this.controller = null;
    }
  }

  private async tus(endpoint: URL, blob: Blob, progress: Progress): Promise<void> {
    const resume = resumeUrls.get(key(this.identity));
    let uploadUrl: URL;
    let offset = 0;
    if (resume) {
      uploadUrl = validateResumeUrl(resume, endpoint);
      const head = await this.tusRequest(uploadUrl, "HEAD", {});
      if (!head.ok) throw new Error("TUS 재개 상태를 읽지 못했어요. 만료 여부를 확인해 주세요.");
      offset = Number(head.headers.get("Upload-Offset"));
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > blob.size) throw new Error("TUS 재개 위치가 올바르지 않아요.");
      if (offset !== blob.size && offset % TUS_CHUNK_BYTES !== 0) throw new Error("TUS 재개 위치가 6 MiB 청크 경계와 일치하지 않아요.");
    } else {
      const created = await this.tusRequest(endpoint, "POST", {
        "Upload-Length": String(blob.size),
        "Upload-Metadata": metadata(this.grant, blob.type),
      });
      if (!created.ok) throw new Error("TUS 시작이 거부됐어요. 승인과 권한을 확인해 주세요.");
      const location = created.headers.get("Location");
      if (!location) throw new Error("TUS 재개 주소가 없어요.");
      uploadUrl = validateResumeUrl(new URL(location, endpoint).href, endpoint);
      resumeUrls.set(key(this.identity), uploadUrl.href);
    }
    progress(offset, blob.size);
    while (offset < blob.size) {
      const next = Math.min(offset + TUS_CHUNK_BYTES, blob.size);
      const chunk = blob.slice(offset, next);
      const response = await this.tusRequest(uploadUrl, "PATCH", {
        "Upload-Offset": String(offset),
        "Content-Type": "application/offset+octet-stream",
      }, chunk);
      if (!response.ok) throw new Error("TUS 전송이 중단됐어요. 같은 음원에서 재개할 수 있어요.");
      const serverOffset = Number(response.headers.get("Upload-Offset"));
      if (serverOffset !== next) throw new Error("TUS 전송 위치가 일치하지 않아요. 다시 상태를 확인해 주세요.");
      offset = next;
      progress(offset, blob.size);
    }
    resumeUrls.delete(key(this.identity));
  }
}
