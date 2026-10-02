"""Investor identity across sheets and workbooks: (vehicle, name), not name alone.

A fund with several vehicles (main fund, parallel fund, executive fund, ...) can carry the
same investor in more than one block, and the general partner entity usually sits in every
block. Keying investors by name alone collapses those rows onto one another and produces
false mismatches. Vehicle blocks are mapped by the LLM separately for every sheet and every
workbook, so their names need not agree either; blocks are aligned by position, confirmed
by the overlap of the investor names they hold.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

_MIN_OVERLAP = 0.5


def _names(vehicle: Any) -> set[str]:
    return {inv.name for inv in vehicle.investors}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def align_vehicles(source: Sequence[Any], target: Sequence[Any]) -> dict[int, int]:
    """Map each source vehicle ordinal to the target vehicle ordinal holding the same investors.

    Same position wins when its investor overlap is at least as good as any other block's;
    otherwise the best-overlapping unused block is taken. A source block with no overlap
    anywhere is mapped by position when the counts agree, and left unmapped otherwise.
    """
    source_names = [_names(v) for v in source]
    target_names = [_names(v) for v in target]
    alignment: dict[int, int] = {}
    used: set[int] = set()
    for i, names in enumerate(source_names):
        scores = [(_overlap(names, t), j) for j, t in enumerate(target_names) if j not in used]
        if not scores:
            break
        best_score = max(score for score, _ in scores)
        positional = next((score for score, j in scores if j == i), None)
        if best_score > 0 and positional is not None and positional >= best_score:
            chosen = i
        elif best_score > 0:
            chosen = max(scores)[1]
        elif len(source) == len(target) and i not in used:
            chosen = i
        else:
            continue
        alignment[i] = chosen
        used.add(chosen)
    return alignment


@dataclass
class InvestorIndex:
    """Investors of one sheet (or one workbook) addressable by (vehicle ordinal, name)."""

    vehicles: Sequence[Any]
    by_key: dict[tuple[int, str], Any] = field(default_factory=dict)
    by_name: dict[str, list[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for ordinal, vehicle in enumerate(self.vehicles):
            for inv in vehicle.investors:
                self.by_key.setdefault((ordinal, inv.name), inv)
                self.by_name.setdefault(inv.name, []).append(inv)

    def is_unique(self, name: str) -> bool:
        return len(self.by_name.get(name, [])) == 1

    def get(self, ordinal: int | None, name: str) -> Any | None:
        """The investor at (ordinal, name); when that block has no such row, the row of that
        name elsewhere if it is the only one."""
        if ordinal is not None:
            hit = self.by_key.get((ordinal, name))
            if hit is not None:
                return hit
        candidates = self.by_name.get(name, [])
        return candidates[0] if len(candidates) == 1 else None

    def __iter__(self):
        return iter(self.by_key.values())


class Matcher:
    """Find the counterpart of a source investor on a target sheet or workbook."""

    def __init__(self, source_vehicles: Sequence[Any], target_vehicles: Sequence[Any]) -> None:
        self.source = list(source_vehicles)
        self.target = InvestorIndex(list(target_vehicles))
        self.alignment = align_vehicles(self.source, self.target.vehicles)
        self._source_counts = Counter(inv.name for v in self.source for inv in v.investors)

    def pairs(self) -> Iterable[tuple[int, Any]]:
        """(vehicle ordinal, investor) for every source investor."""
        for ordinal, vehicle in enumerate(self.source):
            for inv in vehicle.investors:
                yield ordinal, inv

    def find(self, ordinal: int, name: str) -> Any | None:
        return self.target.get(self.alignment.get(ordinal), name)

    def label(self, ordinal: int, name: str) -> str:
        """The investor's name, qualified by its vehicle when the name appears in several blocks."""
        if self._source_counts.get(name, 0) > 1 and ordinal < len(self.source):
            vehicle = getattr(self.source[ordinal], "name", None)
            if vehicle:
                return f"{name} ({vehicle})"
        return name

    def target_pairs(self) -> Iterable[tuple[int, Any]]:
        for ordinal, vehicle in enumerate(self.target.vehicles):
            for inv in vehicle.investors:
                yield ordinal, inv

    def source_has(self, target_ordinal: int, name: str) -> bool:
        """Whether a target investor has a counterpart among the source investors."""
        reverse = {t: s for s, t in self.alignment.items()}
        source_index = InvestorIndex(self.source)
        return source_index.get(reverse.get(target_ordinal), name) is not None
