"""One storage interface for files, on local disk or an S3-compatible store (plan §11.6).

``ObjectStore`` writes, reads (whole or in chunks), checks, lists, copies and deletes
objects by key, and signs short-lived URLs where the backend can. Both implementations use
the same keys: relative paths such as ``tenants/<company>/knowledge/<id>/<file>`` (the
existing company folder rule, ``storage.paths``), never absolute, never with ``..``.

Which one runs is the install profile's choice (``STORAGE_BACKEND``, decision D16): local
disk for a single server, S3-compatible storage (the bundled SeaweedFS or the company's
own) when several servers must see the same files. This layer is the base for new
features (knowledge sources first); moving the existing file kinds onto it is the
separate B3 storage switch with its migration tool.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal, Protocol

from app.config import settings

DEFAULT_CHUNK: Final = 1024 * 1024
PROBE_PREFIX: Final = "tmp/moldy-probe/"


class ObjectStoreError(RuntimeError):
    """The store refused or could not be reached; the message names no secrets."""


class ObjectNotFoundError(ObjectStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int


@dataclass(frozen=True, slots=True)
class StoreHealth:
    provider: str
    ok: bool
    detail: str
    bucket: str | None = None


def check_key(key: str) -> str:
    """A relative, normalised object key; anything else is refused."""
    if not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise ValueError("object keys are relative paths")
    parts = PurePosixPath(key).parts
    if any(part in ("..", ".") for part in parts) or str(PurePosixPath(*parts)) != key:
        raise ValueError("object keys must not contain '.', '..' or empty segments")
    return key


class ObjectStore(Protocol):
    provider: str
    bucket: str | None

    async def put_bytes(
        self, key: str, data: bytes, *, content_type: str | None = None
    ) -> None: ...

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> None: ...

    async def read_bytes(self, key: str) -> bytes: ...

    def iter_chunks(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None: ...

    async def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[ObjectInfo]: ...

    async def copy(self, source: str, target: str) -> None: ...

    async def signed_url(
        self, key: str, *, method: Literal["get", "put"] = "get", expires_seconds: int = 300
    ) -> str | None: ...

    async def health(self) -> StoreHealth: ...


class LocalObjectStore:
    """Files under one root folder; the key is the path below it."""

    provider = "local"
    bucket: str | None = None

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.storage_local_root).resolve()

    def _path(self, key: str) -> Path:
        target = (self.root / check_key(key)).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("object key escapes the storage root")
        return target

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        del content_type
        await asyncio.to_thread(_write_atomic, self._path(key), data)

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> None:
        del content_type
        target = self._path(key)

        def copy() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
            shutil.copyfile(path, partial)
            partial.replace(target)

        await asyncio.to_thread(copy)

    async def read_bytes(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc

    async def iter_chunks(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        path = self._path(key)
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        try:
            while chunk := await asyncio.to_thread(handle.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, missing_ok=True)

    async def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[ObjectInfo]:
        base = self.root / prefix

        def walk() -> list[ObjectInfo]:
            found: list[ObjectInfo] = []
            start = base if base.is_dir() else base.parent
            if not start.is_dir():
                return found
            for path in sorted(start.rglob("*")):
                key = path.relative_to(self.root).as_posix()
                if path.is_file() and key.startswith(prefix) and not path.name.endswith(".part"):
                    found.append(ObjectInfo(key, path.stat().st_size))
                    if len(found) >= limit:
                        break
            return found

        return await asyncio.to_thread(walk)

    async def copy(self, source: str, target: str) -> None:
        source_path = self._path(source)
        if not await asyncio.to_thread(source_path.is_file):
            raise ObjectNotFoundError(source)
        await self.put_file(target, source_path)

    async def signed_url(
        self, key: str, *, method: Literal["get", "put"] = "get", expires_seconds: int = 300
    ) -> str | None:
        """Local files are always served through the backend."""
        del key, method, expires_seconds
        return None

    async def health(self) -> StoreHealth:
        return await _probe(self, f"local folder {self.root}")


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    partial.write_bytes(data)
    partial.replace(target)


class S3ObjectStore:
    """An S3-compatible bucket (SeaweedFS, Ceph RGW, cloud S3) through boto3 in a thread."""

    provider = "s3"

    def __init__(self, client: Any = None, *, bucket: str | None = None) -> None:
        self.bucket: str | None = bucket or settings.storage_s3_bucket
        if not self.bucket:
            raise ObjectStoreError("STORAGE_S3_BUCKET is not set")
        self._client = client or _s3_client()

    def _extra(self, content_type: str | None) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        if settings.storage_s3_server_side_encryption:
            extra["ServerSideEncryption"] = settings.storage_s3_server_side_encryption
        return extra

    async def _call(self, name: str, **kwargs: Any) -> Any:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return await asyncio.to_thread(getattr(self._client, name), **kwargs)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                raise ObjectNotFoundError(kwargs.get("Key", "")) from exc
            raise ObjectStoreError(f"S3 {name} failed: {code or 'error'}") from exc
        except BotoCoreError as exc:
            raise ObjectStoreError(f"S3 {name} failed: {type(exc).__name__}") from exc

    async def put_bytes(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        await self._call(
            "put_object",
            Bucket=self.bucket,
            Key=check_key(key),
            Body=data,
            **self._extra(content_type),
        )

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> None:
        # upload_file switches to multipart for large files.
        await self._call(
            "upload_file",
            Filename=str(path),
            Bucket=self.bucket,
            Key=check_key(key),
            ExtraArgs=self._extra(content_type) or None,
        )

    async def read_bytes(self, key: str) -> bytes:
        response = await self._call("get_object", Bucket=self.bucket, Key=check_key(key))
        return await asyncio.to_thread(response["Body"].read)

    async def iter_chunks(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        response = await self._call("get_object", Bucket=self.bucket, Key=check_key(key))
        body = response["Body"]
        try:
            while chunk := await asyncio.to_thread(body.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def exists(self, key: str) -> bool:
        try:
            await self._call("head_object", Bucket=self.bucket, Key=check_key(key))
        except ObjectNotFoundError:
            return False
        return True

    async def delete(self, key: str) -> None:
        await self._call("delete_object", Bucket=self.bucket, Key=check_key(key))

    async def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[ObjectInfo]:
        found: list[ObjectInfo] = []
        token: str | None = None
        while len(found) < limit:
            page = await self._call(
                "list_objects_v2",
                Bucket=self.bucket,
                Prefix=prefix,
                MaxKeys=min(1000, limit - len(found)),
                **({"ContinuationToken": token} if token else {}),
            )
            found.extend(
                ObjectInfo(item["Key"], int(item["Size"])) for item in page.get("Contents", [])
            )
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return found[:limit]

    async def copy(self, source: str, target: str) -> None:
        await self._call(
            "copy_object",
            Bucket=self.bucket,
            Key=check_key(target),
            CopySource={"Bucket": self.bucket, "Key": check_key(source)},
        )

    async def signed_url(
        self, key: str, *, method: Literal["get", "put"] = "get", expires_seconds: int = 300
    ) -> str | None:
        operation = "get_object" if method == "get" else "put_object"
        return await asyncio.to_thread(
            self._client.generate_presigned_url,
            operation,
            Params={"Bucket": self.bucket, "Key": check_key(key)},
            ExpiresIn=expires_seconds,
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket when it is missing (the bundled server starts empty)."""
        try:
            await self._call("head_bucket", Bucket=self.bucket)
        except ObjectStoreError:
            await self._call("create_bucket", Bucket=self.bucket)

    async def health(self) -> StoreHealth:
        try:
            if settings.storage_s3_create_bucket:
                await self.ensure_bucket()
        except ObjectStoreError as exc:
            return StoreHealth(self.provider, False, str(exc), self.bucket)
        return await _probe(self, f"bucket {self.bucket}")


