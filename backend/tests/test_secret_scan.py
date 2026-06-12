"""M6 Slice C — Secret scanner unit tests (Phase 1 출시 게이트).

Spec §13.1 + Bezos OI-4. Targets ``app.marketplace.secret_scan``:

* ``SECRET_FILE_PATTERNS`` — 9 filename rules (``.env``, PEM, etc.).
* ``SECRET_CONTENT_PATTERNS`` — 6 content rules with **word boundaries**
  on ``sk-…`` (OI-4 false-positive guard).
* ``scan_package(extracted_dir)`` — full walk + dedup contract.
* Performance + safety guards: 256 KB cap, binary-extension skip,
  symlink skip.

Pairs with the integration smoke in ``test_marketplace_publish.py`` —
젠슨's `test_upload_rejects_env_file` / `test_upload_rejects_openai_key_in_content`
/ `test_upload_allows_placeholder_sk_example` verify the publish/upload
router-layer hookup. This file pins every individual regex so a future
tweak surfaces immediately at unit-test granularity.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.marketplace.secret_scan import (
    SECRET_CONTENT_PATTERNS,
    SECRET_FILE_PATTERNS,
    SecretFinding,
    is_suspicious_secret_value,
    scan_package,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _layout(root: Path, files: dict[str, bytes | str]) -> None:
    """Materialize ``files`` (POSIX relative paths) under ``root``."""

    for rel, payload in files.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, str):
            dest.write_text(payload, encoding="utf-8")
        else:
            dest.write_bytes(payload)


def _findings_for(root: Path) -> list[SecretFinding]:
    return scan_package(root)


# ===========================================================================
# Pattern tables sanity — pin the count + key names
# ===========================================================================


class TestPatternRegistry:
    """The exact number + key names of the pattern lists are part of the
    public contract — changes here force ARCHITECTURE.md / Spec §13.1
    updates too."""

    def test_file_pattern_count_pinned(self) -> None:
        # 9 documented patterns. Adding one without updating Spec §13.1
        # would let a new vector through silently.
        assert len(SECRET_FILE_PATTERNS) == 9, (
            f"SECRET_FILE_PATTERNS now has {len(SECRET_FILE_PATTERNS)} "
            f"entries — update Spec §13.1 + this test together"
        )

    def test_content_pattern_count_pinned(self) -> None:
        # 13 documented patterns: sk-, PEM, AWS, GOOGLE_APPLICATION_CREDENTIALS,
        # ghp_, sk_live_, URL userinfo (scheme://user:password@host), Slack
        # (xox[baprs]-), Telegram bot, Google OAuth (ya29.), GitHub
        # ghs_/gho_/ghu_, GitLab glpat-, token-bearing query string.
        # Same rationale.
        assert len(SECRET_CONTENT_PATTERNS) == 13


# ===========================================================================
# Filename patterns
# ===========================================================================


class TestFilenamePatterns:
    """Every documented filename pattern must trigger via the integration
    surface (``scan_package``) — not just the raw regex. Keeps the test
    aligned with the actual blocking path operators will exercise."""

    @pytest.mark.parametrize(
        "filename",
        [
            ".env",
            ".env.local",
            ".env.production",
            "secrets.env",
            "id_rsa.pem",
            "server.key",
            "cert.p12",
            "cert.pfx",
            "cookies.txt",
            "cookies-jar.json",
            "token.json",
            "tokens",  # bare prefix match
            "credentials.json",
            "my-app-credentials.json",
        ],
    )
    def test_filename_pattern_blocks(self, tmp_path: Path, filename: str) -> None:
        _layout(tmp_path, {filename: "irrelevant body\n"})
        findings = _findings_for(tmp_path)
        kinds = {f.kind for f in findings}
        paths = {f.path for f in findings}
        assert "filename" in kinds, (
            f"filename {filename!r} not flagged — pattern table miss"
        )
        assert filename in paths

    @pytest.mark.parametrize(
        "filename",
        [
            "README.md",
            "SKILL.md",
            "config.yaml",
            "settings.json",  # not credentials.json
            "environments.md",  # contains "env" but doesn't match ^\.env...
            "tokenizer.py",  # starts with "token" — DOES match ``^token.*``
        ],
    )
    def test_legitimate_filename_not_flagged_except_token_prefix(
        self, tmp_path: Path, filename: str
    ) -> None:
        """Files like ``tokenizer.py`` will match ``^token.*$`` — that's
        a known broad rule (Spec §13.1). Filter the assertion accordingly
        so the test documents which legitimate names *do* trigger."""

        _layout(tmp_path, {filename: "ok"})
        findings = _findings_for(tmp_path)
        flagged = any(
            f.kind == "filename" and f.path == filename for f in findings
        )
        if filename.startswith("token"):
            # Documented over-block: the operator gets a clear error
            # ("rename tokenizer.py before publishing") rather than a
            # silent leak.
            assert flagged
        else:
            assert not flagged, (
                f"legitimate filename {filename!r} blocked — over-broad regex"
            )


# ===========================================================================
# Content patterns (esp. OI-4 sk- boundary)
# ===========================================================================


class TestContentPatterns:
    @pytest.mark.parametrize(
        "payload",
        [
            b"OPENAI_API_KEY=sk-abc1234567890ABCDEFghij\n",
            b'config={"key":"sk-X1Y2Z3aaaaaaaaaaaaaaaaaa"}\n',
            # Length boundary — exactly 20 chars after the prefix.
            b"key=sk-12345678901234567890\n",
        ],
    )
    def test_sk_pattern_blocks_real_keys(
        self, tmp_path: Path, payload: bytes
    ) -> None:
        _layout(tmp_path, {"scripts/example.py": payload})
        findings = _findings_for(tmp_path)
        assert any(
            f.kind == "content"
            and r"\bsk-" in f.pattern
            and f.path == "scripts/example.py"
            for f in findings
        ), f"sk- key not flagged in {payload!r}"

    @pytest.mark.parametrize(
        "payload",
        [
            # Documentation placeholder — Bezos OI-4 word-boundary guard.
            b"Set OPENAI_API_KEY to sk-example in your .env\n",
            b"Example: api_key=sk-foo (replace with your own)\n",
            # 19 chars after the prefix — one short of the {20,} threshold.
            b"too-short=sk-1234567890123456789\n",
            # No word boundary on the left — fused token.
            b"prefixsk-12345678901234567890\n",
        ],
    )
    def test_sk_pattern_allows_placeholders_and_short_values(
        self, tmp_path: Path, payload: bytes
    ) -> None:
        """OI-4 guard — the ``\\bsk-…{20,}\\b`` regex must NOT flag
        placeholder docstrings or fragments below the length threshold."""

        _layout(tmp_path, {"docs/usage.md": payload})
        findings = _findings_for(tmp_path)
        sk_hits = [
            f
            for f in findings
            if f.kind == "content" and r"\bsk-" in f.pattern
        ]
        assert not sk_hits, (
            f"false positive on placeholder payload {payload!r}: {sk_hits}"
        )

    def test_pem_private_key_blocked(self, tmp_path: Path) -> None:
        body = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA…\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        _layout(tmp_path, {"keys/key.txt": body})
        findings = _findings_for(tmp_path)
        assert any(
            f.kind == "content" and "PRIVATE KEY" in f.pattern
            for f in findings
        )

    @pytest.mark.parametrize(
        "key_label",
        [
            "BEGIN PRIVATE KEY",
            "BEGIN RSA PRIVATE KEY",
            "BEGIN EC PRIVATE KEY",
            "BEGIN DSA PRIVATE KEY",
            "BEGIN OPENSSH PRIVATE KEY",
            "BEGIN ENCRYPTED PRIVATE KEY",
        ],
    )
    def test_pem_pattern_covers_all_documented_variants(
        self, tmp_path: Path, key_label: str
    ) -> None:
        """Spec §13.1 lists six PEM variants — pin them all."""

        body = f"-----{key_label}-----\nMIICabc…\n-----END {key_label[6:]}-----\n"
        _layout(tmp_path, {"snippet.md": body})
        findings = _findings_for(tmp_path)
        assert any(f.kind == "content" for f in findings), (
            f"PEM variant {key_label!r} not detected"
        )

    def test_aws_secret_access_key_env_export_blocked(
        self, tmp_path: Path
    ) -> None:
        _layout(
            tmp_path,
            {
                "README.md": "export AWS_SECRET_ACCESS_KEY=AKIA1234567890ABCDEF\n",
            },
        )
        findings = _findings_for(tmp_path)
        assert any(
            "AWS_SECRET_ACCESS_KEY" in f.pattern for f in findings
        )

    def test_google_application_credentials_blocked(
        self, tmp_path: Path
    ) -> None:
        _layout(
            tmp_path,
            {
                "config.sh": (
                    "GOOGLE_APPLICATION_CREDENTIALS=/etc/gcp/key.json\n"
                ),
            },
        )
        findings = _findings_for(tmp_path)
        assert any(
            "GOOGLE_APPLICATION_CREDENTIALS" in f.pattern for f in findings
        )

    def test_github_personal_access_token_blocked(self, tmp_path: Path) -> None:
        _layout(
            tmp_path,
            {"scripts/probe.py": b"TOKEN = 'ghp_" + b"A" * 35 + b"'\n"},
        )
        findings = _findings_for(tmp_path)
        assert any(r"\bghp_" in f.pattern for f in findings)

    def test_stripe_live_key_blocked(self, tmp_path: Path) -> None:
        _layout(
            tmp_path,
            {"src/billing.py": b"stripe.api_key = 'sk_live_" + b"X" * 30 + b"'\n"},
        )
        findings = _findings_for(tmp_path)
        assert any(r"\bsk_live_" in f.pattern for f in findings)

    @pytest.mark.parametrize(
        ("payload", "needle"),
        [
            # Token literals are split (``b"xoxb-" + b"…"``) so GitHub
            # push-protection doesn't flag these synthetic fixtures; the
            # scanner still receives the reassembled value at runtime.
            (b"SLACK_TOKEN=xoxb-" + b"1234567890-ABCDEFGHIJKLMNOP\n", r"xox"),
            (b"SLACK_TOKEN=xoxp-" + b"9999999999-secretsecretsecret\n", r"xox"),
            (b"BOT=123456789:AAEhBOweik6ad6PsVFktmf7g3HxyzABCDEF\n", r"\d{6,}:"),
            (b"token=ya29.A0ARrdaM-abcdefghijklmnop\n", r"ya29\."),
            (b"GH=ghs_" + b"A" * 36 + b"\n", r"gh[sou]_"),
            (b"GH=gho_" + b"B" * 36 + b"\n", r"gh[sou]_"),
            (b"GH=ghu_" + b"C" * 36 + b"\n", r"gh[sou]_"),
            (b"GL=glpat-" + b"abcdefABCDEF12345678\n", r"glpat-"),
            (b"url=https://api.example.com/v1?access_token=abc123secret\n", r"access_token"),
            (b"url=https://api.example.com/v1?api_key=zzz999\n", r"access_token"),
        ],
    )
    def test_new_provider_token_patterns_block(
        self, tmp_path: Path, payload: bytes, needle: str
    ) -> None:
        _layout(tmp_path, {"config/env.sh": payload})
        findings = _findings_for(tmp_path)
        assert any(
            f.kind == "content" and needle in f.pattern for f in findings
        ), f"new pattern {needle!r} did not flag {payload!r}"

    def test_new_patterns_allow_placeholders(self, tmp_path: Path) -> None:
        """Interpolation placeholders must still pass the new query-string
        and token patterns (best-effort false-positive guard)."""

        _layout(
            tmp_path,
            {
                "config/ok.sh": (
                    "url=https://api.example.com/v1?token={{$credentials.api}}\n"
                    "SLACK=normal-value\n"
                    "VERSION=1.2.3\n"
                ),
            },
        )
        findings = _findings_for(tmp_path)
        assert findings == [], f"placeholder/benign config flagged: {findings}"


# ===========================================================================
# Performance / safety guards
# ===========================================================================


class TestGuards:
    def test_binary_file_skipped_for_content_scan(self, tmp_path: Path) -> None:
        """Binary extensions skip the content scan. Filename pattern can
        still fire — verified here by putting the AWS pattern inside a
        ``.png`` file."""

        _layout(
            tmp_path,
            {"assets/diagram.png": b"AWS_SECRET_ACCESS_KEY=fake"},
        )
        findings = _findings_for(tmp_path)
        # No content finding — extension is in `_BINARY_EXTENSIONS`.
        assert not any(
            f.kind == "content" and f.path == "assets/diagram.png"
            for f in findings
        ), "binary file scanned for content — wastes IO + false positives"

    def test_large_file_only_scans_head(self, tmp_path: Path) -> None:
        """``_MAX_CONTENT_SCAN_BYTES`` (256 KB) — the scanner reads only
        the head. Secret hidden past the cap is NOT detected, which is
        an explicit Spec §13.1 trade-off (operators expect publish to
        succeed for legitimate but large skill scripts).

        Test: build a 300 KB file where the secret lives at offset 280 KB.
        The first 256 KB are filler; the secret is past the cap → no hit.
        """

        cap = 256 * 1024
        filler = b"A" * (cap + 5_000)  # 256 KB + 5 KB before the secret
        secret = b"\nGOOGLE_APPLICATION_CREDENTIALS=/leak\n"
        _layout(tmp_path, {"scripts/big.py": filler + secret})
        findings = _findings_for(tmp_path)
        # Secret past cap → not detected.
        assert not any(
            "GOOGLE_APPLICATION_CREDENTIALS" in f.pattern for f in findings
        ), (
            "scanner read past 256 KB cap — performance guard broken"
        )

        # Secret in the head → still detected (sanity check the cap
        # didn't break legitimate scanning).
        _layout(tmp_path, {"scripts/small.py": secret + filler})
        findings2 = _findings_for(tmp_path)
        assert any(
            "GOOGLE_APPLICATION_CREDENTIALS" in f.pattern
            and f.path == "scripts/small.py"
            for f in findings2
        )

    def test_symlink_skipped(self, tmp_path: Path) -> None:
        """``_iter_files`` filters symlinks — defense in depth on top of
        the packager's symlink rejection."""

        # Real file with a secret.
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        secret_real = real_dir / "secret.env"
        secret_real.write_text("OPENAI_API_KEY=sk-realrealrealrealrealreal\n")

        # Package directory linking to the real file.
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        # Skip the test if the platform can't symlink.
        try:
            (pkg / "linked.env").symlink_to(secret_real)
        except (OSError, NotImplementedError):
            pytest.skip("platform doesn't support symlinks")
        # Also drop a legitimate file so the scan walks something real.
        (pkg / "SKILL.md").write_text("# ok\n")

        findings = _findings_for(pkg)
        # Symlink itself is skipped — `linked.env` should not appear.
        assert not any(f.path == "linked.env" for f in findings), (
            "symlink walked despite _iter_files skip — secret leak vector"
        )


