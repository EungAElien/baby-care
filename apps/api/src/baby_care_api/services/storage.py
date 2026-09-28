from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, urlopen

MAX_SOURCE_BYTES = 25_000_000


class StorageError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class DownloadedObject:
    bytes: int
    checksum_sha256: str


class AudioStoragePort(Protocol):
    @property
    def configured(self) -> bool: ...

    def upload_endpoint(self, *, method: str, bucket: str, object_key: str) -> str: ...

    async def download(
        self, *, bucket: str, object_key: str, destination: Path
    ) -> DownloadedObject: ...

    async def upload(
        self, *, bucket: str, object_key: str, source: Path, mime_type: str
    ) -> None: ...

    async def delete(self, *, bucket: str, object_keys: list[str]) -> None: ...

    async def sign_playback(
        self, *, bucket: str, object_key: str, expires_in_seconds: int
    ) -> str: ...


def _retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or status >= 500


class SupabaseAudioStorage:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_key: str,
        storage_url: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._supabase_url = supabase_url.rstrip("/")
        self._storage_url = (storage_url or supabase_url).rstrip("/")
        self._service_key = service_key
        self._timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self._supabase_url and self._storage_url and self._service_key)

    @staticmethod
    def _object_path(bucket: str, object_key: str) -> str:
        return f"{quote(bucket, safe='')}/{quote(object_key, safe='/')}"

    def _headers(self, *, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self._service_key,
            "Authorization": f"Bearer {self._service_key}",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    def upload_endpoint(self, *, method: str, bucket: str, object_key: str) -> str:
        if method == "TUS":
            return f"{self._storage_url}/storage/v1/upload/resumable"
        if method != "STANDARD":
            raise ValueError("Unknown upload method")
        return f"{self._supabase_url}/storage/v1/object/{self._object_path(bucket, object_key)}"

    @staticmethod
    def _raise_http(exc: HTTPError) -> None:
        raise StorageError(
            "STORAGE_HTTP_ERROR",
            retryable=_retryable_status(exc.code),
        ) from exc

    def _download_sync(self, bucket: str, object_key: str, destination: Path) -> DownloadedObject:
        url = (
            f"{self._supabase_url}/storage/v1/object/authenticated/"
            f"{self._object_path(bucket, object_key)}"
        )
        request = Request(url, headers=self._headers(), method="GET")
        digest = hashlib.sha256()
        size = 0
        try:
            with (
                urlopen(request, timeout=self._timeout_seconds) as response,
                destination.open("xb") as output,
            ):
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None and int(declared_length) > MAX_SOURCE_BYTES:
                    raise StorageError("TOO_LARGE", retryable=False)
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_SOURCE_BYTES:
                        raise StorageError("TOO_LARGE", retryable=False)
                    digest.update(chunk)
                    output.write(chunk)
        except HTTPError as exc:
            self._raise_http(exc)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            if isinstance(exc, StorageError):
                raise
            raise StorageError("STORAGE_UNAVAILABLE", retryable=True) from exc
        if size == 0:
            raise StorageError("OBJECT_EMPTY", retryable=False)
        return DownloadedObject(bytes=size, checksum_sha256=digest.hexdigest())

    async def download(
        self, *, bucket: str, object_key: str, destination: Path
    ) -> DownloadedObject:
        try:
            return await asyncio.to_thread(self._download_sync, bucket, object_key, destination)
        except StorageError:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
            raise

    def _upload_sync(self, bucket: str, object_key: str, source: Path, mime_type: str) -> None:
        try:
            data = source.read_bytes()
        except OSError as exc:
            raise StorageError("DERIVATIVE_UNAVAILABLE", retryable=True) from exc
        if not data or len(data) > MAX_SOURCE_BYTES:
            raise StorageError("DERIVATIVE_SIZE_INVALID", retryable=False)
        url = f"{self._supabase_url}/storage/v1/object/{self._object_path(bucket, object_key)}"
        headers = self._headers(content_type=mime_type)
        headers["x-upsert"] = "false"
        request = Request(url, data=data, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                if response.status not in {200, 201}:
                    raise StorageError(
                        "STORAGE_HTTP_ERROR", retryable=_retryable_status(response.status)
                    )
        except HTTPError as exc:
            self._raise_http(exc)
        except (URLError, TimeoutError, OSError) as exc:
            raise StorageError("STORAGE_UNAVAILABLE", retryable=True) from exc

    async def upload(self, *, bucket: str, object_key: str, source: Path, mime_type: str) -> None:
        await asyncio.to_thread(self._upload_sync, bucket, object_key, source, mime_type)

    def _json_request(
        self, url: str, body: dict[str, object], *, method: str = "POST"
    ) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(body, separators=(",", ":")).encode(),
            headers=self._headers(content_type="application/json"),
            method=method,
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read(64 * 1024 + 1)
                if response.status not in {200, 201} or len(payload) > 64 * 1024:
                    raise StorageError(
                        "STORAGE_HTTP_ERROR", retryable=_retryable_status(response.status)
                    )
        except HTTPError as exc:
            self._raise_http(exc)
        except (URLError, TimeoutError, OSError) as exc:
            raise StorageError("STORAGE_UNAVAILABLE", retryable=True) from exc
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageError("STORAGE_RESPONSE_INVALID", retryable=True) from exc
        if not isinstance(decoded, dict):
            raise StorageError("STORAGE_RESPONSE_INVALID", retryable=True)
        return decoded

    async def delete(self, *, bucket: str, object_keys: list[str]) -> None:
        if not object_keys:
            return
        if len(object_keys) > 100 or any(not key or ".." in key.split("/") for key in object_keys):
            raise StorageError("OBJECT_KEY_INVALID", retryable=False)

        def remove() -> None:
            request = Request(
                f"{self._supabase_url}/storage/v1/object/{quote(bucket, safe='')}",
                data=json.dumps({"prefixes": object_keys}, separators=(",", ":")).encode(),
                headers=self._headers(content_type="application/json"),
                method="DELETE",
            )
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    response.read(64 * 1024 + 1)
                    if response.status not in {200, 204}:
                        raise StorageError(
                            "STORAGE_HTTP_ERROR",
                            retryable=_retryable_status(response.status),
                        )
            except HTTPError as exc:
                self._raise_http(exc)
            except (URLError, TimeoutError, OSError) as exc:
                raise StorageError("STORAGE_UNAVAILABLE", retryable=True) from exc

        await asyncio.to_thread(remove)

    def _sign_sync(self, bucket: str, object_key: str, expires_in_seconds: int) -> str:
        if not 1 <= expires_in_seconds <= 60:
            raise ValueError("Playback lifetime must be between 1 and 60 seconds")
        url = f"{self._supabase_url}/storage/v1/object/sign/{self._object_path(bucket, object_key)}"
        payload = self._json_request(url, {"expiresIn": expires_in_seconds})
        signed = payload.get("signedURL") or payload.get("signedUrl")
        if not isinstance(signed, str) or not signed:
            raise StorageError("STORAGE_RESPONSE_INVALID", retryable=True)
        # Storage API responses use both gateway-relative (`/storage/v1/...`) and
        # service-relative (`/object/sign/...`) paths depending on deployment.
        # Normalize the latter without accepting an arbitrary off-origin URL.
        if signed.startswith("/object/sign/"):
            absolute = f"{self._supabase_url}/storage/v1{signed}"
        else:
            absolute = urljoin(f"{self._supabase_url}/", signed)
        expected = urlsplit(self._supabase_url)
        actual = urlsplit(absolute)
        if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
            raise StorageError("STORAGE_RESPONSE_INVALID", retryable=False)
        if not actual.path.startswith("/storage/v1/object/sign/"):
            raise StorageError("STORAGE_RESPONSE_INVALID", retryable=False)
        return absolute

    async def sign_playback(self, *, bucket: str, object_key: str, expires_in_seconds: int) -> str:
        return await asyncio.to_thread(self._sign_sync, bucket, object_key, expires_in_seconds)


class UnconfiguredAudioStorage:
    @property
    def configured(self) -> bool:
        return False

    def upload_endpoint(self, *, method: str, bucket: str, object_key: str) -> str:
        raise StorageError("STORAGE_UNCONFIGURED", retryable=True)

    async def download(
        self, *, bucket: str, object_key: str, destination: Path
    ) -> DownloadedObject:
        raise StorageError("STORAGE_UNCONFIGURED", retryable=True)

    async def upload(self, *, bucket: str, object_key: str, source: Path, mime_type: str) -> None:
        raise StorageError("STORAGE_UNCONFIGURED", retryable=True)

    async def delete(self, *, bucket: str, object_keys: list[str]) -> None:
        raise StorageError("STORAGE_UNCONFIGURED", retryable=True)

    async def sign_playback(self, *, bucket: str, object_key: str, expires_in_seconds: int) -> str:
        raise StorageError("STORAGE_UNCONFIGURED", retryable=True)
