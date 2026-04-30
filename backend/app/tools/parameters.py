"""Re-export the credential ``FieldDef`` so tool definitions share one schema.

Both credentials and tools render dynamic forms; sharing the dataclass keeps
the front-end renderer single-implementation. Importing from here makes the
intent explicit at the call site (``from app.tools.parameters import FieldDef``).
"""

from app.credentials.field import FieldDef, FieldKind

__all__ = ["FieldDef", "FieldKind"]
