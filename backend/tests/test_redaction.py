"""M5 Slice E Stage 4 — Redaction contract (Phase 1 출시 게이트).

Spec §8.5 + §13.2. Targets ``app.marketplace.redaction`` and its two
integration points:

1. ``executor._create_skill_execute_tool`` — wraps the subprocess result
   with ``redact_credential_values(result, injected_env)``.
2. ``streaming.py`` TOOL_CALL_START event — pipes ``tc.args`` through
   ``redact_keys()`` so MCP/general tool auth payloads don't leak.

Test contract:

* ``redact_credential_values``: literal value → ``<redacted:<env>>``,
  ``len < 5`` skipped, longest values replaced first.
* ``redact_keys``: pure structural; sensitive *key names* replaced with
  ``"<redacted>"``; recursive through dict/list/tuple; preserves
  non-sensitive scalars and key names verbatim.
* End-to-end: subprocess stdout leak → tool result already redacted.
* End-to-end: exception detail traveling through ``redact_credential_values``
  cleanly removes the value.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agent_runtime.executor import (
    AgentConfig,
    _create_skill_execute_tool,
)
from app.marketplace.redaction import (
    is_sensitive_key,
    redact_credential_values,
    redact_keys,
)
from app.marketplace.skill_runtime import (
    ResolvedCredential,
    build_skill_runtime_context,
)

# ---------------------------------------------------------------------------
# Pure-function: redact_credential_values
# ---------------------------------------------------------------------------


class TestRedactCredentialValues:
    def test_redact_credential_values_replaces_mapped_var_value(self) -> None:
        """Every occurrence of a mapped value is replaced with
        ``<redacted:<env_name>>`` — operator can still trace which
        credential surfaced in the log."""

        text = "User logged in with srt_id=supersecret-id-12345 OK"
        out = redact_credential_values(text, {"KSKILL_SRT_ID": "supersecret-id-12345"})
        assert "supersecret-id-12345" not in out
        assert "<redacted:KSKILL_SRT_ID>" in out
        # Surrounding text preserved.
        assert "User logged in with srt_id=" in out
        assert "OK" in out

    def test_short_value_not_replaced(self) -> None:
        """``len(value) < 5`` must NOT trigger replacement — otherwise a
        credential equal to ``"id"`` or ``"a"`` would scrub legitimate
        bytes everywhere. progress.txt L51 — false-positive 폭증 가드."""

        text = "id=A and id=A and code=A all are legit literals"
        out = redact_credential_values(text, {"TINY_ENV": "A"})
        # Untouched — short value below threshold.
        assert out == text

        # Length exactly equal to threshold (5) → DOES replace.
        five_char = "abcde"
        text2 = f"value={five_char} should redact"
        out2 = redact_credential_values(text2, {"V5": five_char})
        assert five_char not in out2
        assert "<redacted:V5>" in out2

    def test_length_sorted_replacement(self) -> None:
        """Longer values are replaced first so a shorter value contained
        inside a longer one doesn't consume a fragment.

        Example: value-LONG = "alpha-12345-bravo" contains value-SHORT
        = "alpha-12345". If short replaces first, the long value is
        already partly consumed and ``<redacted:SHORT>-bravo`` leaks
        the ``-bravo`` suffix."""

        long_value = "alpha-12345-bravo-87654"
        short_value = "alpha-12345"
        text = f"left={long_value} right={short_value}"
        out = redact_credential_values(
            text,
            {"LONG": long_value, "SHORT": short_value},
        )
        # Neither raw value remains.
        assert long_value not in out
        assert short_value not in out
        # Both redaction markers appear.
        assert "<redacted:LONG>" in out
        assert "<redacted:SHORT>" in out
        # No stray fragment leaked.
        assert "-bravo-87654" not in out

    def test_empty_inputs_are_noop(self) -> None:
        """Empty text or empty mapping → returns input unchanged."""

        assert redact_credential_values("", {"X": "long-value"}) == ""
        assert redact_credential_values("hello", None) == "hello"
        assert redact_credential_values("hello", {}) == "hello"


# ---------------------------------------------------------------------------
# Pure-function: redact_keys
# ---------------------------------------------------------------------------


class TestRedactKeys:
    def test_redact_keys_recursive_sensitive_pattern(self) -> None:
        """Sensitive key names get their values replaced with
        ``"<redacted>"``. Match is case-insensitive and substring-aware."""

        payload = {
            "username": "kim",
            "password": "P@ssw0rd",
            "config": {
                "API_KEY": "sk-abc-123",
                "child": {"refresh_token": "rt-456", "name": "x"},
            },
            # NOTE: container key "tokens" itself matches "token" pattern
            # → the entire list value would be masked. Use a neutral
            # container name so the inner dicts remain inspectable.
            "entries": [
                {"access_key": "AKIA-...", "id": "n1"},
                {"client_secret": "csec", "label": "ok"},
            ],
            # And here we verify the substring-match-eats-container
            # behaviour explicitly — "tokens" (plural) matches "token".
            "tokens": [{"value": "should-be-replaced-as-whole-list"}],
        }
        out = redact_keys(payload)
        assert out["username"] == "kim"  # non-sensitive preserved
        assert out["password"] == "<redacted>"
        assert out["config"]["API_KEY"] == "<redacted>"
        assert out["config"]["child"]["refresh_token"] == "<redacted>"
        assert out["config"]["child"]["name"] == "x"
        assert out["entries"][0]["access_key"] == "<redacted>"
        assert out["entries"][0]["id"] == "n1"
        assert out["entries"][1]["client_secret"] == "<redacted>"
        assert out["entries"][1]["label"] == "ok"
        # Container substring match — documented behaviour.
        assert out["tokens"] == "<redacted>", (
            "Substring match should mask the entire 'tokens' value — "
            "any narrower behaviour requires a regex tightening + update here"
        )

    def test_redact_keys_preserves_non_sensitive(self) -> None:
        """Common non-sensitive keys (name, email, message, count) must
        pass through unchanged. Pin the boundary so future regex tweaks
        don't accidentally over-broaden the match."""

        payload = {
            "name": "alpha",
            "email": "a@b.com",
            "message": "Hello, world!",
            "count": 42,
            "id": "abc",
            "is_active": True,
        }
        out = redact_keys(payload)
        assert out == payload, "redact_keys mutated non-sensitive keys — false positive in regex"

    def test_redact_keys_handles_nested_lists(self) -> None:
        """Deeply nested lists of dicts walked end-to-end."""

        payload = [
            {"layer": 1, "items": [{"password": "p1", "user": "u1"}]},
            {
                "layer": 2,
                "items": [
                    {"password": "p2", "metadata": {"private_key": "pk2"}},
                    {"plain": "ok"},
                ],
            },
        ]
        out = redact_keys(payload)
        assert out[0]["items"][0]["password"] == "<redacted>"
        assert out[0]["items"][0]["user"] == "u1"
        assert out[1]["items"][0]["password"] == "<redacted>"
        assert out[1]["items"][0]["metadata"]["private_key"] == "<redacted>"
        assert out[1]["items"][1] == {"plain": "ok"}

    def test_redact_keys_tuple_preserved_as_tuple(self) -> None:
        """Tuples are walked but kept as tuples (json.dumps still works
        because json serialises tuples as arrays — but downstream code
        may type-check)."""

        payload: tuple[Any, ...] = ({"token": "t1"}, {"name": "n1"})
        out = redact_keys(payload)
        assert isinstance(out, tuple)
        assert out[0]["token"] == "<redacted>"
        assert out[1]["name"] == "n1"


