r"""Collect 30 matched pairs from an explicitly authorized official OpenAI Chat Completions project.

From backend, set MOLDY_CACHE_TRIAL_OPENAI_API_KEY to the dedicated test project's key and
include its Moldy organization UUID in OPENAI_PROMPT_CACHE_ORG_IDS. Use the organization's
normal shared ENCRYPTION_KEYS. Rotating the active key changes cache routes and warms them again.

    uv run python -m scripts.collect_prompt_cache trial.jsonl --org-id TEST_ORG_UUID \
        --model-snapshot DATED_MODEL --input-usd-per-million PRICE \
        --cached-input-usd-per-million PRICE --execute-paid-trial

This sends 60 streamed requests, using synthetic text only. Each request allows at most 32
completion tokens. The final report estimates input charges using your supplied rates. Output
tokens are billed separately. Both arms can hit the provider's automatic cache and share warmed
prefixes. Reconcile provider input charges with scripts.compare_prompt_cache.
Each successful observation is flushed; an interrupted file contains only that trial's results.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import httpx
import openai
from langsmith import tracing_context
from pydantic import SecretStr

from app.agent_runtime.openai_prompt_cache import (
    ConversationCacheChatOpenAI,
    conversation_prompt_cache,
    prompt_cache_options,
)
from app.authz.org_context import OrgContext
from app.config import settings
from app.security.key_provider import get_active_key
from scripts.compare_prompt_cache import Arm, Observation, compare

_API = "https://api.openai.com/v1"
_REQUEST_URL = httpx.URL(_API + "/chat/completions")
_PAIRS = 30


async def collect(
    model: ConversationCacheChatOpenAI,
    org: OrgContext,
    *,
    source: Literal["provider_usage", "fixture"],
) -> AsyncGenerator[Observation, None]:
    """Use a dedicated model/client so other traffic cannot enter the measured sequence."""
    if org.org_id not in settings.openai_prompt_cache_org_ids:
        raise ValueError("test organization must opt in to official OpenAI prompt routing")
    get_active_key()  # Validate shared key material before either arm can spend money.
    client = model.http_async_client
    if not isinstance(client, httpx.AsyncClient):
        raise ValueError("a dedicated async HTTP client is required")
    chat = uuid4()
    # A fresh synthetic prefix avoids reusing another trial's warmed prefix.
    scope = uuid4().hex
    prefix = f"Synthetic measurement set {scope}.\n" + "Red blue green yellow.\n" * 300
    expected_arm: Arm = "key_omitted"
    digest: str | None
    request_id: str | None
    seen_ids: set[str] = set()
    pair_digest: str | None

    async def on_request(req: httpx.Request) -> None:
        nonlocal digest
        if req.method != "POST" or req.url != _REQUEST_URL or digest is not None:
            raise ValueError("expected one official Chat Completions request per observation")
        body = json.loads(req.content)
        if ("prompt_cache_key" in body) != (expected_arm == "key_supplied"):
            raise ValueError("outgoing routing key disagrees with the experiment arm")
        if not body.get("stream") or body.get("model") != model.model_name:
            raise ValueError("unexpected outgoing model or streaming mode")
        body.pop("prompt_cache_key", None)
        comparable = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(comparable.encode()).hexdigest()
        if pair_digest is not None and pair_digest != digest:
            raise ValueError("paired outgoing requests must have the same model and input")

    async def on_response(res: httpx.Response) -> None:
        nonlocal request_id
        request_id = res.headers.get("x-request-id")

    client.event_hooks["request"].append(on_request)
    client.event_hooks["response"].append(on_response)
    try:
        for sequence in range(1, _PAIRS + 1):
            pair_digest = None
            prompt = f"{prefix}\nTrial item {sequence}. Answer with the word blue."
            arms: tuple[Arm, Arm] = ("key_omitted", "key_supplied")
            for expected_arm in arms if sequence % 2 else reversed(arms):
                digest, request_id = None, None
                first_text = None
                usage = None
                snapshots: set[str] = set()
                path = "chat" if expected_arm == "key_supplied" else "control"
                with conversation_prompt_cache(org, chat, run_path=path):
                    started = time.monotonic_ns()
                    async for chunk in model.astream(prompt):
                        if first_text is None and chunk.text.strip():
                            first_text = time.monotonic_ns()
                        if chunk.usage_metadata is not None:
                            usage = chunk.usage_metadata
                        snapshot = chunk.response_metadata.get("model_name")
                        if isinstance(snapshot, str):
                            snapshots.add(snapshot)
                if not request_id or request_id in seen_ids:
                    raise ValueError("missing or duplicate provider x-request-id")
                seen_ids.add(request_id)
                if not digest or first_text is None or not usage or usage["input_tokens"] <= 0:
                    raise ValueError(
                        "incomplete request hash, visible token timing or provider usage"
                    )
                cached = usage.get("input_token_details", {}).get("cache_read")
                if cached is None or snapshots != {model.model_name}:
                    raise ValueError(
                        "response lacks cached-token usage or the expected model snapshot"
                    )
                pair_digest = digest
                yield Observation(
                    arm=expected_arm,
                    sequence=sequence,
                    scope=scope,
                    model=model.model_name,
                    request_sha256=digest,
                    request_id=request_id,
                    source=source,
                    input_tokens=usage["input_tokens"],
                    cached_input_tokens=cached,
                    ttft_ms=Decimal(first_text - started) / 1_000_000,
                )
    finally:
        client.event_hooks["request"].remove(on_request)
        client.event_hooks["response"].remove(on_response)


async def _run(args: argparse.Namespace, key: str) -> None:
    org = OrgContext(args.org_id, uuid4(), uuid4())
    get_active_key()
    # Exclusive clients; no implicit endpoint, proxies, redirects, retries or project traces.
    with httpx.Client(trust_env=False) as sync_client, tracing_context(enabled=False):
        async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
            model = ConversationCacheChatOpenAI(
                model=args.model_snapshot,
                api_key=SecretStr(key),
                base_url=_API,
                http_client=sync_client,
                http_async_client=client,
                max_retries=0,
                max_completion_tokens=32,
                stream_usage=True,
                use_responses_api=False,
                **prompt_cache_options("openai", _API, None),
            )
            observations = []
            with args.output.open("x", encoding="utf-8") as output:
                print(
                    f"Sending 60 paid synthetic requests for {args.model_snapshot}, maximum 32 "
                    "output tokens each. Reported USD estimates cover input only.",
                    file=sys.stderr,
                )
                async with aclosing(collect(model, org, source="provider_usage")) as samples:
                    async for sample in samples:
                        output.write(sample.model_dump_json() + "\n")
                        output.flush()
                        observations.append(sample)
    result = compare(observations, args.input_usd_per_million, args.cached_input_usd_per_million)
    print(json.dumps(result, indent=2, default=str))


def _price(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("expected USD per million input tokens") from exc
    if not rate.is_finite() or rate < 0:
        raise argparse.ArgumentTypeError("expected a finite nonnegative input price")
    return rate


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "output", type=Path, help="new path for observations; existing files are preserved"
    )
    parser.add_argument("--org-id", type=UUID, required=True)
    parser.add_argument("--model-snapshot", required=True, help="dated Chat Completions snapshot")
    parser.add_argument("--input-usd-per-million", type=_price, required=True)
    parser.add_argument("--cached-input-usd-per-million", type=_price, required=True)
    parser.add_argument(
        "--execute-paid-trial",
        action="store_true",
        help="send exactly 30 pairs to the official API",
    )
    args = parser.parse_args()
    if not args.execute_paid_trial:
        parser.error("paid requests require --execute-paid-trial")
    if args.org_id not in settings.openai_prompt_cache_org_ids:
        parser.error("org is absent from OPENAI_PROMPT_CACHE_ORG_IDS")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+-\d{4}-\d{2}-\d{2}", args.model_snapshot):
        parser.error("supply the exact dated model snapshot")
    key = os.environ.get("MOLDY_CACHE_TRIAL_OPENAI_API_KEY")
    if not key:
        parser.error("set MOLDY_CACHE_TRIAL_OPENAI_API_KEY for the dedicated test project")
    try:
        asyncio.run(_run(args, key))
    except openai.APIError as exc:
        parser.exit(
            1, f"provider request failed ({type(exc).__name__}); collected rows are retained\n"
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"collection stopped: {exc}\n")


if __name__ == "__main__":
    main()
