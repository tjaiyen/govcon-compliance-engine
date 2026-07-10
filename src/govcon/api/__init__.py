"""HTTP surface for the engine (Phase 0 — the guided web workbench).

Thin, read-only wrapper over the *pure* service layer. Every endpoint returns
the service's own determination, including its human-readable ``reasons`` /
``caveats`` / ``source_citation`` — the substance the guided UI turns into
plain-language teaching. Advisory / synthetic-data posture is unchanged: this
adds a way to *see and learn from* the existing logic, not new logic.
"""

from govcon.api.app import create_app

__all__ = ["create_app"]
