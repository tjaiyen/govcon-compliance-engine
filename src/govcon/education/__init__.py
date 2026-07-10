"""Education layer (enterprise vision Phase 2): the glossary and the
scenario/learning library the guided UI teaches from.

Everything here is DATA about the engine's real behavior, not new logic:
glossary examples quote the seeded thresholds, and every scenario carries a
pre-registered expected outcome that tests/test_education.py executes against
the live engine — the teaching content is proven, not asserted."""

from govcon.education.glossary import GLOSSARY
from govcon.education.scenarios import SCENARIOS

__all__ = ["GLOSSARY", "SCENARIOS"]