# ===========================================================================
# Walk + dedup contract
# ===========================================================================


class TestScanPackageWalk:
    def test_deeply_nested_directories_walked(self, tmp_path: Path) -> None:
        """Default ``Path.rglob('*')`` walks at unlimited depth — pin the
        contract so a future refactor doesn't accidentally cap depth."""

        deep = "a/b/c/d/e/f/g/.env"
        _layout(tmp_path, {deep: "x"})
        findings = _findings_for(tmp_path)
        assert any(f.path == deep for f in findings)

    def test_multiple_files_each_reported(self, tmp_path: Path) -> None:
        _layout(
            tmp_path,
            {
                ".env": "x",
                "secrets.env": "y",
                "config/private.pem": "-----BEGIN PRIVATE KEY-----\nabc",
            },
        )
        findings = _findings_for(tmp_path)
        paths = {f.path for f in findings}
        # All three flagged. ``private.pem`` may match BOTH filename and
        # content rules — dedup is on (path, pattern) so the file path
        # appears at least once.
        assert ".env" in paths
        assert "secrets.env" in paths
        assert "config/private.pem" in paths

    def test_dedup_same_path_same_pattern(self, tmp_path: Path) -> None:
        """``scan_package`` dedups on ``(path, pattern)`` so an operator
        gets a clean blocklist, not a flood from multiple regex hits."""

        body = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "first hit\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "second hit (same pattern)\n"
        )
        _layout(tmp_path, {"key.txt": body})
        findings = _findings_for(tmp_path)
        # Exactly one finding for the PEM pattern on key.txt — not two.
        pem_hits = [
            f
            for f in findings
            if f.kind == "content" and "PRIVATE KEY" in f.pattern
        ]
        assert len(pem_hits) == 1, (
            f"PEM dedup failed — {len(pem_hits)} hits for same (path, pattern)"
        )

    def test_different_patterns_same_file_both_reported(
        self, tmp_path: Path
    ) -> None:
        """Different patterns matching the same file → separate findings
        (Spec §13.1: 'so the user knows *why* it's blocked')."""

        body = (
            "OPENAI_API_KEY=sk-realkey-1234567890abcdefghij\n"
            "AWS_SECRET_ACCESS_KEY=AKIA1234567890\n"
        )
        _layout(tmp_path, {"scripts/leak.py": body})
        findings = _findings_for(tmp_path)
        patterns = {f.pattern for f in findings if f.path == "scripts/leak.py"}
        assert any(r"\bsk-" in p for p in patterns)
        assert any("AWS_SECRET_ACCESS_KEY" in p for p in patterns)

    def test_clean_package_returns_empty_list(self, tmp_path: Path) -> None:
        _layout(
            tmp_path,
            {
                "SKILL.md": "# Hello\nLegit description without secrets.\n",
                "scripts/run.py": "print('hello world')\n",
                "README.md": "Set your `OPENAI_API_KEY` env var (no value).\n",
            },
        )
        findings = _findings_for(tmp_path)
        assert findings == [], f"clean package flagged: {findings}"

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        # No files seeded — scan should return [] without crashing.
        empty = tmp_path / "empty-pkg"
        empty.mkdir()
        assert _findings_for(empty) == []


