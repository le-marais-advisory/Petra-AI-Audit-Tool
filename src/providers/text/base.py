from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TextAnalysisProvider(ABC):
    @abstractmethod
    def evaluate_rule(self, document_content: str, rule: dict, system_prompt: str) -> dict[str, Any]:
        raise NotImplementedError

    def complete_structured(
        self,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        name: str = "result",
        effort: str | None = None,
    ) -> dict[str, Any]:
        """Return a JSON object that validates against ``json_schema`` (strict structured output).

        ``effort`` ("low" | "medium" | "high") is a reasoning-depth hint; providers without an
        equivalent ignore it. Raises ``TruncatedResponseError`` when the answer hit the token cap.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support structured completion")
