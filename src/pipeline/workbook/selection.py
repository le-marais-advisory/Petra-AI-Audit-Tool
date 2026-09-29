"""Choose the sheets a run processes, from the event type the user selected."""
from __future__ import annotations

from src.document_types.registry import get_document_type, relevant_roles

DOCUMENT_TYPE = "capital_event_workbook"


def relevant_sheets(sheet_roles: dict[str, str], event_type: str) -> list[str]:
    """Sheets whose role the event type needs, in workbook order."""
    roles = set(relevant_roles(get_document_type(DOCUMENT_TYPE), event_type))
    return [name for name, role in sheet_roles.items() if role in roles]
