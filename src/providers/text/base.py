from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TextAnalysisProvider(ABC):
    @abstractmethod
    def evaluate_rule(
        self,
        document_content: str,
        rule: dict,
        system_prompt: str,
        rule_context: str = "",
        cache_content: bool = False,
        shared_context: str = "",
        cache_shared_context: bool = False,
    ) -> dict[str, Any]:
        """Evaluate ``rule`` against ``document_content``.

        The prompt runs from most to least shared:
        ``document_content`` is shared by every rule run on the same content and goes first;
        ``shared_context`` is shared by a subset of those rules (e.g. layout metadata) and follows it;
        ``rule_context`` is this rule's alone and goes last, with the rule.
        ``cache_content`` / ``cache_shared_context`` ask for those parts to be cached, because other
        calls will reuse them.
        """
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
