"""AI interaction layer (enterprise vision, post-roadmap).

A grounded assistant over the deterministic engine: the AI is an INTERFACE that
translates English↔structured-inputs and explains determinations — it never
makes one. Every number it states must come from a tool result (the engine's
pure services), verified by the GroundingVerifier; unverified prose is withheld.
Synthetic-data only, fail-closed. See docs/AI_LAYER.md.
"""

from govcon.ai.client import AnthropicClient, LLMClient, default_client_or_none
from govcon.ai.patterns import ask

__all__ = ["AnthropicClient", "LLMClient", "ask", "default_client_or_none"]
