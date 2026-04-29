"""Connectivity tester — runs a definition's ``test`` recipe and evaluates rules.

A test result is a JSON-serializable dict so it can be persisted on
``Credential.last_test_result`` and surfaced verbatim in the UI.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.credentials.authenticate import apply_authentication
from app.credentials.domain import CredentialDefinition, TestRequestSpec
from app.credentials.interpolation import InterpolationError, resolve_deep


@dataclass
class TestResult:
    success: bool
    http_status: int | None
    message: str
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evaluate_rules(
    rules: list[dict[str, Any]],
    response: httpx.Response,
) -> tuple[bool, str]:
    """Evaluate the configured acceptance rules against ``response``.

    Returns ``(success, reason)``. An empty rules list defaults to "2xx is OK".
    """

    if not rules:
        if 200 <= response.status_code < 300:
            return True, f"HTTP {response.status_code}"
        return False, f"HTTP {response.status_code}"

    body: Any = None
    try:
        body = response.json()
    except ValueError:
        body = None

    for rule in rules:
        rule_type = rule.get("type")
        if rule_type == "responseCode":
            expected = rule.get("value")
            if isinstance(expected, list):
                if response.status_code not in expected:
                    return False, (
                        f"expected status in {expected}, got {response.status_code}"
                    )
            else:
                if response.status_code != int(expected or 0):
                    return False, (
                        f"expected status {expected}, got {response.status_code}"
                    )
        elif rule_type == "responseSuccessBody":
            key = rule.get("key")
            expected_value = rule.get("value")
            if not isinstance(body, dict):
                return False, "response body is not a JSON object"
            if body.get(key) != expected_value:
                return False, (
                    f"body[{key!r}] != {expected_value!r} (got {body.get(key)!r})"
                )
        else:
            return False, f"unknown rule type: {rule_type!r}"

    return True, f"HTTP {response.status_code}"


class CredentialTester:
    """Execute a credential definition's connectivity test."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def run(
        self,
        definition: CredentialDefinition,
        decrypted_data: dict[str, Any],
    ) -> TestResult:
        spec = definition.test
        if spec is None:
            return TestResult(
                success=False,
                http_status=None,
                message=f"definition '{definition.key}' has no test recipe",
                details={"reason": "no_test_recipe"},
            )

        try:
            request_opts = self._build_request(spec, definition, decrypted_data)
        except InterpolationError as exc:
            return TestResult(
                success=False,
                http_status=None,
                message=f"missing field for test: {exc}",
                details={"reason": "interpolation_error"},
            )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(**request_opts)
        except httpx.HTTPError as exc:
            return TestResult(
                success=False,
                http_status=None,
                message=f"network error: {exc}",
                details={"reason": "network_error", "exception": exc.__class__.__name__},
            )

        success, reason = _evaluate_rules(spec.rules, response)
        details: dict[str, Any] = {
            "url": str(response.request.url),
            "method": response.request.method,
            "status_code": response.status_code,
        }
        body_text = response.text or ""
        if body_text:
            # Truncate to keep audit logs / DB rows small.
            details["response_excerpt"] = body_text[:500]
        return TestResult(
            success=success,
            http_status=response.status_code,
            message=reason,
            details=details,
        )

    def _build_request(
        self,
        spec: TestRequestSpec,
        definition: CredentialDefinition,
        credentials: dict[str, Any],
    ) -> dict[str, Any]:
        base = resolve_deep(spec.request, credentials)
        if not isinstance(base, dict):
            raise ValueError("test.request must resolve to a dict")
        # Apply the definition's authenticate recipe on top of the test request.
        return apply_authentication(definition.authenticate, base, credentials)


__all__ = ["CredentialTester", "TestResult"]