# ===========================================================================
# SecretFinding shape
# ===========================================================================


class TestSecretFindingShape:
    def test_finding_uses_posix_paths(self, tmp_path: Path) -> None:
        """Spec §13.1 — paths in findings use POSIX separators so the
        same operator message renders identically on Windows + macOS +
        Linux deployments."""

        _layout(tmp_path, {"deeply/nested/.env": "x"})
        findings = _findings_for(tmp_path)
        assert any(f.path == "deeply/nested/.env" for f in findings)
        # Even if pytest ran on Windows (hypothetical), the finding path
        # must NOT contain backslashes.
        for finding in findings:
            assert "\\" not in finding.path, (
                f"non-POSIX path leaked into finding: {finding.path!r}"
            )

    def test_finding_carries_pattern_for_operator_message(
        self, tmp_path: Path
    ) -> None:
        """Operator-facing error mentions the pattern so the user knows
        why a file was blocked. Pin: ``finding.pattern`` is non-empty
        and matches the canonical regex source."""

        _layout(tmp_path, {".env": "x"})
        findings = _findings_for(tmp_path)
        env_hit = next(f for f in findings if f.path == ".env")
        # Pattern source includes ``\.env`` token.
        assert r"\.env" in env_hit.pattern


# ===========================================================================
# Integration smoke — module is importable from the publish path
# ===========================================================================


