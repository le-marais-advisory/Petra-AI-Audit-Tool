from __future__ import annotations

import re


COVER_PAGE = "cover_page"
BALANCE_SHEET = "balance_sheet"
STATEMENT_OF_OPERATIONS = "statement_of_operations"
STATEMENT_OF_CASH_FLOWS = "statement_of_cash_flows"
SCHEDULE_OF_INVESTMENTS = "schedule_of_investments"
STATEMENT_OF_CHANGES = "statement_of_changes"

GLOBAL_SECTIONS = {"", "all", "all statements", "full document"}

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
    """
    section = _normalize(str(rule.get("section") or ""))
    if section in GLOBAL_SECTIONS:
        return True

    if not page_types:
        return True

    target = SECTION_TO_KEY.get(section)
    if target is None:
        return True

    return target in page_types
