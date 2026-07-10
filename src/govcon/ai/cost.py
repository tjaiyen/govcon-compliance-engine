"""Token + cost accounting for every LLM call (ai-ml.md hard rule).

A per-request CostLog accumulates usage across the tool-use loop; each
``client.create`` is recorded via ``core.logging`` (structlog) so spend is
observable, and the totals ride back in the API response envelope. An optional
per-request USD ceiling aborts the loop loudly rather than running away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from govcon.ai.errors import CostCeilingError
from govcon.core.logging import get_logger

#: USD per 1M tokens (input, output), by model-id prefix. From the claude-api
#: pricing table; unknown models fall back to the opus-tier rate.
_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-fable-5": (Decimal("10.00"), Decimal("50.00")),
    "claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-7": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-6": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-sonnet-4-6": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
}
_FALLBACK = (Decimal("5.00"), Decimal("25.00"))


def _rate(model: str) -> tuple[Decimal, Decimal]:
    for prefix, rate in _PRICING.items():
        if model.startswith(prefix):
            return rate
    return _FALLBACK


def call_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    in_rate, out_rate = _rate(model)
    per_million = Decimal(1_000_000)
    usd = (Decimal(input_tokens) * in_rate + Decimal(output_tokens) * out_rate) / per_million
    return usd.quantize(Decimal("0.000001"))


@dataclass
class CostLog:
    pattern: str
    actor: str = "unknown"
    workspace: str = "default"
    max_usd: Decimal | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usd: Decimal = field(default_factory=lambda: Decimal("0"))
    calls: int = 0

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        cost = call_cost_usd(model, input_tokens, output_tokens)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.usd += cost
        self.calls += 1
        get_logger("govcon.ai.cost").info(
            "llm_call",
            pattern=self.pattern,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usd=str(cost),
            actor=self.actor,
            workspace=self.workspace,
        )
        if self.max_usd is not None and self.usd > self.max_usd:
            raise CostCeilingError(
                f"AI request exceeded its {self.max_usd} USD ceiling "
                f"(spent {self.usd} over {self.calls} calls)"
            )

    def as_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": str(self.usd.quantize(Decimal("0.000001"))),
            "calls": self.calls,
        }
