"""The object storage layer (app/storage/object_store.py, plan §11.6).

The same contract runs on local disk here and on a real S3-compatible server in
``tests/integration/test_object_store_s3.py`` (bundled SeaweedFS).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.object_store import (
    LocalObjectStore,
    ObjectNotFoundError,
    ObjectStore,
    check_key,
    get_object_store,
    reset_object_store,
)

KEY = "tenants/t1/knowledge/doc/report.pdf"


async def store_contract(store: ObjectStore, tmp_path: Path) -> None:
    """What every implementation must do (shared with the S3 integration test)."""
    await store.put_bytes(KEY, b"hello", content_type="application/pdf")
    assert await store.exists(KEY)
    assert await store.read_bytes(KEY) == b"hello"

    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (3 * 1024 * 1024 + 7))
    await store.put_file("tenants/t1/knowledge/doc/big.bin", source)
    chunks = [c async for c in store.iter_chunks("tenants/t1/knowledge/doc/big.bin", 1024 * 1024)]
    assert sum(len(c) for c in chunks) == source.stat().st_size and len(chunks) >= 3

    await store.copy(KEY, "tenants/t1/knowledge/doc/copy.pdf")
    listed = await store.list_prefix("tenants/t1/knowledge/doc/")
    assert sorted(item.key for item in listed) == [
        "tenants/t1/knowledge/doc/big.bin",
        "tenants/t1/knowledge/doc/copy.pdf",
        KEY,
    ]
    assert {item.key: item.size for item in listed}[KEY] == 5
    assert await store.list_prefix("tenants/t2/") == []

    await store.delete(KEY)
    await store.delete(KEY)  # deleting a missing object is fine
    assert not await store.exists(KEY)
    with pytest.raises(ObjectNotFoundError):
        await store.read_bytes(KEY)
    health = await store.health()
    assert health.ok, health.detail
    assert await store.list_prefix("tmp/moldy-probe/") == []  # the probe cleans up


async def test_the_local_store_meets_the_contract(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")

    await store_contract(store, tmp_path)

    assert await store.signed_url("tenants/t1/a") is None  # served through the backend


@pytest.mark.parametrize(
    "key", ["", "/etc/passwd", "a/../b", "a//b", "./a", "a/./b", "a\\b", "a\x00b"]
)
def test_unsafe_keys_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match="object keys"):
        check_key(key)


async def test_a_key_cannot_leave_the_local_root(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError, match="object keys"):
        await store.put_bytes("../outside", b"x")
    assert not (tmp_path / "outside").exists()


def test_the_backend_setting_picks_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    reset_object_store()
    monkeypatch.setattr(settings, "storage_backend", "local")
    assert get_object_store().provider == "local"
    reset_object_store()
