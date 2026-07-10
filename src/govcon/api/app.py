"""FastAPI application factory + endpoints (Phase 0).

The endpoints construct *transient* (unsaved) model objects from the request
and hand them to the pure service functions — the determinations are read-only
(they look up seeded regulatory thresholds and compute), so nothing is
persisted. This keeps the write path (append-only ledger, audit chain)
completely untouched by the browsing/what-if UI.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from govcon.core.identity import current_actor, reset_actor, set_actor
from govcon.db.engine import make_engine, make_session_factory
from govcon.models import Contract, ContractAction
from govcon.models.enums import (
    AgencyType,
    CASCoverageType,
    ContractActionType,
    ContractorSize,
)
from govcon.services.cas_tina import (
    determine_cas_coverage,
    determine_tina_applicability,
)
from govcon.services.reverification import reverification_items
from govcon.services.sf1408 import explain_limitations, has_data, run_self_check
from govcon.services.thresholds import threshold_in_force

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


# --------------------------------------------------------------- request models
class CASRequest(BaseModel):
    award_date: datetime.date
    contract_value: str = Field(..., description="Decimal string, e.g. '12000000.00'")
    contractor_size: ContractorSize = ContractorSize.OTHER_THAN_SMALL
    is_nontraditional_dc: bool = False
    agency_type: AgencyType = AgencyType.DOD


class TINARequest(BaseModel):
    action_date: datetime.date
    proposed_value: str = Field(..., description="Decimal string")
    # Tri-state, like the DB columns: None = not yet evaluated (the honest
    # default — omitting a field must NOT silently assert "evaluated False";
    # a Phase 2 education test caught the old bool=False default doing
    # exactly that, which made the pending path unreachable via the API).
    tina_exception_adequate_price_competition: bool | None = None
    tina_exception_commercial_product_service: bool | None = None
    tina_exception_prices_set_by_law: bool | None = None
    tina_exception_waiver_granted: bool | None = None


def _money(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError) as exc:  # pragma: no cover - guard
        raise ValueError(f"not a valid dollar amount: {raw!r}") from exc


def create_app(session_factory=None, workspace_registry=None) -> FastAPI:
    """Build the app. ``session_factory`` is injectable for tests; otherwise it
    is derived from ``GOVCON_DB_URL`` (via make_engine).

    ``workspace_registry`` (Phase 4) enables per-request workspace routing:
    an ``X-Govcon-Workspace`` header selects an isolated workspace database
    (see govcon.workspaces — physical isolation, deliberately not RLS).
    Without a registry, or without the header, the default factory serves —
    fully backward compatible."""
    factory = session_factory or make_session_factory(make_engine())
    app = FastAPI(
        title="GovCon Compliance Workbench",
        description=(
            "Advisory decision-support & training tool on SYNTHETIC data. "
            "Not a certified accounting system; see /api/about."
        ),
        version="0.1.0",
    )
    workspace_factories: dict[str, object] = {}

    @app.middleware("http")
    async def _attribute_request(request: Request, call_next):
        # Asserted identity (stated limitation): a header names the actor;
        # authentication is a deployment concern in front of this app.
        user = request.headers.get("x-govcon-user")
        token = set_actor(f"web:{user}" if user and user.strip() else "web:anonymous")
        try:
            return await call_next(request)
        finally:
            reset_actor(token)

    def _factory_for(request: Request):
        name = request.headers.get("x-govcon-workspace")
        if not name or workspace_registry is None:
            return factory
        if name not in workspace_factories:
            try:
                if not workspace_registry.exists(name):
                    raise HTTPException(
                        status_code=404,
                        detail=f"no workspace {name!r} — create it with "
                        "`govcon workspace create`",
                    )
            except ValueError as exc:  # hostile/invalid name — never a path
                raise HTTPException(status_code=422, detail=str(exc)) from None
            workspace_factories[name] = make_session_factory(
                make_engine(workspace_registry.url(name))
            )
        return workspace_factories[name]

    def get_session(request: Request) -> Iterator[Session]:
        with _factory_for(request)() as session:
            yield session

    @app.get("/api/whoami")
    def whoami(request: Request) -> dict:
        """Who the engine will attribute this request's actions to, and
        which isolated workspace it is routed at."""
        return {
            "actor": current_actor(),
            "workspace": (
                request.headers.get("x-govcon-workspace") or "default"
                if workspace_registry is not None
                else "default"
            ),
            "workspaces": (
                workspace_registry.list() if workspace_registry is not None else None
            ),
        }

    # ---------------------------------------------------------------- the UI
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_WEB_DIR / "index.html").read_text(encoding="utf-8")

    # ------------------------------------------------------------ determinations
    @app.post("/api/cas")
    def cas(req: CASRequest, session: Session = Depends(get_session)) -> dict:
        contract = Contract(
            award_date=req.award_date,
            contract_value=_money(req.contract_value),
            contractor_size=req.contractor_size,
            is_nontraditional_dc=req.is_nontraditional_dc,
            agency_type=req.agency_type,
            cas_coverage_type=CASCoverageType.NONE,
        )
        try:
            d = determine_cas_coverage(session, contract)
        except LookupError as exc:
            return {"available": False, "message": str(exc)}
        return {
            "available": True,
            "tier": d.tier,
            "requires_review": d.requires_review,
            "disclosure_required": d.disclosure_required,
            "reasons": d.reasons,
            "caveats": d.caveats,
            "provenance": d.provenance,
        }

    @app.post("/api/tina")
    def tina(req: TINARequest, session: Session = Depends(get_session)) -> dict:
        action = ContractAction(
            action_type=ContractActionType.OTHER_NEGOTIATED_ACTION,
            action_date=req.action_date,
            proposed_value=_money(req.proposed_value),
            tina_exception_adequate_price_competition=(
                req.tina_exception_adequate_price_competition
            ),
            tina_exception_commercial_product_service=(
                req.tina_exception_commercial_product_service
            ),
            tina_exception_prices_set_by_law=req.tina_exception_prices_set_by_law,
            tina_exception_waiver_granted=req.tina_exception_waiver_granted,
        )
        try:
            d = determine_tina_applicability(session, action)
        except LookupError as exc:
            return {"available": False, "message": str(exc)}
        return {
            "available": True,
            "threshold_value": str(d.threshold_value),
            "above_threshold": d.above_threshold,
            "certification_required": d.certification_required,
            "exception_applied": d.exception_applied,
            "unevaluated_exceptions": d.unevaluated_exceptions,
            "reasons": d.reasons,
            "caveats": d.caveats,
            "provenance": d.provenance,
        }

    @app.get("/api/threshold")
    def threshold(
        rule: str, on: datetime.date, session: Session = Depends(get_session)
    ) -> dict:
        try:
            row = threshold_in_force(session, rule, on)
        except LookupError as exc:
            return {"in_force": False, "message": str(exc)}
        return {
            "in_force": True,
            "rule_name": row.rule_name,
            "value": None if row.value is None else str(row.value),
            "effective_date": (
                None if row.effective_date is None else row.effective_date.isoformat()
            ),
            "status": row.status.value,
            "source_citation": row.source_citation,
        }

    @app.get("/api/sf1408")
    def sf1408(session: Session = Depends(get_session)) -> dict:
        results = run_self_check(session)
        return {
            "has_data": has_data(session),
            "criteria": [
                {
                    "criterion": r.criterion,
                    "name": r.name,
                    "passed": r.passed,
                    "findings": r.findings,
                }
                for r in results
            ],
        }

    @app.get("/api/reverify")
    def reverify(session: Session = Depends(get_session)) -> dict:
        as_of = datetime.date.today()
        items = reverification_items(session, as_of)
        return {
            "as_of": as_of.isoformat(),
            "items": [
                {"kind": i.kind, "due": i.due, "description": i.description}
                for i in items
            ],
        }

    @app.get("/api/suggestions")
    def suggestions(session: Session = Depends(get_session)) -> dict:
        """Regulation-watch inbox, read-only. Scanning (network + writes) and
        reviewing stay CLI-only on purpose — the workbench never mutates."""
        import sqlalchemy as sa

        from govcon.models import RegulatorySuggestion

        rows = (
            session.execute(
                sa.select(RegulatorySuggestion).order_by(
                    RegulatorySuggestion.strong_match.desc(),
                    RegulatorySuggestion.publication_date.desc(),
                )
            )
            .scalars()
            .all()
        )
        return {
            "suggestions": [
                {
                    "suggestion_id": r.suggestion_id,
                    "watch_rule": r.watch_rule,
                    "document_number": r.document_number,
                    "doc_type": r.doc_type,
                    "title": r.title,
                    "publication_date": (
                        None if r.publication_date is None
                        else r.publication_date.isoformat()
                    ),
                    "effective_on": (
                        None if r.effective_on is None else r.effective_on.isoformat()
                    ),
                    "url": r.url,
                    "strong_match": r.strong_match,
                    "status": r.status.value,
                }
                for r in rows
            ]
        }

    # --------------------------------------------------------------- education
    @app.get("/api/glossary")
    def glossary() -> dict:
        from govcon.education import GLOSSARY

        return {"terms": GLOSSARY}

    @app.get("/api/scenarios")
    def scenarios() -> dict:
        from govcon.education import SCENARIOS

        return {"scenarios": SCENARIOS}

    @app.get("/api/about", response_class=PlainTextResponse)
    def about() -> str:
        return explain_limitations()

    return app
