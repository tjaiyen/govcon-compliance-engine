"""Workspace isolation (enterprise vision Phase 4 — the multi-tenancy
decision, stated).

A workspace is a fully separate database: `$GOVCON_HOME/workspaces/<name>.db`,
created by running the real migrations. For an advisory/training tool this is
DELIBERATELY chosen over row-level tenancy (tenant_id + RLS on shared
tables):

  * isolation is physical — no policy bug can leak one workspace into
    another, and every existing trigger/audit-chain guarantee holds per
    workspace unchanged;
  * the schema and all 235 tests stay untouched — no tenant column threaded
    through 15 tables;
  * each learner/team genuinely WANTS a separate synthetic world (a shared
    ledger would corrupt everyone's SF 1408 self-check).

Row-level tenancy on shared Postgres becomes the right trade only at
real-data SaaS scale — which sits behind the excluded Phase 5 liability
line. If that line is ever crossed, docs/POSTGRES.md carries the RLS plan.

Workspace names are strictly validated ([a-z0-9][a-z0-9_-]{0,39}) — the name
becomes a filename, so validation is the path-traversal guard, and it is
tested with hostile input.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
#: Windows reserved device names — a workspace name becomes a filename, and on
#: Windows `nul.db` / `con.db` open the null device or fail opaquely (silent
#: data loss). Rejected case-insensitively even though this is a POSIX-first
#: tool, so a workspace created here is portable.
_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def govcon_home() -> Path:
    return Path(os.environ.get("GOVCON_HOME", str(Path.home() / ".govcon")))


class WorkspaceRegistry:
    """Filesystem registry of workspace databases, migrate-on-create."""

    def __init__(self, root: Path | None = None, project_root: Path | None = None):
        self.root = (root or govcon_home()) / "workspaces"
        #: where alembic.ini lives — migrations run from here.
        self.project_root = project_root or Path(__file__).resolve().parents[2]

    def _validated(self, name: str) -> str:
        if not _NAME_RE.match(name or ""):
            raise ValueError(
                f"invalid workspace name {name!r} — lowercase letters, digits, "
                "hyphen/underscore, max 40 chars, must start alphanumeric"
            )
        if name.lower() in _RESERVED_NAMES:
            raise ValueError(
                f"invalid workspace name {name!r} — reserved device name"
            )
        return name

    def path(self, name: str) -> Path:
        return self.root / f"{self._validated(name)}.db"

    def url(self, name: str) -> str:
        return f"sqlite:///{self.path(name)}"

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.db"))

    def create(self, name: str) -> Path:
        """Create a workspace by running the REAL migrations (never
        create_all — the triggers and seeds are the product)."""
        path = self.path(name)
        if path.exists():
            raise FileExistsError(f"workspace {name!r} already exists at {path}")
        # Case-insensitive collision guard: on a case-insensitive filesystem
        # 'Team'/'team' would clobber one file; reject before creating.
        lower = name.lower()
        if any(existing.lower() == lower for existing in self.list()):
            raise FileExistsError(
                f"workspace {name!r} collides case-insensitively with an existing one"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=self.project_root,
            env={**os.environ, "GOVCON_DB_URL": f"sqlite:///{path}"},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            path.unlink(missing_ok=True)  # no half-migrated husk left behind
            raise RuntimeError(f"workspace migration failed:\n{result.stderr}")
        return path
