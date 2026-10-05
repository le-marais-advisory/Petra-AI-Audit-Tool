"""Render a comparison's results as a self-contained HTML page (no external assets: it may hold client data)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.evaluation.llm_compare.metrics import compute

_TEMPLATE = Path(__file__).with_name("report_template.html")


def merge_results(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine runs from several comparisons (e.g. a variant added later) into one result set. The first part's
    settings win; variants and cases are unioned and runs are concatenated."""
    merged = json.loads(json.dumps(parts[0]))
    for part in parts[1:]:
        merged["variants"].update({k: v for k, v in part["variants"].items() if k not in merged["variants"]})
        merged["cases"].update({k: v for k, v in part["cases"].items() if k not in merged["cases"]})
        merged["runs"].extend(part["runs"])
        merged["repeats"] = max(merged.get("repeats", 1), part.get("repeats", 1))
    return merged


def render_report(results: dict[str, Any], path: Path) -> Path:
    payload = json.dumps({"results": {k: v for k, v in results.items() if k != "runs"}, "metrics": compute(results)},
                         default=str)
    # keep the embedded JSON from closing its <script> element early
    payload = payload.replace("</", "<\\/")
    path.write_text(_TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/", payload), encoding="utf-8")
    return path