def _s3_client() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.storage_s3_endpoint_url or None,
        region_name=settings.storage_s3_region or None,
        aws_access_key_id=settings.storage_s3_access_key_id or None,
        aws_secret_access_key=settings.storage_s3_secret_access_key or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if settings.storage_s3_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


async def _probe(store: ObjectStore, where: str) -> StoreHealth:
    """Write, read back and delete one small object."""
    key = f"{PROBE_PREFIX}{uuid.uuid4().hex}"
    payload = b"moldy-storage-probe"
    try:
        await store.put_bytes(key, payload, content_type="text/plain")
        ok = await store.read_bytes(key) == payload
        await store.delete(key)
    except (ObjectStoreError, OSError) as exc:
        return StoreHealth(store.provider, False, f"{where}: {exc}", store.bucket)
    return StoreHealth(
        store.provider, ok, where if ok else f"{where}: read back differed", store.bucket
    )


_STORE: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """The install's store (``STORAGE_BACKEND``), built once per process."""
    global _STORE  # noqa: PLW0603 - one client per process
    if _STORE is None:
        store: ObjectStore = (
            S3ObjectStore() if settings.storage_backend == "s3" else LocalObjectStore()
        )
        _STORE = store
        return store
    return _STORE


def reset_object_store() -> None:
    global _STORE  # noqa: PLW0603
    _STORE = None