class TestSecretScanModuleSurface:
    def test_public_exports_unchanged(self) -> None:
        """Pin the public surface — publish_service / install_service /
        skills.upload all import from this list."""

        from app.marketplace import secret_scan

        assert hasattr(secret_scan, "scan_package")
        assert hasattr(secret_scan, "SECRET_FILE_PATTERNS")
        assert hasattr(secret_scan, "SECRET_CONTENT_PATTERNS")
        assert hasattr(secret_scan, "SecretFinding")

    def test_scan_works_with_relative_extracted_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Some callsites pass a path computed via ``Path.cwd()``;
        verify ``scan_package`` handles a relative directory without
        resolving outside the intended root."""

        monkeypatch.chdir(tmp_path)
        rel_root = Path("pkg")
        rel_root.mkdir()
        (rel_root / ".env").write_text("x")
        findings = scan_package(rel_root)
        assert findings
        # Relative input shouldn't leak the cwd into the result paths.
        assert all(not os.path.isabs(f.path) for f in findings)


# ===========================================================================
# is_suspicious_secret_value — structure + entropy allowlist regression guard
# ===========================================================================


class TestSuspiciousValueStructureGuard:
    """Adversarial-review regression table for the env_vars/headers/args
    allowlist. The policy is structure + entropy based:

    * a value with >= 2 separators (``-_./:@`` or space) reads as a
      structured identifier → allowed (model names, regions, UUIDs,
      reverse-DNS namespaces, npm specs, enum constants);
    * a continuous run (<= 1 separator) of length >= 20 with Shannon
      entropy >= 3.0 bits/char, or a pure-hex digest >= 32 chars, reads as
      opaque secret material → blocked;
    * URLs are re-scanned (query/fragment) and MIME subtypes are
      length-capped so disguised secrets don't auto-pass;
    * known token shapes (``sk-`` etc.) block regardless of structure.
    """

    # FALSE-POSITIVE GUARD — benign structured/short config must pass.
    @pytest.mark.parametrize(
        ("value", "header"),
        [
            ("claude-3-5-sonnet-20241022", None),
            ("prod-cluster-us-east-1a-replica", None),
            ("550e8400-e29b-41d4-a716-446655440000", None),
            ("FEATURE_FLAG_ENABLED_FOR_ALL", None),
            ("com.example.production.service.handler", None),
            ("us-west-2", None),
            ("application/json", None),
            ("application/x-www-form-urlencoded", None),
            ("production", None),
            ("30", None),
            ("https://api.example.com", None),
            ("@modelcontextprotocol/server-filesystem", None),
            ("req-12345", "Idempotency-Key"),
            ("home-v2", "X-Cache-Key"),
            ("abc-1", "X-Request-Key"),
            ("part-7", "X-Partition-Key"),
            ("user-42", "X-Routing-Key"),
            ("={{ $credentials.x }}", None),
            ("", None),
        ],
    )
    def test_benign_values_allowed(self, value: str, header: str | None) -> None:
        assert not is_suspicious_secret_value(value, header_name=header), (
            value,
            header,
        )

    # SECURITY GUARD — opaque/disguised/known secrets must be blocked.
    @pytest.mark.parametrize(
        ("value", "header"),
        [
            ("aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd0Ef2Gh", None),  # 30-char entropy run
            ("deadbeefdeadbeefdeadbeefdeadbeef", None),  # 32 hex digest
            ("Bearer aB3kLm9Qp5Rs7Tv1Wx", "Authorization"),
            ("aB3kLm9Qp5Rs7Tv1Wx4Yz6Cd", "X-Api-Key"),
            ("session=aB3kLm9Qp5Rs7Tv1Wx4", "Cookie"),
            ("https://h/x?sig=aB3kLm9Qp5Rs7Tv1Wx4Yz", None),  # signed URL
            ("https://u:aB3kLm9Qp5Rs7@h", None),  # URL userinfo
            ("application/x-realsecrettoken12345", None),  # opaque MIME subtype
            ("sk-abc1234567890ABCDEFghij", None),  # content token
            ("xoxb-" + "1234567890-ABCDEFGHIJKLMNOP", None),  # Slack token (split)
            ("ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", None),  # GitHub PAT
            ("aB3kLm9Qp5Rs7Tv1Wx4Yz6", None),  # opaque arg value
        ],
    )
    def test_secret_values_flagged(self, value: str, header: str | None) -> None:
        assert is_suspicious_secret_value(value, header_name=header), (value, header)

    def test_degenerate_low_entropy_run_allowed(self) -> None:
        """A ``B*40`` run has entropy 0 — it is *not* real secret material,
        so the entropy-based policy intentionally lets it through. This
        pins the documented residual so a future tweak doesn't silently
        resurrect the length-only false-positive behaviour."""

        assert not is_suspicious_secret_value("B" * 40)
