from __future__ import annotations

from pathlib import Path

import pytest

from app.services.artifact_paths import (
    ArtifactPathError,
    artifact_kind_for,
    normalize_output_path,
    safe_storage_filename,
)


def test_normalize_output_path_accepts_nested_markdown_file(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    target = base / "report" / "final.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Final", encoding="utf-8")

    normalized = normalize_output_path(base, target)

    assert normalized.logical_path == "report/final.md"
    assert normalized.display_name == "final.md"
    assert normalized.extension == "md"
    assert normalized.artifact_kind == "markdown"
    assert normalized.mime_type in {"text/markdown", "text/x-markdown", "text/plain"}


def test_normalize_output_path_rejects_escape(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    base.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ArtifactPathError):
        normalize_output_path(base, outside)


def test_normalize_output_path_rejects_preview_cache(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    target = base / ".previews" / "image.webp"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"cache")

    with pytest.raises(ArtifactPathError):
        normalize_output_path(base, target)


def test_normalize_output_path_rejects_symlink(tmp_path: Path) -> None:
    base = tmp_path / "outputs"
    base.mkdir()
    source = tmp_path / "real.txt"
    source.write_text("real", encoding="utf-8")
    link = base / "link.txt"
    link.symlink_to(source)

    with pytest.raises(ArtifactPathError):
        normalize_output_path(base, link)


def test_safe_storage_filename_removes_path_separators_and_control_chars() -> None:
    assert safe_storage_filename("../weird:name\n.md") == "_weird_name_.md"


@pytest.mark.parametrize(
    ("mime_type", "extension", "expected"),
    [
        ("image/png", "png", "image"),
        ("text/markdown", "md", "markdown"),
        ("application/pdf", "pdf", "pdf"),
        ("application/octet-stream", "py", "code"),
        ("application/octet-stream", "csv", "data"),
        ("application/octet-stream", "json", "data"),
        ("application/octet-stream", "yaml", "data"),
        ("application/octet-stream", "yml", "data"),
        ("application/octet-stream", "toml", "data"),
        ("application/octet-stream", "docx", "document"),
        ("application/octet-stream", "hwp", "document"),
        ("application/octet-stream", "hwpx", "document"),
        ("application/octet-stream", "dxf", "cad"),
        ("application/octet-stream", "bin", "other"),
    ],
)
def test_artifact_kind_for_known_types(
    mime_type: str,
    extension: str,
    expected: str,
) -> None:
    assert artifact_kind_for(mime_type, extension) == expected


@pytest.mark.parametrize(
    ("filename", "expected_mime"),
    [
        ("sample.hwp", "application/x-hwp"),
        ("sample.hwpx", "application/x-hwpx"),
        (
            "sample.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "sample.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            "sample.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
    ],
)
def test_normalize_output_path_uses_custom_document_mime_types(
    tmp_path: Path, filename: str, expected_mime: str
) -> None:
    base = tmp_path / "outputs"
    target = base / filename
    target.parent.mkdir(parents=True)
    target.write_bytes(b"artifact")

    normalized = normalize_output_path(base, target)

    assert normalized.mime_type == expected_mime
