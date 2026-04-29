"""Skills domain — text and package skill management.

Public surface:
- :mod:`app.skills.service` — DB-backed CRUD with disk persistence.
- :mod:`app.skills.packager` — safe ``.skill`` zip extraction.
- :mod:`app.skills.inspector` — SKILL.md frontmatter parsing + safe file IO.
- :mod:`app.skills.runtime` — translates skill links into deep-agents skill spec.
"""

from app.skills import inspector, packager, runtime, service

__all__ = ["inspector", "packager", "runtime", "service"]
