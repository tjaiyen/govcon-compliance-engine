"""Request/operation-scoped actor identity (enterprise vision Phase 4).

Replaces the process-level SESSION_ID as the audit trail's user_id: every
audited change is attributed to the actor active WHEN it flushed, so two
users of one running server produce distinguishable, hash-chained history.

Honesty boundary (stated in explain_limitations): identity here is
ASSERTED — an HTTP header, an environment variable, the OS username — not
authenticated. Attribution ≠ segregation of duties; SoD additionally needs
a real identity provider in front of the deployment.

Resolution order:
  1. an explicit actor_context(...) / set via the API middleware
  2. GOVCON_USER environment variable   -> "user:<value>"
  3. the OS login name                  -> "cli:<login>"
  4. a per-process fallback             -> "proc:<12 hex>"
contextvars make this correct across threads AND async tasks (each request
carries its own value; Starlette propagates context into sync endpoints).
"""

from __future__ import annotations

import contextlib
import contextvars
import getpass
import os
from uuid import uuid4

#: Last-resort attribution when nothing identifies the caller.
PROCESS_FALLBACK = f"proc:{uuid4().hex[:12]}"

_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "govcon_actor", default=None
)


def current_actor() -> str:
    """The actor to attribute the current operation to. Never raises,
    never returns empty — the audit trail always gets a value."""
    explicit = _actor.get()
    if explicit:
        return explicit
    env = os.environ.get("GOVCON_USER")
    if env:
        return f"user:{env}"
    try:
        login = getpass.getuser()
    except Exception:  # pragma: no cover - no resolvable OS user
        return PROCESS_FALLBACK
    return f"cli:{login}" if login else PROCESS_FALLBACK


@contextlib.contextmanager
def actor_context(actor: str):
    """Attribute everything flushed inside the block to `actor`."""
    if not actor or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    token = _actor.set(actor)
    try:
        yield
    finally:
        _actor.reset(token)


def set_actor(actor: str) -> contextvars.Token:
    """Non-contextmanager form for middleware; pair with reset_actor()."""
    if not actor or not actor.strip():
        raise ValueError("actor must be a non-empty string")
    return _actor.set(actor)


def reset_actor(token: contextvars.Token) -> None:
    _actor.reset(token)
