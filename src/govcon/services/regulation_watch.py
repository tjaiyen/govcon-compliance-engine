"""Federal Register regulation watcher (enterprise vision Phase 3).

Scans the Federal Register's public JSON API for documents that might affect
what the engine is watching (its non-final thresholds and non-final decision
rules) and records them as RegulatorySuggestion rows for a HUMAN to review.

Hard boundaries (load-bearing, tested):
  * SUGGEST-ONLY: this module has no code path that writes to
    regulatory_thresholds or the decision tables. Acting on a suggestion =
    a person verifies the primary source and lands a migration.
  * Fetched text is untrusted DATA (title/abstract stored inert, rendered
    escaped) — it can never change engine behavior.
  * Degrade gracefully: no network / API error → the scan reports the
    source unavailable and moves on; it never crashes and never blocks.
  * No silent caps: unmapped watch targets, truncated result pages, and
    unavailable sources are all REPORTED in the scan result.

The fetcher is injectable so tests are hermetic (no live network in CI);
the default fetcher uses stdlib urllib against
https://www.federalregister.gov/api/v1/documents.json (keyless, read-only).
"""

from __future__ import annotations

import datetime
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from govcon.models import (
    DecisionRule,
    DecisionTable,
    RegulatorySuggestion,
    RegulatoryThreshold,
)
from govcon.models.enums import SuggestionStatus, ThresholdStatus

#: Search terms per watched threshold rule_name. Unmapped non-final rules are
#: reported as skipped — never silently ignored.
WATCH_TERMS: dict[str, str] = {
    "TINA_THRESHOLD": "truthful cost or pricing data",
    "CAS_CONTRACT_TRIGGER": "cost accounting standards",
    "CAS_FULL_COVERAGE": "cost accounting standards",
    "CAS_407_STATUS": "cost accounting standards",
    "CAS_408_STATUS": "cost accounting standards",
    "CAS_411_STATUS": "cost accounting standards",
    "SAT": "simplified acquisition threshold",
    "EXEC_COMP_CAP": "executive compensation benchmark contractor",
    "CDA_CLAIM_CERT": "contract disputes act",
}

#: Search terms per non-final decision rule, keyed by rule_key.
DECISION_RULE_TERMS: dict[str, str] = {
    "full_coverage_cumulative": "cost accounting standards",
}

_FR_API = "https://www.federalregister.gov/api/v1/documents.json"
_PER_PAGE = 20
_EXCERPT_CHARS = 600


def default_fetcher(term: str, since: datetime.date) -> dict:
    """One page of Federal Register documents matching term, published on or
    after `since`. Returns the parsed JSON body (keys: count, results).
    Raises on network/HTTP/parse errors — scan() catches and reports."""
    params = [
        ("conditions[term]", f'"{term}"'),
        ("conditions[publication_date][gte]", since.isoformat()),
        ("per_page", str(_PER_PAGE)),
        ("order", "newest"),
    ]
    for f in ("document_number", "title", "publication_date", "effective_on",
              "html_url", "abstract", "type"):
        params.append(("fields[]", f))
    url = _FR_API + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - fixed https host
        return json.load(resp)


@dataclass
class ScanResult:
    as_of: datetime.date
    since: datetime.date
    targets: list[dict] = field(default_factory=list)  # {watch_rule, term}
    new_suggestions: list[int] = field(default_factory=list)  # suggestion_ids
    already_known: int = 0
    skipped_unmapped: list[str] = field(default_factory=list)
    unavailable: list[dict] = field(default_factory=list)  # {watch_rule, error}
    truncated: list[dict] = field(default_factory=list)  # {watch_rule, total, recorded}


