"""Cheap per-sheet inventory built for every sheet, with no LLM involved."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.pipeline.workbook.loader import WorkbookModel

_PREVIEW_ROWS = 12
_PREVIEW_CELLS = 24


@dataclass
class SheetInventoryEntry:
    name: str
    index: int
    state: str
    dimensions: str
    formula_count: int
    value_count: int
    header_preview: list[list[str]] = field(default_factory=list)  # first non-empty rows, text cells only

    @property
    def is_empty(self) -> bool:
        return self.formula_count + self.value_count == 0

    def preview_text(self) -> str:
        return " ".join(" ".join(row) for row in self.header_preview)


def build_inventory(model: WorkbookModel) -> list[SheetInventoryEntry]:
    entries: list[SheetInventoryEntry] = []
    for sheet in model.sheets:
        formulas = sum(1 for c in sheet.cells.values() if c.is_formula)
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
            )
        )
    return entries
