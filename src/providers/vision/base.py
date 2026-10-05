from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VisionProvider(ABC):
    @abstractmethod
    def evaluate_rule(
        self, page_image: dict[str, Any], rule: dict, system_prompt: str, cache_content: bool = False
    ) -> dict[str, Any]:
        """Evaluate ``rule`` against the page image. ``cache_content`` asks for the image to be cached,
        because other rules will be evaluated against the same page."""
        raise NotImplementedError
