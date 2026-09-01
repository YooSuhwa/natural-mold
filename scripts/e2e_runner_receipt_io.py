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
        if not stat.S_ISREG(before.st_mode):
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
            ):
                raise PlaywrightReceiptError("unsafe_playwright_receipt_file")
            data = receipt.read(MAX_PLAYWRIGHT_RECEIPT_BYTES + 1)
        except OSError as error:
            raise PlaywrightReceiptError("invalid_playwright_json") from error
    if len(data) > MAX_PLAYWRIGHT_RECEIPT_BYTES:
        raise PlaywrightReceiptError("playwright_receipt_too_large")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PlaywrightReceiptError("invalid_playwright_json") from error
