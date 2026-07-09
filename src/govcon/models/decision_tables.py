"""Rules-as-data decision tables (enterprise vision Phase 1).

The decision LOGIC that used to be code-locked in the service layer (the CAS
coverage order, the TINA exception ladder) lives here as versioned, dated,
append-only rows — the same discipline regulatory_thresholds established for
values, extended to the rule structure itself. Numeric thresholds are NOT
duplicated into rules: a rule references a threshold symbolically through the
table's threshold_context and the evaluator resolves the row in force on the
evaluation date via threshold_in_force(), so the dated-lookup and status-caveat
machinery stays single-sourced.

Split of responsibility (deliberate):
  * inputs  = code — the service adapter assembles facts (queries, computed
    sums, tri-state counts) into a plain dict;
  * logic   = data — ordered DecisionRule rows with safe JSON predicates
    (never eval()), outcome fragments, and reason templates;
  * values  = regulatory_thresholds — unchanged.

Both tables are append-only (triggers in migration 0015): changing a rule set
means seeding a NEW version row via a migration a human reviews — there is no
silent-regulation-automation path here by construction.
"""

from __future__ import annotations

import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from govcon.db.base import Base
from govcon.models.enums import ThresholdStatus, db_enum


class DecisionTable(Base):
    __tablename__ = "decision_tables"
    __table_args__ = (
        sa.UniqueConstraint("table_name", "version", name="uq_decision_tables_name_version"),
    )

    decision_table_id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    # Dated-window semantics identical to regulatory_thresholds (None = open).
    effective_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    superseded_date: Mapped[datetime.date | None] = mapped_column(sa.Date)
    source_citation: Mapped[str] = mapped_column(sa.Text, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    #: {alias: regulatory_thresholds.rule_name} — the thresholds this table's
    #: rules may reference as {"threshold": alias}.
    threshold_context: Mapped[dict | None] = mapped_column(sa.JSON)
    #: "eager" resolves (and caveats) every alias at evaluation start;
    #: "on_first_use" resolves the whole context when evaluation first reaches
    #: a rule that references any alias — so early-exit rules (e.g. the CAS
    #: small-business exemption) return with no threshold lookup, exactly as
    #: the coded logic did.
    threshold_resolution: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default="eager"
    )
    #: Outcome dict the evaluation starts from; matched rules merge onto it.
    initial_outcome: Mapped[dict | None] = mapped_column(sa.JSON)


class DecisionRule(Base):
    __tablename__ = "decision_rules"
    __table_args__ = (
        sa.UniqueConstraint(
            "decision_table_id", "rule_order", name="uq_decision_rules_table_order"
        ),
    )

    rule_id: Mapped[int] = mapped_column(primary_key=True)
    decision_table_id: Mapped[int] = mapped_column(
        sa.ForeignKey("decision_tables.decision_table_id"), nullable=False
    )
    rule_order: Mapped[int] = mapped_column(nullable=False)
    rule_key: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    #: Safe predicate AST (JSON, evaluated structurally — never eval()):
    #: None = always matches; {"all": [...]} / {"any": [...]} groups; leaf
    #: {"lhs": operand, "op": name, "rhs": operand} where an operand is
    #: {"input": key} | {"threshold": alias} | a JSON literal.
    when_ast: Mapped[dict | None] = mapped_column(sa.JSON)
    #: Outcome fragment merged onto the running outcome when the rule matches.
    outcome: Mapped[dict | None] = mapped_column(sa.JSON)
    #: str.format template rendered against inputs + resolved threshold values.
    reason_template: Mapped[str | None] = mapped_column(sa.Text)
    #: True = evaluation ends when this rule matches (first-match exit);
    #: False = merge and keep evaluating (cascade upgrade, e.g. modified→full).
    stop: Mapped[bool] = mapped_column(nullable=False, server_default=sa.false())
    #: Regulatory provenance of THIS rule's encoding, when it is weaker than
    #: settled final regulation (e.g. the CAS cumulative window encodes a
    #: still-PROPOSED 9903.201-2). A matched non-final rule emits a caveat —
    #: honesty the coded version buried in a comment. None = no caveat.
    status: Mapped[ThresholdStatus | None] = mapped_column(
        db_enum(ThresholdStatus, "threshold_status")
    )
    source_citation: Mapped[str | None] = mapped_column(sa.Text)
