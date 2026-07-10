"""FastAPI application factory + endpoints (Phase 0).

The endpoints construct *transient* (unsaved) model objects from the request
and hand them to the pure service functions — the determinations are read-only
(they look up seeded regulatory thresholds and compute), so nothing is
persisted. This keeps the write path (append-only ledger, audit chain)
completely untouched by the browsing/what-if UI.
"""

from __future__ import annotations

import datetime
import enum
import json
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


class TutorPersona(str, enum.Enum):
    """The five teaching depths — same data, different framing (mirrors the
    guided UI's persona bar). An unknown value → 422 (Pydantic validation)."""

    NEWCOMER = "newcomer"
    ANALYST = "analyst"
    CONTROLLER = "controller"
    EXECUTIVE = "executive"
    AUDITOR = "auditor"


class TutorRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    persona: TutorPersona = TutorPersona.NEWCOMER


class DraftRuleRequest(BaseModel):
    instruction: str = Field(
        ..., min_length=1, max_length=2000,
        description="Describe the regulatory change to draft a decision rule for.",
    )


class NarrativeRequest(BaseModel):
    instruction: str = Field(
        ..., min_length=1, max_length=2000,
        description="Describe the situation to draft a grounded memo/narrative for.",
    )


def _advisory_notice() -> str:
    """The per-response advisory line — honest about the LIVE data mode (synthetic
    by default; real, local-model when GOVCON_DATA_MODE=real)."""
    from govcon.ai.gate import is_real

    over = "real data (local model)" if is_real() else "synthetic data"
    return (
        f"Advisory rendering over {over}. The structured determination above is "
        "the authoritative result."
    )


def _narrative_banner() -> str:
    """The draft-narrative banner — mode-aware, always advisory/not-for-filing."""
    from govcon.ai.gate import is_real

    kind = "REAL DATA (LOCAL MODEL)" if is_real() else "SYNTHETIC DATA"
    return f"{kind} — DRAFT NARRATIVE FOR INTERNAL REVIEW, NOT FOR FILING OR CERTIFICATION"


def _data_mode_label() -> str:
    """A short mode-aware data label ('Synthetic data.' / 'Real data (local model).')."""
    from govcon.ai.gate import is_real

    return "Real data (local model)." if is_real() else "Synthetic data."


def _sse(evt: dict) -> str:
    """Pack one event as an SSE ``data:`` frame (single unnamed event channel)."""
    return "data: " + json.dumps(evt) + "\n\n"


