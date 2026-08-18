"""Dexia AIP Logic Layer — self-correcting doctrine (Phase 8.5).

After-Action Review + Doctrine update Logic Blocks over a JSON Tactical Recipe,
with an episodic-memory trail of doctrine evolution. Ray-free architectural PoC.
"""

from .logic_blocks import (
    INITIAL_DOCTRINE,
    DEFAULT_RECIPES_PATH,
    DEFAULT_MEMORY_PATH,
    DEFAULT_PENDING_PATH,
    OAGEngine,
    MockLLMClient,
    OllamaLLMClient,
    AfterActionReviewBlock,
    DoctrineUpdateBlock,
    DoctrineProposalBlock,
    AARParseError,
)

__all__ = [
    "INITIAL_DOCTRINE", "DEFAULT_RECIPES_PATH", "DEFAULT_MEMORY_PATH",
    "DEFAULT_PENDING_PATH",
    "OAGEngine", "MockLLMClient", "OllamaLLMClient",
    "AfterActionReviewBlock", "DoctrineUpdateBlock", "DoctrineProposalBlock",
    "AARParseError",
]
