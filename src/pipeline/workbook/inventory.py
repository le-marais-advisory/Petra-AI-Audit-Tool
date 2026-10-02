"""Cheap per-sheet inventory built for every sheet, with no LLM involved."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.pipeline.workbook.loader import WorkbookModel

_PREVIEW_ROWS = 12
_PREVIEW_CELLS = 24
_SHEET_REF_RE = re.compile(r"'((?:[^']|'')+)'!|(?<![A-Za-z0-9_.'])([A-Za-z_][A-Za-z0-9_.]*)!")


@dataclass
class SheetInventoryEntry:
    name: str
    index: int
    state: str
    dimensions: str
    formula_count: int
    value_count: int
    header_preview: list[list[str]] = field(default_factory=list)  # first non-empty rows, text cells only
    linked_sheets: dict[str, int] = field(default_factory=dict)  # other sheet -> formula cells referencing it

    @property
    def is_empty(self) -> bool:
        return self.formula_count + self.value_count == 0

    @property
    def link_share(self) -> float:
        """Share of the sheet's formulas that read another sheet (a tab built of links scores ~1)."""
        if not self.formula_count:
            return 0.0
        return min(1.0, sum(self.linked_sheets.values()) / self.formula_count)

    def preview_text(self) -> str:
        return " ".join(" ".join(row) for row in self.header_preview)


def build_inventory(model: WorkbookModel) -> list[SheetInventoryEntry]:
    entries: list[SheetInventoryEntry] = []
    for sheet in model.sheets:
        formulas = sum(1 for c in sheet.cells.values() if c.is_formula)
        known = {s.name for s in model.sheets}
        linked: dict[str, int] = {}
        for cell in sheet.cells.values():
            if not cell.formula or "!" not in cell.formula:
                continue
            targets = {(q.replace("''", "'") if q else b) for q, b in _SHEET_REF_RE.findall(cell.formula)}
            for target in targets & known - {sheet.name}:
                linked[target] = linked.get(target, 0) + 1
        preview: list[list[str]] = []
        for _, cells in sheet.rows():
            texts = [str(c.value).strip() for c in cells if isinstance(c.value, str) and str(c.value).strip()]
            if texts:
                preview.append(texts[:_PREVIEW_CELLS])
            if len(preview) >= _PREVIEW_ROWS:
                break
        entries.append(
            SheetInventoryEntry(
                name=sheet.name,
                index=sheet.index,
                state=sheet.state,
                dimensions=sheet.dimensions,
                formula_count=formulas,
                value_count=len(sheet.cells) - formulas,
                header_preview=preview,
                linked_sheets=linked,
            )
        )
    return entries
