"""Small helpers shared by the workbook validator, extractor and checks."""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

# Money comparisons: the rules use $0.005 (penny-exact), $0.01 (roll-forward) and $2
# (pro-rata parity).
PENNY = Decimal("0.005")
CENT = Decimal("0.01")

_SUM_RANGE_RE = re.compile(r"SUM\(\s*\$?([A-Z]{1,3})\$?(\d+)\s*:\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)", re.I)


def col_idx(column: str) -> int:
    return column_index_from_string(column.replace("$", "").upper())


def col_letter(index: int) -> str:
    return get_column_letter(index)


def coord(column: str, row: int) -> str:
    return f"{column}{row}"


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def is_text(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def to_decimal(value: Any) -> Decimal | None:
    """Numbers and number-like strings ('$1,234.50', '(12.00)', '-') to Decimal."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        text = value.strip().replace("$", "").replace(",", "")
        if text in ("-", "–", "—"):
            return Decimal("0")
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        return -number if negative else number
    return None


def to_money(value: Any) -> Decimal:
    number = to_decimal(value)
    return number if number is not None else Decimal("0")


def to_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y", "%m.%d.%y", "%m.%d.%Y", "%m-%d-%y", "%m-%d-%Y"):
            try:
                return dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def sum_range_rows(formula: str | None) -> tuple[int, int] | None:
    """Row span of the first SUM(X7:X19) in a formula, if any."""
    if not formula:
        return None
    match = _SUM_RANGE_RE.search(formula)
    if not match:
        return None
    return int(match.group(2)), int(match.group(4))


def rows_between(span: list[int]) -> range:
    return range(span[0], span[-1] + 1)