def watch_targets(session: Session) -> tuple[list[dict], list[str]]:
    """(targets, unmapped): every non-final, non-superseded threshold rule
    and non-final decision rule, mapped to its search term. Deduped by
    (watch_rule) — several rows of one rule watch once."""
    targets: dict[str, str] = {}
    unmapped: list[str] = []
    rule_names = set(
        session.execute(
            sa.select(RegulatoryThreshold.rule_name)
            .where(RegulatoryThreshold.status != ThresholdStatus.FINAL_RULE)
            .where(RegulatoryThreshold.superseded_date.is_(None))
        ).scalars()
    )
    for name in sorted(rule_names):
        if name in WATCH_TERMS:
            targets[name] = WATCH_TERMS[name]
        else:
            unmapped.append(name)
    rows = session.execute(
        sa.select(DecisionRule.rule_key, DecisionTable.table_name)
        .join(DecisionTable,
              DecisionRule.decision_table_id == DecisionTable.decision_table_id)
        .where(DecisionRule.status.is_not(None))
        .where(DecisionRule.status != ThresholdStatus.FINAL_RULE)
        .where(DecisionTable.superseded_date.is_(None))
    ).all()
    for rule_key, table_name in sorted(rows):
        key = f"decision:{table_name}.{rule_key}"
        if rule_key in DECISION_RULE_TERMS:
            targets[key] = DECISION_RULE_TERMS[rule_key]
        else:
            unmapped.append(key)
    return (
        [{"watch_rule": k, "term": v} for k, v in targets.items()],
        unmapped,
    )


def _strong(term: str, doc: dict) -> bool:
    hay = ((doc.get("title") or "") + " " + (doc.get("abstract") or "")).lower()
    return term.lower() in hay


def _date(s: str | None) -> datetime.date | None:
    return None if not s else datetime.date.fromisoformat(s)


def scan(
    session: Session,
    *,
    since: datetime.date | None = None,
    as_of: datetime.date | None = None,
    fetcher=None,
    now: datetime.datetime | None = None,
) -> ScanResult:
    """Fetch and record suggestions for every watch target. INSERT-only into
    regulatory_suggestions; touches nothing else."""
    as_of = as_of or datetime.date.today()
    since = since or (as_of - datetime.timedelta(days=90))
    fetcher = fetcher or default_fetcher
    now = now or datetime.datetime.now(datetime.UTC).replace(tzinfo=None)

    targets, unmapped = watch_targets(session)
    result = ScanResult(as_of=as_of, since=since, targets=targets,
                        skipped_unmapped=unmapped)

    known = {
        (r.source, r.document_number, r.watch_rule)
        for r in session.execute(sa.select(RegulatorySuggestion)).scalars()
    }
    for target in targets:
        watch_rule, term = target["watch_rule"], target["term"]
        try:
            body = fetcher(term, since)
        except Exception as exc:  # degrade gracefully, report loudly
            result.unavailable.append({"watch_rule": watch_rule, "error": str(exc)})
            continue
        docs = body.get("results") or []
        total = body.get("count", len(docs))
        if total > len(docs):
            result.truncated.append(
                {"watch_rule": watch_rule, "total": total, "recorded": len(docs)}
            )
        for doc in docs:
            key = ("federal_register", doc["document_number"], watch_rule)
            if key in known:
                result.already_known += 1
                continue
            known.add(key)
            excerpt = (doc.get("abstract") or "")[:_EXCERPT_CHARS] or None
            row = RegulatorySuggestion(
                watch_rule=watch_rule,
                source="federal_register",
                document_number=doc["document_number"],
                doc_type=doc.get("type"),
                title=doc.get("title") or "(untitled)",
                publication_date=_date(doc.get("publication_date")),
                effective_on=_date(doc.get("effective_on")),
                url=doc.get("html_url"),
                excerpt=excerpt,
                strong_match=_strong(term, doc),
                fetched_at=now,
                status=SuggestionStatus.NEW,
            )
            session.add(row)
            session.flush()
            result.new_suggestions.append(row.suggestion_id)
    return result


def review_suggestion(
    session: Session,
    suggestion_id: int,
    *,
    status: SuggestionStatus,
    note: str | None = None,
    now: datetime.datetime | None = None,
) -> RegulatorySuggestion:
    """The human half of the loop: mark a suggestion reviewed or dismissed.
    A suggestion never transitions back to new, and this function cannot
    change anything except the suggestion row itself."""
    if status == SuggestionStatus.NEW:
        raise ValueError("a suggestion cannot be moved back to 'new'")
    row = session.get(RegulatorySuggestion, suggestion_id)
    if row is None:
        raise LookupError(f"no suggestion {suggestion_id}")
    row.status = status
    row.review_note = note
    row.reviewed_at = now or datetime.datetime.now(
        datetime.UTC
    ).replace(tzinfo=None)
    session.flush()
    return row
