"""Score a predicted SheetLayout against a golden one.

Anchors are the fields the extractor cannot work without (header/driver rows,
investor ranges, component columns and types, event blocks, ...). The layout-mapping
eval requires 100% anchor accuracy; ``field_accuracy`` over every golden field is
reported for tracking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ANCHORS: dict[str, list[str]] = {
    "allocation": [
        "header_row",
        "fund_driver_row",
        "columns.investor",
        "columns.commitment",
        "columns.commitment_pct",
        "components[].column+component_type+side",
        "roll_forward.*",
        "vehicles[].investor_rows",
        "vehicles[].driver_row",
        "vehicles[].subtotal_rows.total",
        "event.notice_date_cell",
        "event.due_date_cell",
    ],
    "itd": [
        "classification_rows.*",
        "event_header_row",
        "subheader_row",
        "investor_column",
        "cumulative_columns.*",
        "event_blocks[].first_column+last_column+is_current",
        "vehicles[].investor_rows",
    ],
    "summary": ["event_total_cell", "check_cells", "fund_commitment_cell", "notice_date_cell", "due_date_cell"],
    "merge": ["first_data_row", "last_data_row", "columns.investor", "columns.investor_id", "columns.fund_id",
              "columns.file_name"],
    "mgmt_fee": ["investor_rows", "columns.investor", "columns.affiliate_flag", "columns.commitment",
                 "fee_columns[].column"],
    "investor_data": ["columns.investor_name", "columns.investor_id", "columns.fund_id"],
    "holiday_calendar": ["date_column", "first_row", "last_row"],
}


@dataclass
class LayoutScore:
    anchor_accuracy: float
    field_accuracy: float
    mismatches: list[str] = field(default_factory=list)


def _get(obj: Any, path: str) -> Any:
    for part in path.split("."):
        if obj is None:
            return None
        obj = obj.get(part) if isinstance(obj, dict) else None
    return obj


def _anchor_values(layout: dict[str, Any], anchor: str) -> Any:
    if "[]" in anchor:
        list_key, rest = anchor.split("[].", 1)
        items = layout.get(list_key) or []
        keys = rest.split("+")
        return sorted(tuple(str(_get(item, k)) for k in keys) for item in items)
    if anchor.endswith(".*"):
        mapping = _get(layout, anchor[:-2]) or {}
        return {k: v for k, v in mapping.items() if v is not None}
    return _get(layout, anchor)


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            out.update(_flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def score_layout(predicted: dict[str, Any], golden: dict[str, Any]) -> LayoutScore:
    role = golden["role"]
    anchors = ANCHORS[role]
    mismatches = [a for a in anchors if _anchor_values(predicted, a) != _anchor_values(golden, a)]
    golden_fields = {k: v for k, v in _flatten(golden).items() if v is not None}
    predicted_fields = _flatten(predicted)
    matched = sum(1 for k, v in golden_fields.items() if predicted_fields.get(k) == v)
    return LayoutScore(
        anchor_accuracy=1 - len(mismatches) / len(anchors),
        field_accuracy=matched / len(golden_fields) if golden_fields else 1.0,
        mismatches=mismatches,
    )
