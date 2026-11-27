from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.security.cipher import CipherKey
from app.services import ag_ui_read_cursor as cursors

_SCOPE = cursors.CursorScope("thread", "공개/실행?", "physical", "1", "branch", "reader")


@pytest.fixture(autouse=True)
def test_keyring(monkeypatch: pytest.MonkeyPatch) -> list[CipherKey]:
    ring = [CipherKey.from_hex("30" * 32)]
    monkeypatch.setattr(cursors, "get_keys", lambda: ring)
    return ring


def committed(scope: cursors.CursorScope = _SCOPE) -> str:
    return cursors.frame_cursor(
        scope, ordinal=2, frame=13, generation="private-generation", source_id="private-source"
    )


def test_opaque_cursor_resolves_exact_source_and_output_frame() -> None:
    token = committed()
    assert "private" not in token
    assert "physical" not in token
    assert "공개" not in token
    assert committed() == token
    position = cursors.parse_cursor(token, _SCOPE)
    assert (position.kind, position.ordinal, position.frame) == ("event", 2, 13)
    assert cursors.matches_source(
        position, source_id="private-source", generation="private-generation"
    )
    assert not cursors.matches_source(
        position, source_id="different", generation="private-generation"
    )
    assert not cursors.matches_source(position, source_id="private-source", generation="compacted")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("thread_id", "new"),
        ("public_run_id", "new"),
        ("physical_run_id", None),
        ("projection_version", "2"),
        ("branch_key", None),
        ("visibility_key", "revoked"),
    ],
)
def test_cross_scope_rejected(name: str, value: str | None) -> None:
    scope = replace(_SCOPE, **{name: value})
    with pytest.raises(cursors.InvalidReadCursor):
        cursors.parse_cursor(committed(), scope)


def test_position_tampering_and_noncanonical_encodings_rejected() -> None:
    token = committed()
    change = "0" if token[-1] != "0" else "1"
    invalid = [
        token[:-1] + change,
        token.replace(".13.", ".14."),
        token.replace(".2.", ".3."),
        token.replace(".2.", ".02."),
        token.replace(".2.", ".-1."),
        token.replace(".2.", "." + "9" * 20 + "."),
        token.replace(".event.", ".terminal."),
        token + "\n",
        "",
    ]
    for item in invalid:
        with pytest.raises(cursors.InvalidReadCursor):
            cursors.parse_cursor(item, _SCOPE)


def test_head_remains_valid_after_first_write_and_queue_transition_has_a_new_scope() -> None:
    token = cursors.frame_cursor(
        _SCOPE, ordinal=0, frame=0, source_id="start", generation=None, kind="head"
    )
    position = cursors.parse_cursor(token, _SCOPE)
    assert cursors.matches_source(position, source_id="start", generation="new-journal")
    with pytest.raises(cursors.InvalidReadCursor):
        cursors.parse_cursor(token, replace(_SCOPE, physical_run_id=None))


def test_terminal_for_unclaimed_queue_cannot_resume_a_different_physical_run() -> None:
    scope = replace(_SCOPE, physical_run_id=None)
    token = cursors.frame_cursor(
        scope, ordinal=1, frame=0, source_id="cancelled", generation=None, kind="terminal"
    )
    position = cursors.parse_cursor(token, scope)
    assert position.kind == "terminal"
    assert cursors.matches_source(position, source_id="cancelled", generation=None)
    with pytest.raises(cursors.InvalidReadCursor):
        cursors.parse_cursor(token, _SCOPE)


def test_key_rotation_accepts_retained_issuer_and_rejects_retired_issuer(
    test_keyring: list[CipherKey],
) -> None:
    old = committed()
    test_keyring.insert(0, CipherKey.from_hex("a7" * 32))
    new = committed()
    assert new != old
    position = cursors.parse_cursor(old, _SCOPE)
    assert cursors.matches_source(
        position, source_id="private-source", generation="private-generation"
    )
    test_keyring.pop()
    assert not cursors.matches_source(
        position, source_id="private-source", generation="private-generation"
    )
    with pytest.raises(cursors.InvalidReadCursor):
        cursors.parse_cursor(old, _SCOPE)
    assert cursors.parse_cursor(new, _SCOPE).frame == 13


def test_swapping_two_historical_issuers_invalidates_the_reader_cursor() -> None:
    from app.agent_runtime.ag_ui_visibility import ChildVisibility
    from app.models.conversation import Conversation
    from app.services.ag_ui_read_target import cursor_scope_for

    room, run = Conversation(id=uuid4()), uuid4()
    calls, scopes = frozenset({"a", "b"}), frozenset({("one",), ("two",)})
    prior = ChildVisibility(
        False, calls, scopes, historical_bindings=frozenset({("a", ("one",)), ("b", ("two",))})
    )
    changed = ChildVisibility(
        False, calls, scopes, historical_bindings=frozenset({("a", ("two",)), ("b", ("one",))})
    )
    original = cursor_scope_for(room, "public", run, "1", None, prior, frozenset(), None, ())
    replacement = cursor_scope_for(room, "public", run, "1", None, changed, frozenset(), None, ())
    cursor = cursors.frame_cursor(original, ordinal=3, frame=0, source_id="digest", generation="row")
    with pytest.raises(cursors.InvalidReadCursor):
        cursors.parse_cursor(cursor, replacement)