# ---------------------------------------------------------------------------
# is_sensitive_key alignment
# ---------------------------------------------------------------------------


class TestIsSensitiveKey:
    def test_matches_documented_sensitive_names(self) -> None:
        for name in (
            "password",
            "Password",
            "API_KEY",
            "api-key",
            "ApiKey",
            "secret",
            "client_secret",
            "user_token",
            "access_key",
            "refresh_token",
            "private_key",
        ):
            assert is_sensitive_key(name), f"is_sensitive_key({name!r}) → False; expected True"

    def test_does_not_match_common_non_sensitive_names(self) -> None:
        for name in ("name", "email", "id", "count", "message", "is_active"):
            assert not is_sensitive_key(name), f"is_sensitive_key({name!r}) → True; expected False"


# ---------------------------------------------------------------------------
# Channel integration: subprocess stdout
# ---------------------------------------------------------------------------


def _seed_skill(root: Path, slug: str) -> Path:
    src = root / "_canonical" / slug
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text("# canonical\n")
    return src


def _make_cfg(thread_id: str, skills: list[dict] | None) -> AgentConfig:
    return AgentConfig(
        provider="anthropic",
        model_name="claude-sonnet-4-5",
        api_key=None,
        base_url=None,
        system_prompt="",
        tools_config=[],
        thread_id=thread_id,
        agent_skills=skills,
    )


