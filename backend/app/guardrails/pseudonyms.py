"""A conversation's pseudonyms: ``<<PERSON_1>>`` for a value, and back (plan §3.3.1).

Tokens are tag-style so a stream can be restored by holding at most one token's length, and
numbered per entity within the conversation, so the same person keeps the same pseudonym
across turns and the model can still tell people apart. The map is loaded once per run
from ``guardrail_pseudonyms`` and new tokens are written back with the run's records.
Without a conversation row (API or trigger threads) the map lives for the run only.

A token the map never issued (the model invented ``<<PERSON_9>>``) is left as it is and
counted, never guessed.

Natural style (rule ``pseudonym_style="natural"``, people only) uses a made-up Korean name
instead: same length, and the last syllable has a final consonant (batchim) exactly when the
original's does, so a particle the model attaches (은/는, 이/가) is still right after restore.
A candidate that already appears in the text, the map or the conversation's values is
skipped. Restore swaps the full name, and the given name alone when it stands as a word
(``준호 씨``); a surname alone is too ambiguous to restore.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select

from app.database import tenant_session
from app.guardrails.events import RunScope, value_digest
from app.models.conversation import Conversation
from app.models.guardrail_pseudonym import GuardrailPseudonym
from app.security import cipher, key_provider

logger = logging.getLogger(__name__)

TOKEN = re.compile(r"<<([A-Z][A-Z_]{0,38}[A-Z])_([1-9][0-9]{0,5})>>")
MAX_TOKEN_LENGTH = 52  # "<<" + 40 + "_" + 6 + ">>"


def token(entity: str, number: int) -> str:
    return f"<<{entity}_{number}>>"


_SURNAMES = "김이박최정강조윤장임한오서신권황안송류홍"
# Given names by whether the last syllable has a final consonant; chosen to be rare as
# ordinary words, so a given-name-only restore does not hit unrelated text.
_GIVEN = {
    False: ("준호", "서아", "예주", "지호", "도우", "시아", "태오", "윤서", "서후", "아라"),
    True: ("지훈", "현준", "서진", "민준", "하린", "예린", "도현", "유진", "태윤", "가온"),
}
_HANGUL = re.compile(r"[가-힣]{2,4}")
# What may follow a given name standing as a word: particles (longest first, for reading;
# the lookahead only needs a prefix), honorifics, copulas, space or punctuation. Anything
# else (``준호박``) is another word and is left alone.
_FOLLOWERS = (
    "에게서",
    "한테서",
    "이라고",
    "이라는",
    "이에요",
    "입니다",
    "에게",
    "에서",
    "한테",
    "께서",
    "이랑",
    "하고",
    "처럼",
    "같이",
    "까지",
    "부터",
    "보다",
    "마저",
    "조차",
    "밖에",
    "라고",
    "라는",
    "이고",
    "이며",
    "이나",
    "이든",
    "이야",
    "예요",
    "으로",
    "이요",
    "씨",
    "님",
    "께",
    "랑",
    "로",
    "와",
    "과",
    "의",
    "도",
    "만",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "나",
    "야",
    "아",
    "에",
    "요",
)
_WORD_END = "(?=" + "|".join(_FOLLOWERS) + r"|[\s,.!?)\]'\"’”:;~]|$)"


def has_batchim(syllable: str) -> bool:
    return (ord(syllable) - 0xAC00) % 28 != 0


def natural_candidates(value: str) -> list[str]:
    """Made-up names shaped like ``value`` (2-3 syllables), in a stable order."""
    batchim = has_batchim(value[-1])
    given = _GIVEN[batchim]
    if len(value) == 3:  # noqa: PLR2004
        return [s + g for g in given for s in _SURNAMES]
    if len(value) == 2:  # noqa: PLR2004
        return [s + g[-1] for g in given for s in _SURNAMES]
    return []


@dataclass
class Pseudonyms:
    key: Callable[[], bytes]
    scope: RunScope | None = None
    by_digest: dict[str, str] = field(default_factory=dict)
    by_token: dict[str, str] = field(default_factory=dict)
    entities: dict[str, str] = field(default_factory=dict)  # token -> entity
    counters: Counter[str] = field(default_factory=Counter)
    invented: int = 0
    natural: dict[str, str] = field(default_factory=dict)  # natural pseudonym -> value
    _new: list[tuple[str, str, str, str]] = field(default_factory=list)
    _loaded: bool = False

    async def load(self) -> None:
        """The conversation's map, once per run (decrypting only its own rows)."""
        if self._loaded:
            return
        self._loaded = True
        context = self.scope.org_context if self.scope else None
        conversation_id = self.scope.conversation_id if self.scope else None
        if context is None or conversation_id is None:
            return
        try:
            async with tenant_session(context) as db:
                rows = await db.scalars(
                    select(GuardrailPseudonym).where(
                        GuardrailPseudonym.conversation_id == conversation_id
                    )
                )
                keys = key_provider.get_keys()
                for row in rows:
                    self._remember(
                        row.token,
                        row.entity,
                        row.value_digest,
                        cipher.decrypt(row.value_encrypted, keys),
                    )
        except Exception:  # noqa: BLE001 - a run without its old map still pseudonymizes
            logger.warning("guardrail pseudonym map not loaded", exc_info=True)

    def _remember(self, tok: str, entity: str, digest: str, value: str) -> None:
        self.by_digest[digest] = tok
        self.by_token[tok] = value
        self.entities[tok] = entity
        match = TOKEN.fullmatch(tok)
        if match:
            self.counters[entity] = max(self.counters[entity], int(match.group(2)))
        else:
            self.natural[tok] = value

    def token_for(self, entity: str, value: str, *, style: str = "tag", context: str = "") -> str:
        if value in self.natural:
            # Already a pseudonym: a second pass (summarizer, then the main guard) or the
            # model's own answer in the history must not pseudonymize it again.
            return value
        digest = value_digest(self.key(), entity, value)
        existing = self.by_digest.get(digest)
        if existing is not None:
            return existing
        tok = self._natural(value, context) if style == "natural" and entity == "PERSON" else None
        if tok is None:
            self.counters[entity] += 1
            tok = token(entity, self.counters[entity])
        self._remember(tok, entity, digest, value)
        self._new.append((tok, entity, digest, value))
        return tok

    def _natural(self, value: str, context: str) -> str | None:
        if not _HANGUL.fullmatch(value):
            return None
        taken = set(self.by_token) | set(self.by_token.values()) | {value}
        # Given names in use too: a given-name-only restore must point at one person.
        given = {name[1:] for name in taken if len(name) == 3}  # noqa: PLR2004
        for candidate in natural_candidates(value):
            if candidate in taken or candidate in context:
                continue
            if len(candidate) == 3 and (candidate[1:] in given or candidate[1:] in context):  # noqa: PLR2004
                continue
            return candidate
        return None

    def restore(self, text: str, *, keep: Callable[[str], bool] = lambda _entity: False) -> str:
        """``text`` with every issued token back to its value, except entities ``keep`` keeps."""
        text = self._restore_natural(text, keep)
        if "<<" not in text:
            return text

        def swap(match: re.Match[str]) -> str:
            tok = match.group(0)
            value = self.by_token.get(tok)
            if value is None:
                self.invented += 1
                return tok
            return tok if keep(self.entities[tok]) else value

        return TOKEN.sub(swap, text)

    def _restore_natural(self, text: str, keep: Callable[[str], bool]) -> str:
        if not self.natural:
            return text
        for alias, value in sorted(self.natural.items(), key=lambda kv: -len(kv[0])):
            if keep(self.entities[alias]):
                continue
            text = text.replace(alias, value)
            if len(alias) == 3 and len(value) == 3:  # noqa: PLR2004 - given name alone
                text = re.sub(rf"(?<![가-힣]){alias[1:]}{_WORD_END}", value[1:], text)
        return text

    async def flush(self) -> None:
        """Write the tokens issued since the last flush (best effort)."""
        new, self._new = self._new, []
        context = self.scope.org_context if self.scope else None
        conversation_id = self.scope.conversation_id if self.scope else None
        if not new or context is None or conversation_id is None:
            return
        try:
            async with tenant_session(context) as db:
                if await db.get(Conversation, conversation_id) is None:
                    return  # an API or trigger thread: the map stays in this run
                active = key_provider.get_active_key()
                db.add_all(
                    GuardrailPseudonym(
                        tenant_id=context.tenant_id,
                        conversation_id=conversation_id,
                        token=tok,
                        entity=entity,
                        value_digest=digest,
                        value_encrypted=cipher.encrypt(value, active),
                    )
                    for tok, entity, digest, value in new
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - a lost row only costs consistency in later runs
            logger.warning("guardrail pseudonyms not written (%d)", len(new), exc_info=True)
