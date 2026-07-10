"""FastAPI application factory + endpoints (Phase 0).

The endpoints construct *transient* (unsaved) model objects from the request
and hand them to the pure service functions — the determinations are read-only
(they look up seeded regulatory thresholds and compute), so nothing is
persisted. This keeps the write path (append-only ledger, audit chain)
completely untouched by the browsing/what-if UI.
"""

from __future__ import annotations

import datetime
import os
import threading
from collections import OrderedDict
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from govcon.core.identity import (
    current_actor,
    reset_actor,
    sanitize_actor_label,
    set_actor,
)
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


#: Reject NaN/Infinity/1e400 as dollar amounts (they break comparisons and can
#: raise from Decimal.quantize downstream) — shared bound with the AI registry.
_MAX_MONEY = Decimal("1e15")


def _money(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:  # pragma: no cover - guard
        raise ValueError(f"not a valid dollar amount: {raw!r}") from exc
    if not value.is_finite() or abs(value) > _MAX_MONEY:
        raise ValueError(f"dollar amount out of range: {raw!r}")
    return value


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


def create_app(session_factory=None, workspace_registry=None, llm_client=None) -> FastAPI:
    """Build the app. ``session_factory`` is injectable for tests; otherwise it
    is derived from ``GOVCON_DB_URL`` (via make_engine).

    ``workspace_registry`` (Phase 4) enables per-request workspace routing:
    an ``X-Govcon-Workspace`` header selects an isolated workspace database
    (see govcon.workspaces — physical isolation, deliberately not RLS).
    Without a registry, or without the header, the default factory serves —
    fully backward compatible.

    ``llm_client`` (AI layer) enables the grounded assistant endpoints. Inject a
    FakeLLMClient in tests; pass None (default) and the AI endpoints report
    unavailable — the engine runs with zero AI configuration."""
    factory = session_factory or make_session_factory(make_engine())
    app = FastAPI(
        title="GovCon Compliance Workbench",
        description=(
            "Advisory decision-support & training tool on SYNTHETIC data. "
            "Not a certified accounting system; see /api/about."
        ),
        version="0.1.0",
    )
    from govcon.api.hardening import install as install_hardening
    from govcon.api.hardening import make_ask_limiter

    install_hardening(app)  # request-id + security headers + optional gate/CORS
    ask_limiter = make_ask_limiter()

    @app.get("/health")
    def health() -> dict:
        """Liveness + a cheap DB readiness probe."""
        import sqlalchemy as sa

        try:
            with factory() as s:
                s.execute(sa.text("SELECT 1"))
            db_ok = True
        except Exception:  # pragma: no cover - only on a broken DB
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "db": db_ok,
                "ai": llm_client is not None}

    # Bounded engine cache (one connection pool per workspace); an LRU cap +
    # lock stop unbounded pool growth and a check-then-build race across the
    # sync-endpoint threadpool.
    workspace_factories: OrderedDict[str, object] = OrderedDict()
    workspace_lock = threading.Lock()
    _MAX_CACHED_WORKSPACES = 32

    @app.middleware("http")
    async def _attribute_request(request: Request, call_next):
        # Asserted identity (stated limitation): a header names the actor;
        # authentication is a deployment concern in front of this app. The
        # value is sanitized + length-capped before it reaches the immutable
        # audit trail (it is untrusted input).
        user = sanitize_actor_label(request.headers.get("x-govcon-user"))
        token = set_actor(f"web:{user}" if user else "web:anonymous")
        try:
            return await call_next(request)
        finally:
            reset_actor(token)

    def _factory_for(request: Request):
        name = request.headers.get("x-govcon-workspace")
        if not name or workspace_registry is None:
            return factory
        with workspace_lock:
            cached = workspace_factories.get(name)
            if cached is not None:
                workspace_factories.move_to_end(name)
                return cached
            try:
                if not workspace_registry.exists(name):
                    raise HTTPException(
                        status_code=404,
                        detail=f"no workspace {name!r} — create it with "
                        "`govcon workspace create`",
                    )
            except ValueError as exc:  # hostile/invalid name — never a path
                raise HTTPException(status_code=422, detail=str(exc)) from None
            built = make_session_factory(make_engine(workspace_registry.url(name)))
            workspace_factories[name] = built
            if len(workspace_factories) > _MAX_CACHED_WORKSPACES:
                _, evicted = workspace_factories.popitem(last=False)
                evicted.kw["bind"].dispose()  # close the evicted pool
            return built

    def get_session(request: Request) -> Iterator[Session]:
        with _factory_for(request)() as session:
            yield session

    @app.get("/api/whoami")
    def whoami(request: Request) -> dict:
        """Who the engine will attribute this request's actions to, and which
        isolated workspace it is routed at. Deliberately does NOT enumerate
        other workspaces — that would leak tenant identities across the very
        isolation boundary this feature enforces (enumeration is CLI-only,
        `govcon workspace list`, gated by local shell access)."""
        requested = request.headers.get("x-govcon-workspace")
        if workspace_registry is None:
            workspace = "default"
        elif requested:
            # only confirm the caller's OWN workspace, never list the rest
            try:
                workspace = requested if workspace_registry.exists(requested) else "unknown"
            except ValueError:
                workspace = "invalid"
        else:
            workspace = "default"
        return {
            "actor": current_actor(),
            "workspace": workspace,
            "routing_enabled": workspace_registry is not None,
        }

    # ------------------------------------------------------------ AI assistant
    @app.post("/api/ask")
    def ask(
        req: AskRequest, request: Request, session: Session = Depends(get_session)
    ) -> dict:
        """Conversational query (Pattern 1): a plain-English question → the AI
        calls the deterministic engine as tools → a grounded answer that ALWAYS
        returns the authoritative determination beside the prose. The AI never
        makes the determination; unverified prose is withheld."""
        if llm_client is None:
            return {"ai_available": False, "reason": "AI is not configured on this server"}
        from fastapi import HTTPException as _HTTPException

        from govcon.ai.errors import CostCeilingError, SyntheticGateError
        from govcon.ai.gate import assert_synthetic
        from govcon.ai.patterns import ask as run_ask
        from govcon.api.hardening import _client_key

        if not ask_limiter.allow(_client_key(request)):
            raise _HTTPException(status_code=429, detail="rate limit exceeded; slow down")

        try:
            assert_synthetic()  # fast HTTP-layer reject (kernel re-checks)
        except SyntheticGateError as exc:
            return {"ai_available": False, "reason": str(exc)}
        # Hard per-request USD ceiling so a single /api/ask cannot drive
        # unbounded Claude spend (GOVCON_AI_MAX_USD, default $0.50). Combined
        # with the rate limiter below, this bounds AI cost-DoS.
        try:
            max_usd = Decimal(os.environ.get("GOVCON_AI_MAX_USD", "0.50"))
        except (InvalidOperation, TypeError):
            max_usd = Decimal("0.50")
        try:
            result = run_ask(
                llm_client,
                session,
                req.question,
                actor=current_actor(),
                workspace=request.headers.get("x-govcon-workspace") or "default",
                max_usd=max_usd,
            )
        except CostCeilingError as exc:
            return {"ai_available": True, "error": str(exc), "cost_exceeded": True}
        return {
            "ai_available": True,
            "prose": result.prose,
            "determinations": result.determinations,
            "grounding": {
                "verified": result.grounding.verified,
                "violations": result.grounding.violations,
            },
            "cost": result.cost.as_dict(),
            "notice": (
                "Advisory rendering over synthetic data. The structured "
                "determination above is the authoritative result."
            ),
        }

    # ---------------------------------------------------------------- the UI
    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = (_WEB_DIR / "index.html").read_text(encoding="utf-8")
        # Explicit charset + a short revalidating cache: the 140KB file (mostly
        # inlined fonts) is cacheable, but must-revalidate so a redeploy of the
        # UI code is picked up promptly rather than served stale forever.
        return HTMLResponse(
            html,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "max-age=300, must-revalidate",
            },
        )

    # ------------------------------------------------------------ determinations
    @app.post("/api/cas")
    def cas(req: CASRequest, session: Session = Depends(get_session)) -> dict:
        try:
            contract = Contract(
                award_date=req.award_date,
                contract_value=_money(req.contract_value),
                contractor_size=req.contractor_size,
                is_nontraditional_dc=req.is_nontraditional_dc,
                agency_type=req.agency_type,
                cas_coverage_type=CASCoverageType.NONE,
            )
            d = determine_cas_coverage(session, contract)
        except (ValueError, LookupError) as exc:
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
        try:
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
            d = determine_tina_applicability(session, action)
        except (ValueError, LookupError) as exc:
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

    _SUGGESTIONS_CAP = 500

    @app.get("/api/suggestions")
    def suggestions(
        session: Session = Depends(get_session), limit: int = _SUGGESTIONS_CAP
    ) -> dict:
        """Regulation-watch inbox, read-only. Scanning (network + writes) and
        reviewing stay CLI-only on purpose — the workbench never mutates.
        Hard-capped so the response can never blow up memory; ``truncated``
        flags when the cap hit."""
        import sqlalchemy as sa

        from govcon.models import RegulatorySuggestion

        capped = max(1, min(limit, _SUGGESTIONS_CAP))
        rows = (
            session.execute(
                sa.select(RegulatorySuggestion)
                .order_by(
                    RegulatorySuggestion.strong_match.desc(),
                    RegulatorySuggestion.publication_date.desc(),
                )
                .limit(capped + 1)
            )
            .scalars()
            .all()
        )
        truncated = len(rows) > capped
        rows = rows[:capped]
        return {
            "truncated": truncated,
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
