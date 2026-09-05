"""The interface every AI provider implements."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class AIError(Exception):
    """Something went wrong talking to the model."""


class AIUnavailable(AIError):
    """No provider is configured, or its package isn't installed."""


@runtime_checkable
class AIProvider(Protocol):
    """A minimal interface, so providers stay swappable.

    Only two operations are needed: get structured JSON back for
    extraction, and get prose back for summaries.
    """

    name: str
    model: str

    def complete_json(self, *, system: str, prompt: str, schema: dict) -> dict:
        """Return a JSON object conforming to schema."""
        ...

    def complete_text(self, *, system: str, prompt: str, max_tokens: int = 1024) -> str:
        """Return plain text."""
        ...