class TestSubprocessRedaction:
    @pytest.mark.asyncio
    async def test_subprocess_stdout_redacted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skill script that prints the credential value (debug echo,
        traceback, etc.) must come back with the value redacted at the
        tool boundary — no raw secret ever reaches the LLM context.

        Strategy: install a credential binding on the descriptor and
        write a SKILL.md script that prints the env var value.
        """

        monkeypatch.setattr("app.agent_runtime.executor._DATA_DIR", tmp_path)

        slug = "leaker"
        src = _seed_skill(tmp_path, slug)
        (src / "scripts").mkdir(exist_ok=True)
        (src / "scripts" / "echo.py").write_text(
            "import os\nprint('debug:', os.environ.get('KSKILL_SRT_PASSWORD'))\n"
        )

        cfg = _make_cfg(
            thread_id="t-redact",
            skills=[
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "name": slug,
                    "kind": "package",
                    "storage_path": str(src),
                    "description": "",
                }
            ],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        # Inject a credential binding directly — Stage 3 normally does
        # this via resolve_runtime_credentials, but we bypass the DB
        # for a pure-runtime test of the redaction wiring.
        descriptor = ctx.descriptors[slug]
        secret_value = "super-secret-password-9999"
        descriptor.credential_bindings = {
            "srt_account": ResolvedCredential(
                credential_id=uuid.uuid4(),
                definition_key="srt_account",
                env_map={"password": "KSKILL_SRT_PASSWORD"},
                decrypted={"password": secret_value},
            )
        }

        tool = _create_skill_execute_tool(ctx)
        result = await tool.coroutine(
            skill_directory=f"/runtime/t-redact/skills/{slug}/",
            command="python scripts/echo.py",
        )

        assert secret_value not in result, f"raw credential leaked into tool result: {result!r}"
        assert "<redacted:KSKILL_SRT_PASSWORD>" in result, (
            f"redaction marker missing from tool result: {result!r}"
        )

    @pytest.mark.asyncio
    async def test_subprocess_stderr_redacted_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same gate but for stderr — a failing script that includes the
        credential value in its traceback / error message must surface
        only the redaction marker."""

        monkeypatch.setattr("app.agent_runtime.executor._DATA_DIR", tmp_path)

        slug = "raiser"
        src = _seed_skill(tmp_path, slug)
        (src / "scripts").mkdir(exist_ok=True)
        (src / "scripts" / "fail.py").write_text(
            "import os, sys\n"
            "sys.stderr.write('boom: ' + os.environ['KSKILL_SRT_PASSWORD'] "
            "+ '\\n')\n"
            "sys.exit(1)\n"
        )

        cfg = _make_cfg(
            thread_id="t-stderr",
            skills=[
                {
                    "id": str(uuid.uuid4()),
                    "slug": slug,
                    "name": slug,
                    "kind": "package",
                    "storage_path": str(src),
                    "description": "",
                }
            ],
        )
        ctx = build_skill_runtime_context(cfg, data_dir=tmp_path)
        descriptor = ctx.descriptors[slug]
        secret = "another-secret-pw-aaaaaa"
        descriptor.credential_bindings = {
            "srt_account": ResolvedCredential(
                credential_id=uuid.uuid4(),
                definition_key="srt_account",
                env_map={"password": "KSKILL_SRT_PASSWORD"},
                decrypted={"password": secret},
            )
        }

        tool = _create_skill_execute_tool(ctx)
        result = await tool.coroutine(
            skill_directory=f"/runtime/t-stderr/skills/{slug}/",
            command="python scripts/fail.py",
        )
        # STDERR routed into the result on non-zero exit.
        assert "STDERR" in result
        assert secret not in result
        assert "<redacted:KSKILL_SRT_PASSWORD>" in result


