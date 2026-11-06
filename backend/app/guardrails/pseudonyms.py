"""A conversation's pseudonyms: ``<<PERSON_1>>`` for a value, and back (plan §3.3.1).

Tokens are tag-style so a stream can be restored by holding at most one token's length, and
numbered per entity within the conversation, so the same person keeps the same pseudonym
across turns and the model can still tell people apart. The map is loaded once per run
from ``guardrail_pseudonyms`` and new tokens are written back with the run's records.
Without a conversation row (API or trigger threads) the map lives for the run only.

A token the map never issued (the model invented ``<<PERSON_9>>``) is left as it is and
counted, never guessed.
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


@dataclass
class Pseudonyms:
    key: Callable[[], bytes]
    scope: RunScope | None = None
    by_digest: dict[str, str] = field(default_factory=dict)
    by_token: dict[str, str] = field(default_factory=dict)
    entities: dict[str, str] = field(default_factory=dict)  # token -> entity
    counters: Counter[str] = field(default_factory=Counter)
    invented: int = 0
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

    def token_for(self, entity: str, value: str) -> str:
        digest = value_digest(self.key(), entity, value)
        existing = self.by_digest.get(digest)
        if existing is not None:
            return existing
        self.counters[entity] += 1
        tok = token(entity, self.counters[entity])
        self._remember(tok, entity, digest, value)
        self._new.append((tok, entity, digest, value))
        return tok

    def restore(self, text: str, *, keep: Callable[[str], bool] = lambda _entity: False) -> str:
        """``text`` with every issued token back to its value, except entities ``keep`` keeps."""
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
