from __future__ import annotations

import re


COVER_PAGE = "cover_page"
TABLE_OF_CONTENTS = "table_of_contents"
BALANCE_SHEET = "balance_sheet"
STATEMENT_OF_OPERATIONS = "statement_of_operations"
STATEMENT_OF_CASH_FLOWS = "statement_of_cash_flows"
SCHEDULE_OF_INVESTMENTS = "schedule_of_investments"
STATEMENT_OF_CHANGES = "statement_of_changes"

GLOBAL_SECTIONS = {"", "all", "all statements", "full document"}

# Pages that carry no evaluable financial data. Arithmetic / signage / formatting-
# geometry rules must not run here — on these pages they produce false positives
# (fail / needs_review) instead of the correct not_applicable.
STRUCTURAL_PAGE_TYPES = {COVER_PAGE, TABLE_OF_CONTENTS}

# A rule is "structural-skip" (not applicable on cover / TOC pages) when its id
# starts with one of these prefixes or is one of these exact ids. Covers signage,
# cross-foot / re-foot, arithmetic tie-outs, dollar-sign placement, double
# underlines, and visual-integrity checks.
_STRUCTURAL_SKIP_PREFIXES = ("NUM-", "SIG-", "ARITH-", "TIE-", "RND-")
_STRUCTURAL_SKIP_IDS = {
    "FMT-DOLLAR-SIGNS",
    "FMT-DOUBLE-UNDERLINE",
    "FMT-VISUAL-INTEGRITY",
    "BS-TIE-OUTS",
    "DUP-TOTALS",
    "SOI-PERCENTAGE-TIE",
}


def _is_structural_skip_rule(rule: dict) -> bool:
    rule_id = str(rule.get("id") or "").strip().upper()
    return rule_id in _STRUCTURAL_SKIP_IDS or rule_id.startswith(_STRUCTURAL_SKIP_PREFIXES)

SECTION_TO_KEY: dict[str, str] = {
    "cover page": COVER_PAGE,
    "balance sheet": BALANCE_SHEET,
    "statement of assets and liabilities": BALANCE_SHEET,
    "statement of operations": STATEMENT_OF_OPERATIONS,
    "statement of income": STATEMENT_OF_OPERATIONS,
    "statement of cash flows": STATEMENT_OF_CASH_FLOWS,
    "schedule of investments": SCHEDULE_OF_INVESTMENTS,
    "statement of changes in partners' capital": STATEMENT_OF_CHANGES,
    "statement of changes in members' capital": STATEMENT_OF_CHANGES,
    "statement of changes in net assets": STATEMENT_OF_CHANGES,
}

BALANCE_SHEET_SIGNALS = (
    "statement of assets and liabilities",
    "balance sheet",
    "total assets",
    "total liabilities",
    "total net assets",
    "members' capital",
    "members\u2019 capital",
    "partners' capital",
    "partners\u2019 capital",
)

OPERATIONS_SIGNALS = (
    "statement of operations",
    "statement of income",
    "net investment income",
    "net realized gain",
    "net realized loss",
    "net change in unrealized",
    "realized and unrealized",
    "investment income",
)

CASH_FLOWS_SIGNALS = (
    "statement of cash flows",
    "cash flows from operating",
    "cash flows from investing",
    "cash flows from financing",
    "net change in cash",
    "net increase in cash",
    "net decrease in cash",
)

INVESTMENTS_SIGNALS = (
    "schedule of investments",
    "investments in securities",
    "% of net assets",
    "percent of net assets",
    "portfolio of investments",
)

TOC_SIGNALS = (
    "table of contents",
)

COVER_SIGNALS = (
    "financial statements",
    "for the year ended",
    "for the period ended",
    "for the years ended",
    "for the periods ended",
    "audited financial statements",
    "unaudited financial statements",
    "annual report",
    "semi-annual report",
)

CHANGES_SIGNALS = (
    "statement of changes in partners' capital",
    "statement of changes in partners’ capital",
    "statement of changes in members' capital",
    "statement of changes in members’ capital",
    "statement of changes in net assets",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _page_text_blob(page: dict) -> str:
    parts: list[str] = [page.get("text") or ""]
    for table in page.get("tables") or []:
        for row in table.get("rows") or []:
            for cell in row:
                if cell:
                    parts.append(str(cell))
    return _normalize(" ".join(parts))


def classify_page(page: dict) -> list[str]:
    """Return every statement type detected on this page.

    Uses keyword heuristics on extracted text plus table cells. Empty list
    means we couldn't classify — callers should treat that as fail-open
    (run every rule) rather than skip.
    """
    blob = _page_text_blob(page)
    if not blob:
        return []

    # A table of contents lists the statement names alongside page numbers. Detect
    # it first and short-circuit: its line items (e.g. "Balance Sheet .... 3") would
    # otherwise trip the statement signals below and mislabel the TOC as those
    # statements, letting numeric rules run on a page that has no real data.
    if any(signal in blob for signal in TOC_SIGNALS):
        return [TABLE_OF_CONTENTS]

    types: list[str] = []
    if any(signal in blob for signal in BALANCE_SHEET_SIGNALS):
        types.append(BALANCE_SHEET)
    if any(signal in blob for signal in OPERATIONS_SIGNALS):
        types.append(STATEMENT_OF_OPERATIONS)
    if any(signal in blob for signal in CASH_FLOWS_SIGNALS):
        types.append(STATEMENT_OF_CASH_FLOWS)
    if any(signal in blob for signal in INVESTMENTS_SIGNALS):
        types.append(SCHEDULE_OF_INVESTMENTS)
    if any(signal in blob for signal in CHANGES_SIGNALS):
        types.append(STATEMENT_OF_CHANGES)

    page_number = page.get("page")
    table_count = len(page.get("tables") or [])
    is_early_page = isinstance(page_number, int) and page_number <= 2
    if not types and is_early_page and table_count <= 1:
        if any(signal in blob for signal in COVER_SIGNALS):
            types.append(COVER_PAGE)

    return types


def rule_applies_to_page(rule: dict, page_types: list[str] | None) -> bool:
    """Decide whether a rule should run on a page with the given classification.

    Fail-open: a rule runs when its section is global, when the page is
    unclassified, or when the rule's section maps to one of the page's types.

    Exception: numeric / geometry rules never run on structural pages (cover,
    table of contents) — there they are not_applicable, not fail/needs_review.
    """
    page_types = page_types or []

    if _is_structural_skip_rule(rule) and any(pt in STRUCTURAL_PAGE_TYPES for pt in page_types):
        return False

    section = _normalize(str(rule.get("section") or ""))
    if section in GLOBAL_SECTIONS:
        return True

    if not page_types:
        return True

    target = SECTION_TO_KEY.get(section)
    if target is None:
        return True

    return target in page_types
