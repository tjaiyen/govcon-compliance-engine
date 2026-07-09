"""Markdown exporter (Phase 10; export-format decision 2026-07-08).

A pure rendering layer over the schedules' canonical JSON — the data was
the deliverable (Phase 5); this makes it readable. Every rendered document
LEADS with the SYNTHETIC banner (handoff §4: on ALL generated reports).
Excel is the committed next exporter; this module is where it would live.
"""

from __future__ import annotations

import json

from govcon.models import ICESchedule
from govcon.services.ice_schedules import BANNER


def _render_value(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _render_table(rows: list[dict]) -> str:
    """A list of homogeneous dicts renders as a markdown table."""
    if not rows:
        return "_(no rows)_\n"
    headers = list(rows[0].keys())
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_render_value(row.get(h)) for h in headers) + " |")
    return "\n".join(out) + "\n"


def _render_section(key: str, value) -> str:
    title = key.replace("_", " ")
    if isinstance(value, list) and value and all(isinstance(r, dict) for r in value):
        return f"## {title}\n\n{_render_table(value)}"
    if isinstance(value, dict):
        lines = [f"- **{k.replace('_', ' ')}**: {_render_value(v)}" for k, v in value.items()]
        return f"## {title}\n\n" + "\n".join(lines) + "\n"
    return f"## {title}\n\n{_render_value(value)}\n"


def render_markdown(title: str, content: dict) -> str:
    """Render any of this engine's canonical-JSON documents to markdown.
    The banner is extracted from content and forced to the top — a rendered
    report without it never leaves this function."""
    body = dict(content)
    banner = body.pop("banner", BANNER)
    sections = [f"# {title}", "", f"**{banner}**", ""]
    for key, value in body.items():
        if key in ("fiscal_year", "schedule"):
            continue  # already in the title
        sections.append(_render_section(key, value))
    return "\n".join(sections)


def render_schedule(schedule: ICESchedule) -> str:
    content = json.loads(schedule.content)
    title = (
        f"ICE Schedule {schedule.schedule_type.value} — FY{schedule.fiscal_year} "
        f"({schedule.reconciliation_status.value}, generated {schedule.generated_date.isoformat()})"
    )
    return render_markdown(title, content)
