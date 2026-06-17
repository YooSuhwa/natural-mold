from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import settings
from app.skills.packager import PackageError, extract_package


def test_extract_package_rejects_uncompressed_total_beyond_package_limit(
    tmp_path: Path,
) -> None:
    # Given: a highly compressed package whose ZIP bytes fit under the limit
    # but whose extracted asset payload is larger than the allowed package size.
    zip_bytes = _compressed_zip_with(
        {
            "SKILL.md": _skill_md("oversized-expanded"),
            "assets/reference.bin": b"0" * 2048,
        }
    )
    assert len(zip_bytes) < 1024

    # When / Then: extraction rejects the expanded total before persisting the package.
    with (
        patch.object(settings, "skill_max_package_bytes", 1024),
        pytest.raises(PackageError, match="package too large after extraction"),
    ):
        extract_package(zip_bytes, tmp_path)


def test_extract_package_streams_large_members_before_size_rejection(
    tmp_path: Path,
) -> None:
    zip_bytes = _compressed_zip_with(
        {
            "SKILL.md": _skill_md("streamed-expanded"),
            "assets/reference.bin": b"0" * 2048,
        }
    )
    original_read = zipfile.ZipFile.read
    original_infolist = zipfile.ZipFile.infolist

    def fail_on_asset_read(
        archive: zipfile.ZipFile,
        name: str,
        pwd: bytes | None = None,
    ) -> bytes:
        if name == "assets/reference.bin":
            raise AssertionError("asset members must be streamed, not read into memory")
        return original_read(archive, name, pwd)

    def forged_infolist(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        forged_infos: list[zipfile.ZipInfo] = []
        for info in original_infolist(archive):
            if info.filename == "assets/reference.bin":
                forged_info = zipfile.ZipInfo(info.filename, info.date_time)
                forged_info.compress_type = info.compress_type
                forged_info.external_attr = info.external_attr
                forged_info.file_size = 1
                forged_infos.append(forged_info)
                continue
            forged_infos.append(info)
        return forged_infos

    with (
        patch.object(settings, "skill_max_package_bytes", 1024),
        patch.object(zipfile.ZipFile, "infolist", forged_infolist),
        patch.object(zipfile.ZipFile, "read", fail_on_asset_read),
        pytest.raises(PackageError, match="package too large after extraction"),
    ):
        extract_package(zip_bytes, tmp_path)


def test_extract_package_rejects_too_many_files(tmp_path: Path) -> None:
    zip_bytes = _compressed_zip_with(
        {
            "SKILL.md": _skill_md("too-many-files"),
            "assets/a.txt": "a",
            "assets/b.txt": "b",
        }
    )

    with (
        patch.object(settings, "skill_max_package_files", 2),
        pytest.raises(PackageError, match="too many files"),
    ):
        extract_package(zip_bytes, tmp_path)


def _compressed_zip_with(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(path, data)
    return buffer.getvalue()


def _skill_md(name: str) -> str:
    return f'---\nname: {name}\ndescription: "demo skill"\nversion: "1.0.0"\n---\n\n# Demo body\n'