# ---------------------------------------------------------------------------
# Channel integration: SSE TOOL_CALL_START.parameters
# ---------------------------------------------------------------------------


class TestStreamingToolResultRedacted:
    """streaming.py:352 — every TOOL_CALL_START event runs args through
    ``redact_keys``. This guards MCP/general tools that may carry auth
    args (e.g. an HTTP tool with ``headers.api_key``).

    Verifies the helper-level contract; the full SSE pipe is exercised
    by ``test_conversations_router.py``."""

    def test_redact_keys_masks_auth_style_tool_arguments(self) -> None:
        tc_args = {
            "url": "https://api.example.com/widgets",
            "method": "GET",
            "headers": {
                "Authorization": "Bearer secret-token-xyz",
                "X-Api-Key": "ak-9876",
                "User-Agent": "agent/1.0",
            },
            "params": {
                "client_secret": "cs-456",
                "limit": 10,
            },
        }
        redacted = redact_keys(tc_args)
        # Sensitive header values are masked structurally.
        # Note: 'Authorization' itself doesn't match the sensitive pattern;
        # the value isn't redacted by redact_keys (that's by-design — the
        # subprocess-level helper handles literal value redaction). What
        # IS guaranteed: `X-Api-Key` and `client_secret` keys get masked.
        assert redacted["headers"]["X-Api-Key"] == "<redacted>"
        assert redacted["headers"]["User-Agent"] == "agent/1.0"
        assert redacted["params"]["client_secret"] == "<redacted>"
        assert redacted["params"]["limit"] == 10
        assert redacted["url"] == tc_args["url"]
        assert redacted["method"] == "GET"

    def test_memory_tool_parameters_redact_content_and_reason_values(self) -> None:
        from app.agent_runtime.streaming import sanitize_tool_call_parameters

        secret = "sk-1234567890abcdef123456"

        redacted = sanitize_tool_call_parameters(
            "save_user_memory",
            {
                "content": f"api_key={secret}",
                "reason": f"User asked to remember token={secret}",
                "scope": "user",
            },
        )

        assert secret not in str(redacted)
        assert redacted["content"] == "<redacted>"
        assert redacted["reason"] == "<redacted>"
        assert redacted["scope"] == "user"

    def test_streaming_module_uses_redact_keys_for_tool_call_start(
        self,
    ) -> None:
        """Pin the integration site — accidental removal of the
        ``redact_keys(tc.get("args", {}))`` wrapper at
        ``streaming.py:352`` would silently disable SSE redaction."""

        import inspect

        from app.agent_runtime import streaming

        src = inspect.getsource(streaming)
        assert "redact_keys(" in src, (
            "streaming.py no longer calls redact_keys — SSE redaction broken"
        )


# ---------------------------------------------------------------------------
# Exception detail redaction
# ---------------------------------------------------------------------------


class TestExceptionDetailRedaction:
    def test_exception_detail_redacted(self) -> None:
        """Exception messages that surface credential values can be run
        through the same redaction helper before they reach the API
        envelope.

        Pure-function contract here — the integration path
        (FastAPI exception handler) calls ``redact_credential_values``
        with the active thread's mapped env when surfacing the message.
        """

        secret = "leakable-token-1234567890"
        try:
            raise RuntimeError(f"failed to call API with token={secret}: 401 unauthorized")
        except RuntimeError as exc:
            redacted = redact_credential_values(str(exc), {"SOME_TOKEN": secret})
        assert secret not in redacted
        assert "<redacted:SOME_TOKEN>" in redacted
        assert "401 unauthorized" in redacted  # surrounding context preserved


# ---------------------------------------------------------------------------
# Smoke — module surface
# ---------------------------------------------------------------------------


class TestRedactionHelpersAvailable:
    def test_redaction_module_imports(self) -> None:
        """xfail removed in stage 4 — module ships."""

        from app.marketplace import redaction as mod

        assert callable(mod.redact_credential_values)
        assert callable(mod.redact_keys)
        assert callable(mod.is_sensitive_key)


# ---------------------------------------------------------------------------
# Local consistency helpers (kept from skeleton — used by tests above)
# ---------------------------------------------------------------------------


def _ensure_event_loop() -> None:
    """Belt-and-braces for sync test calls that touch async helpers."""

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
