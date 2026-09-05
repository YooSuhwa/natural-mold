"""No-follow filesystem boundary for Playwright JSON receipts."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Final

MAX_PLAYWRIGHT_RECEIPT_BYTES: Final = 2 * 1024 * 1024


class PlaywrightReceiptError(RuntimeError):
    """Stable parse failure for an untrusted Playwright receipt."""


def read_playwright_receipt(path: Path) -> str:
    """Read one stable, regular receipt without following a path link."""
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
        ):
            raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
        if before.st_size > MAX_PLAYWRIGHT_RECEIPT_BYTES:
            raise PlaywrightReceiptError("playwright_receipt_too_large")
        if not hasattr(os, "O_NOFOLLOW"):
            raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PlaywrightReceiptError("unsafe_playwright_receipt_file") from error
    with os.fdopen(descriptor, "rb") as receipt:
        try:
            opened = os.fstat(receipt.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or opened.st_mode & 0o022
            ):
                raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
            data = receipt.read(MAX_PLAYWRIGHT_RECEIPT_BYTES + 1)
            after = path.lstat()
            if (
                (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_uid != os.geteuid()
                or after.st_nlink != 1
                or not stat.S_ISREG(after.st_mode)
                or after.st_mode & 0o022
            ):
                raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
        except OSError as error:
            raise PlaywrightReceiptError("invalid_playwright_json") from error
    if len(data) > MAX_PLAYWRIGHT_RECEIPT_BYTES:
        raise PlaywrightReceiptError("playwright_receipt_too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PlaywrightReceiptError("invalid_playwright_json") from error