def _sse_response(events):
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        events, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_single(evt: dict):
    """A one-off SSE stream (an event + done) — e.g. AI-not-configured."""
    def _one():
        yield _sse(evt)
        yield _sse({"type": "done"})

    return _sse_response(_one())


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
            "Advisory decision-support & training tool. Not a certified accounting "
            "system; see /api/about for the current data mode and limitations."
        ),
        version="0.1.0",
    )
    from govcon.api.auth import build_verifier
    from govcon.api.hardening import install as install_hardening
    from govcon.api.hardening import make_ask_limiter

    # Real per-user JWT auth (None = off, the default: identity stays asserted
    # from the header). When on, the hardening layer verifies every gated
    # /api/* request and sets a cryptographically-verified auth:<sub> actor.
    verifier = build_verifier()
    install_hardening(app, verifier=verifier)  # request-id + headers + auth/CORS
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
        from govcon.ai.gate import data_mode

        return {"status": "ok" if db_ok else "degraded", "db": db_ok,
                "ai": llm_client is not None, "data_mode": data_mode()}

    # Bounded engine cache (one connection pool per workspace); an LRU cap +
    # lock stop unbounded pool growth and a check-then-build race across the
    # sync-endpoint threadpool.
    workspace_factories: OrderedDict[str, object] = OrderedDict()
    workspace_lock = threading.Lock()
    _MAX_CACHED_WORKSPACES = 32

    @app.middleware("http")
    async def _attribute_request(request: Request, call_next):
        if verifier is not None:
            # Auth is ON: the cryptographically-verified actor is set by the
            # hardening layer (deeper middleware). The spoofable X-Govcon-User
            # header is STRUCTURALLY IGNORED here — defer entirely.
            return await call_next(request)
        # Auth is OFF (default): asserted identity (stated limitation) — a header
        # names the actor; the value is sanitized + length-capped before it
        # reaches the immutable audit trail (it is untrusted input).
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
                # Close the evicted pool. Safe even if an in-flight request still
                # holds this factory: engine.dispose() only closes IDLE pooled
                # connections; a subsequent checkout transparently re-opens.
                evicted.kw["bind"].dispose()
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
    def _grounded_envelope(result) -> dict:
        """The default AI response shape: prose + the authoritative determination(s)
        + grounding + cost. Used by /api/ask and /api/tutor."""
        return {
            "ai_available": True,
            "prose": result.prose,
            "determinations": result.determinations,
            "grounding": {
                "verified": result.grounding.verified,
                "violations": result.grounding.violations,
            },
            "cost": result.cost.as_dict(),
            "notice": _advisory_notice(),
        }

    def _serve_ai(request: Request, run, *, envelope=None, extra: dict | None = None) -> dict:
        """Shared plumbing for every AI pattern endpoint: rate limit → synthetic
        gate → per-request USD ceiling → run → pattern envelope. ``run`` is a
        callable ``(max_usd) -> AITurnResult`` capturing the pattern + inputs;
        ``envelope`` builds the pattern-specific response (defaults to the
        grounded shape). One loop of guardrails for every AI route (no drift)."""
        from fastapi import HTTPException as _HTTPException

        from govcon.ai.errors import CostCeilingError, SyntheticGateError
        from govcon.ai.gate import assert_data_mode
        from govcon.api.hardening import _client_key

        if not ask_limiter.allow(_client_key(request)):
            raise _HTTPException(status_code=429, detail="rate limit exceeded; slow down")
        try:
            assert_data_mode(llm_client)  # fast HTTP-layer reject (kernel re-checks)
        except SyntheticGateError as exc:
            return {"ai_available": False, "reason": str(exc)}
        # Hard per-request USD ceiling so a single AI call cannot drive unbounded
        # Claude spend (GOVCON_AI_MAX_USD, default $0.50). With the rate limiter
        # above, this bounds AI cost-DoS.
        try:
            max_usd = Decimal(os.environ.get("GOVCON_AI_MAX_USD", "0.50"))
        except (InvalidOperation, TypeError):
            max_usd = Decimal("0.50")
        try:
            result = run(max_usd)
        except CostCeilingError as exc:
            return {"ai_available": True, "error": str(exc), "cost_exceeded": True}
        except Exception:
            # Degrade, don't 500: an unexpected failure (e.g. the local model is
            # unreachable in real-data mode) returns a clean error the UI can show.
            # Logged server-side; the message is generic so no internals leak.
            from govcon.core.logging import get_logger

            get_logger("govcon.api").exception("ai_request_failed")
            return {"ai_available": True, "error": "the assistant hit an unexpected error"}
        out = (envelope or _grounded_envelope)(result)
        out.update(extra or {})
        return out

    def _serve_ai_stream(request: Request, factory):
        """SSE variant of _serve_ai: the SAME rate-limit + synthetic gate + USD
        ceiling, but stream the loop's events (status → determination(s) →
        grounding → prose → cost → done) as they resolve. ``factory(max_usd)``
        yields event dicts (see patterns.stream_pattern)."""
        from fastapi import HTTPException as _HTTPException

        from govcon.ai.errors import CostCeilingError, SyntheticGateError
        from govcon.ai.gate import assert_data_mode
        from govcon.api.hardening import _client_key
        from govcon.core.logging import get_logger

        if not ask_limiter.allow(_client_key(request)):
            raise _HTTPException(status_code=429, detail="rate limit exceeded; slow down")

        def _events():
            try:
                assert_data_mode(llm_client)
            except SyntheticGateError as exc:
                yield _sse({"type": "unavailable", "reason": str(exc)})
                yield _sse({"type": "done"})
                return
            try:
                max_usd = Decimal(os.environ.get("GOVCON_AI_MAX_USD", "0.50"))
            except (InvalidOperation, TypeError):
                max_usd = Decimal("0.50")
            try:
                for evt in factory(max_usd):
                    yield _sse(evt)
            except CostCeilingError as exc:
                yield _sse({"type": "error", "cost_exceeded": True, "message": str(exc)})
            except SyntheticGateError as exc:  # belt-and-suspenders vs kernel gate
                yield _sse({"type": "unavailable", "reason": str(exc)})
            except Exception:
                # Degrade, never break the stream: any unexpected error still
                # terminates cleanly with an error + done, so the client is never
                # left hanging on a half-open stream. Logged server-side; the
                # client message is generic so no internals leak.
                get_logger("govcon.api").exception("ai_stream_failed")
                yield _sse({"type": "error", "message": "the assistant hit an unexpected error"})
            yield _sse({"type": "done"})

        return _sse_response(_events())

    def _maybe_stream(request: Request, pattern: str, text: str, *, persona: str | None = None):
        """If ``?stream`` is set, return an SSE StreamingResponse for a streamable
        pattern; else return None so the caller falls through to the JSON path.

        The stream outlives the request's Depends(get_session) scope, so it opens
        its OWN session on the resolved workspace factory and closes it in a
        finally when iteration ends (or errors)."""
        if not request.query_params.get("stream"):
            return None
        from govcon.ai.patterns import STREAMABLE, stream_pattern

        if pattern not in STREAMABLE:
            return None
        if llm_client is None:
            return _sse_single({"type": "unavailable", "reason": "AI is not configured on this server"})
        workspace = request.headers.get("x-govcon-workspace") or "default"
        actor = current_actor()

        def factory(max_usd):
            session = _factory_for(request)()
            try:
                yield from stream_pattern(
                    llm_client, session, text, pattern=pattern, persona=persona,
                    actor=actor, workspace=workspace, max_usd=max_usd,
                )
            finally:
                session.close()

        return _serve_ai_stream(request, factory)

    @app.post("/api/ask")
    def ask(
        req: AskRequest, request: Request, session: Session = Depends(get_session)
    ) -> dict:
        """Conversational query (Pattern 1): a plain-English question → the AI
        calls the deterministic engine as tools → a grounded answer that ALWAYS
        returns the authoritative determination beside the prose. The AI never
        makes the determination; unverified prose is withheld. Pass ``?stream=1``
        for an SSE stream of the determinations + grounded prose as they resolve."""
        streamed = _maybe_stream(request, "ask", req.question)
        if streamed is not None:
            return streamed
        if llm_client is None:
            return {"ai_available": False, "reason": "AI is not configured on this server"}
        from govcon.ai.patterns import ask as run_ask

        return _serve_ai(
            request,
            lambda max_usd: run_ask(
                llm_client,
                session,
                req.question,
                actor=current_actor(),
                workspace=request.headers.get("x-govcon-workspace") or "default",
                max_usd=max_usd,
            ),
        )

    @app.post("/api/tutor")
    def tutor(
        req: TutorRequest, request: Request, session: Session = Depends(get_session)
    ) -> dict:
        """AI tutor (Pattern 2): the same grounded engine-as-tools loop as
        /api/ask, but taught at the requested ``persona``'s depth. Same
        authoritative-determination-beside-prose contract; same withhold-on-
        ungrounded discipline. Teaching depth never changes the determination.
        Pass ``?stream=1`` for an SSE stream (persona taken from the body)."""
        streamed = _maybe_stream(request, "tutor", req.question, persona=req.persona.value)
        if streamed is not None:
            return streamed
        if llm_client is None:
            return {"ai_available": False, "reason": "AI is not configured on this server"}
        from govcon.ai.patterns import tutor as run_tutor

        return _serve_ai(
            request,
            lambda max_usd: run_tutor(
                llm_client,
                session,
                req.question,
                persona=req.persona.value,
                actor=current_actor(),
                workspace=request.headers.get("x-govcon-workspace") or "default",
                max_usd=max_usd,
            ),
            extra={"persona": req.persona.value},
        )

    @app.post("/api/draft-rule")
    def draft_rule(
        req: DraftRuleRequest, request: Request, session: Session = Depends(get_session)
    ) -> dict:
        """Rule-authoring (Pattern 3): describe a regulatory change → the AI drafts
        a decision-table rule and validates it STRUCTURALLY → returns the draft for
        a human-reviewed migration. It applies NOTHING and writes NOTHING (B53):
        the response always carries ``requires_human_migration: true``."""
        if llm_client is None:
            return {"ai_available": False, "reason": "AI is not configured on this server"}
        from govcon.ai.patterns import draft_rule as run_draft

        def _draft_envelope(result) -> dict:
            # The drafted rule is the input to the LAST validate_draft_rule call;
            # its result is the validation. (No validate call → no validated draft.)
            draft, validation = None, None
            for d in result.determinations:
                if d["tool"] == "validate_draft_rule" and not d["is_error"]:
                    draft = (d["input"] or {}).get("rule", d["input"])
                    validation = d["result"]
            return {
                "ai_available": True,
                "prose": result.prose,
                "draft": draft,
                "validation": validation,
                "requires_human_migration": True,
                "grounding": {
                    "verified": result.grounding.verified,
                    "violations": result.grounding.violations,
                },
                "cost": result.cost.as_dict(),
                "notice": (
                    "DRAFT only — a proposal for a human-reviewed migration. Nothing "
                    "was applied, saved, or put in force. " + _data_mode_label()
                ),
            }

        return _serve_ai(
            request,
            lambda max_usd: run_draft(
                llm_client,
                session,
                req.instruction,
                actor=current_actor(),
                workspace=request.headers.get("x-govcon-workspace") or "default",
                max_usd=max_usd,
            ),
            envelope=_draft_envelope,
        )

    @app.post("/api/draft-narrative")
    def draft_narrative(
        req: NarrativeRequest, request: Request, session: Session = Depends(get_session)
    ) -> dict:
        """Narrative drafter (Pattern 4): describe a situation → the AI drafts a
        memo grounded ENTIRELY in the engine's computed numbers, returned beside
        the authoritative determination. Strictest grounding (an ungrounded figure
        withholds the memo). A SYNTHETIC, advisory draft — never a filing.
        Pass ``?stream=1`` for an SSE stream."""
        streamed = _maybe_stream(request, "draft_narrative", req.instruction)
        if streamed is not None:
            return streamed
        if llm_client is None:
            return {"ai_available": False, "reason": "AI is not configured on this server"}
        from govcon.ai.patterns import draft_narrative as run_narrative

        return _serve_ai(
            request,
            lambda max_usd: run_narrative(
                llm_client,
                session,
                req.instruction,
                actor=current_actor(),
                workspace=request.headers.get("x-govcon-workspace") or "default",
                max_usd=max_usd,
            ),
            extra={"synthetic_banner": _narrative_banner()},
        )

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
