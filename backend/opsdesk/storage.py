"""File storage for attachments and inbound stashes .

``STORAGE_BACKEND=local`` writes under MEDIA_ROOT; ``s3`` uses a private bucket (SSE, short-lived presigned
download URLs). Keys are opaque relative paths such as ``t7/tickets/42/9f2c...-report.pdf``.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path
from typing import Protocol

from .settings import settings

log = logging.getLogger(__name__)


class StorageError(Exception):
    pass


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str = "") -> str: ...
    def read(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def download_url(self, key: str, filename: str, content_type: str, expires: int) -> str | None: ...


def _safe_key(key: str) -> str:
    key = key.replace("\\", "/").lstrip("/")
    parts = [p for p in key.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise StorageError("invalid storage key")
    return "/".join(parts)


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / _safe_key(key)).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str = "") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        stem, suffix = path.stem, path.suffix
        while path.exists():  # never overwrite an existing object
            n += 1
            path = path.with_name(f"{stem}_{n}{suffix}")
        path.write_bytes(data)
        return str(path.relative_to(self.root.resolve())).replace(os.sep, "/")

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def download_url(self, key: str, filename: str, content_type: str, expires: int) -> str | None:
        return None  # served through the API


class S3Storage:
    def __init__(self) -> None:
        import boto3
        self.bucket = settings.s3_bucket
        if not self.bucket:
            raise StorageError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        self.client = boto3.client("s3", region_name=settings.s3_region or None,
                                   endpoint_url=settings.s3_endpoint_url or None)

    def save(self, key: str, data: bytes, content_type: str = "") -> str:
        key = _safe_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type or mimetypes.guess_type(key)[0] or "application/octet-stream",
                               ServerSideEncryption=settings.s3_sse)
        return key

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=_safe_key(key))["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_safe_key(key))

    def download_url(self, key: str, filename: str, content_type: str, expires: int) -> str | None:
        safe_name = os.path.basename(filename).replace('"', "")
        return self.client.generate_presigned_url(
            "get_object", ExpiresIn=expires,
            Params={"Bucket": self.bucket, "Key": _safe_key(key),
                    "ResponseContentDisposition": f'attachment; filename="{safe_name}"',
                    "ResponseContentType": content_type or "application/octet-stream"})


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.storage_backend == "s3" else LocalStorage(settings.media_root)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
