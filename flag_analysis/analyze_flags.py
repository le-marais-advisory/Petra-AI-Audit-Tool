"""Analyze flag/feedback data from Petra and ask Claude for areas of improvement.

By default, reads `~/Desktop/feedback_audit_tool/feedback.json` and writes
Claude's Markdown feedback to `~/Desktop/feedback_audit_tool/improvements.md`.
Both can be overridden on the CLI.

Auto-detects the input shape:

* **feedback.json** — list of FeedbackRecord (the file written by the API at
  `/app/data/feedback.json`). "Errors" = entries where users marked Claude's
  output as `incorrect`. The summary covers model accuracy and which rules
  most often produce wrong verdicts.
* **validation report** — a DocumentValidationResponse-shaped dict (output of
  `python -m src.main validate --out report.json`). "Errors" = rules with
  verdict `fail` or `needs_review`.

Usage:
    python flag_analysis/analyze_flags.py
    python flag_analysis/analyze_flags.py <path-to.json>
    python flag_analysis/analyze_flags.py --out custom.md
    python flag_analysis/analyze_flags.py --skip-llm

Environment:
    ANTHROPIC_API_KEY     Required (unless --skip-llm).
    CLAUDE_TEXT_MODEL     Optional. Defaults to claude-sonnet-5.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import typer
from anthropic import Anthropic
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

DEFAULT_MODEL = "claude-sonnet-5"
FAIL_VERDICTS = {"fail", "needs_review"}
DEFAULT_DIR = Path.home() / "Desktop" / "feedback_audit_tool"
DEFAULT_INPUT = DEFAULT_DIR / "feedback.json"
DEFAULT_OUTPUT = DEFAULT_DIR / "improvements.md"

cli = typer.Typer(add_completion=False, help=__doc__)
console = Console()


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise typer.BadParameter(f"File not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Invalid JSON in {path}: {exc}") from exc


def _is_feedback_records(payload: Any) -> bool:
    """Detect feedback.json shape: a list whose items have an `assessment` field."""
    return (
        isinstance(payload, list)
        and bool(payload)
        and isinstance(payload[0], dict)
        and "assessment" in payload[0]
    )


# ---------------------------------------------------------------------------
# Feedback (user-marked correct/incorrect) analysis
# ---------------------------------------------------------------------------

def _summarize_feedback(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    assessments = Counter(r.get("assessment", "unknown") for r in records)
    incorrect = [r for r in records if r.get("assessment") == "incorrect"]
    correct_count = assessments.get("correct", 0)
    accuracy = (correct_count / total * 100) if total else 0.0

    by_rule = Counter((r.get("rule_id"), r.get("rule_name")) for r in incorrect)
    by_verdict = Counter(r.get("verdict", "unknown") for r in incorrect)
    by_analysis_type = Counter(r.get("analysis_type", "unknown") for r in incorrect)
    by_document = Counter(r.get("source_filename") or r.get("document_id") for r in incorrect)

    return {
        "total_records": total,
        "assessment_counts": dict(assessments),
        "incorrect_count": len(incorrect),
        "accuracy_pct": round(accuracy, 1),
        "incorrect_by_rule": [
            {"rule_id": rid, "rule_name": rname, "count": c}
            for (rid, rname), c in by_rule.most_common(20)
        ],
        "incorrect_by_verdict": dict(by_verdict),
        "incorrect_by_analysis_type": dict(by_analysis_type),
        "incorrect_by_document": dict(by_document.most_common(10)),
        "incorrect": incorrect,
    }


def _render_feedback_summary(s: dict[str, Any]) -> None:
    table = Table(title="Feedback Summary", header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total feedback records", str(s["total_records"]))
    table.add_row("Model accuracy (user-rated)", f"{s['accuracy_pct']}%")
    table.add_row("Marked incorrect", str(s["incorrect_count"]))
    for k, v in s["assessment_counts"].items():
        table.add_row(f"  {k}", str(v))
    console.print(table)

    if s["incorrect_by_rule"]:
        rt = Table(title="Top Rules with Incorrect Assessments", header_style="bold")
        rt.add_column("Rule ID")
        rt.add_column("Rule Name")
        rt.add_column("Errors", justify="right")
        for row in s["incorrect_by_rule"][:10]:
            rt.add_row(row["rule_id"] or "?", row["rule_name"] or "?", str(row["count"]))
        console.print(rt)

    if s["incorrect_by_verdict"]:
        vt = Table(title="Incorrect by Verdict", header_style="bold")
        vt.add_column("Verdict")
        vt.add_column("Count", justify="right")
        for k, v in s["incorrect_by_verdict"].items():
            vt.add_row(k, str(v))
        console.print(vt)


def _build_feedback_prompt(s: dict[str, Any]) -> str:
    incorrect_payload = [
        {
            "rule_id": r.get("rule_id"),
            "rule_name": r.get("rule_name"),
            "analysis_type": r.get("analysis_type"),
            "verdict": r.get("verdict"),
            "summary": r.get("summary"),
            "reasoning": r.get("reasoning"),
            "comment": r.get("comment"),
            "page": r.get("page"),
            "source_filename": r.get("source_filename"),
        }
        for r in s["incorrect"]
    ]
    stats = {
        "total_records": s["total_records"],
        "accuracy_pct": s["accuracy_pct"],
        "assessment_counts": s["assessment_counts"],
        "incorrect_by_verdict": s["incorrect_by_verdict"],
        "incorrect_by_analysis_type": s["incorrect_by_analysis_type"],
        "incorrect_by_rule_top": s["incorrect_by_rule"][:10],
        "incorrect_by_document_top": s["incorrect_by_document"],
    }
    return (
        "You are reviewing user feedback on an AI-powered PDF audit tool. Each "
        "record below is a case the user marked as INCORRECT — meaning the "
        "model's verdict, reasoning, or summary did not match the user's "
        "expectation. Use this signal to identify where the model needs to "
        "improve.\n\n"
        "Return Markdown with these sections:\n"
        "1. **Overview** - one paragraph on overall model quality given the stats "
        "(accuracy %, total errors, etc.).\n"
        "2. **Top Failure Modes** - 3-5 themes that explain most of the incorrect "
        "calls. Cite rule_ids and quote user comments where useful.\n"
        "3. **Areas for Improvement** - concrete changes to prompts, rules, or "
        "extraction logic that would reduce these errors.\n"
        "4. **Quick Wins** - the smallest changes that would clear the largest "
        "share of incorrect assessments.\n\n"
        f"STATISTICS:\n{json.dumps(stats, indent=2)}\n\n"
        f"INCORRECT ASSESSMENTS:\n{json.dumps(incorrect_payload, indent=2)}\n"
    )


# ---------------------------------------------------------------------------
# Validation report (rule_assessments) analysis
# ---------------------------------------------------------------------------

def _extract_assessments(report: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(report, list):
        return report
    analysis = report.get("analysis") or {}
    out = list(analysis.get("rule_assessments") or [])
    out.extend(analysis.get("text_page_results") or [])
    out.extend(analysis.get("visual_page_results") or [])
    return out


def _summarize_report(assessments: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = Counter(a.get("verdict", "unknown") for a in assessments)
    flagged = [a for a in assessments if a.get("verdict") in FAIL_VERDICTS and not a.get("bypass")]
    by_group = Counter(a.get("group") or "ungrouped" for a in flagged)
    total = len(assessments)
    pass_rate = (verdicts.get("pass", 0) / total * 100) if total else 0.0
    return {
        "total_rules": total,
        "verdict_counts": dict(verdicts),
        "flagged_count": len(flagged),
        "pass_rate_pct": round(pass_rate, 1),
        "flagged_by_group": dict(by_group.most_common()),
        "flagged": flagged,
    }


def _render_report_summary(s: dict[str, Any]) -> None:
    table = Table(title="Flag Summary", header_style="bold")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("Total rule assessments", str(s["total_rules"]))
    table.add_row("Pass rate", f"{s['pass_rate_pct']}%")
    table.add_row("Flagged (fail + needs_review)", str(s["flagged_count"]))
    for k, v in s["verdict_counts"].items():
        table.add_row(f"  {k}", str(v))
    console.print(table)
    if s["flagged_by_group"]:
        gt = Table(title="Flagged by Group", header_style="bold")
        gt.add_column("Group")
        gt.add_column("Count", justify="right")
        for k, v in s["flagged_by_group"].items():
            gt.add_row(k, str(v))
        console.print(gt)


def _build_report_prompt(s: dict[str, Any], source_filename: str | None) -> str:
    flagged_payload = [
        {
            "rule_id": a.get("rule_id"),
            "rule_name": a.get("rule_name"),
            "verdict": a.get("verdict"),
            "group": a.get("group"),
            "summary": a.get("summary"),
            "reasoning": a.get("reasoning"),
            "findings": a.get("findings", []),
            "page": a.get("page"),
        }
        for a in s["flagged"]
    ]
    stats = {
        "source_filename": source_filename,
        "total_rules": s["total_rules"],
        "verdict_counts": s["verdict_counts"],
        "flagged_count": s["flagged_count"],
        "pass_rate_pct": s["pass_rate_pct"],
        "flagged_by_group": s["flagged_by_group"],
    }
    return (
        "You are reviewing the results of an automated PDF validation run. "
        "Each flagged item is a rule that returned `fail` or `needs_review`. "
        "Produce concise, actionable feedback for the document author.\n\n"
        "Return Markdown with these sections:\n"
        "1. **Overview** - one paragraph on overall quality given the stats.\n"
        "2. **Top Issues** - the 3-5 most impactful flags, grouped by theme. "
        "Cite rule_id and page where useful.\n"
        "3. **Areas for Improvement** - specific changes the author should make.\n"
        "4. **Quick Wins** - low-effort fixes that would clear the most flags.\n\n"
        f"STATISTICS:\n{json.dumps(stats, indent=2)}\n\n"
        f"FLAGGED RULES:\n{json.dumps(flagged_payload, indent=2)}\n"
    )


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def _ask_claude(prompt: str, model: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AI_API_KEY")
    if not api_key:
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set in the environment.")
    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@cli.command()
def main(
    path: Path = typer.Argument(
        DEFAULT_INPUT,
        help=f"Path to feedback.json or a validation report JSON. Defaults to {DEFAULT_INPUT}.",
    ),
    out: Path = typer.Option(
        DEFAULT_OUTPUT,
        "--out",
        "-o",
        help=f"Write Claude's feedback to this Markdown file. Defaults to {DEFAULT_OUTPUT}.",
    ),
    model: str = typer.Option(
        os.environ.get("CLAUDE_TEXT_MODEL", DEFAULT_MODEL),
        "--model",
        "-m",
        help="Claude model id.",
    ),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="Print stats only; do not call Claude."),
) -> None:
    load_dotenv()
    console.print(f"[dim]Reading from:[/dim] {path}")
    payload = _load_json(path)

    if _is_feedback_records(payload):
        console.print("[bold]Detected:[/bold] feedback.json (user assessments)")
        summary = _summarize_feedback(payload)
        _render_feedback_summary(summary)
        if summary["incorrect_count"] == 0:
            console.print("[green]No incorrect assessments — nothing to analyze.[/green]")
            return
        prompt = _build_feedback_prompt(summary)
    else:
        console.print("[bold]Detected:[/bold] validation report")
        assessments = _extract_assessments(payload)
        if not assessments:
            console.print("[yellow]No rule assessments found in the file.[/yellow]")
            raise typer.Exit(code=1)
        summary = _summarize_report(assessments)
        _render_report_summary(summary)
        if summary["flagged_count"] == 0:
            console.print("[green]No flagged rules — nothing to analyze.[/green]")
            return
        source_filename = payload.get("source_filename") if isinstance(payload, dict) else None
        prompt = _build_report_prompt(summary, source_filename)

    if skip_llm:
        return

    console.print(f"\n[bold]Asking {model} for feedback...[/bold]")
    feedback = _ask_claude(prompt, model)
    console.rule("[bold]Claude Feedback[/bold]")
    console.print(feedback)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(feedback, encoding="utf-8")
    console.print(f"\n[green]Saved feedback to {out}[/green]")


if __name__ == "__main__":
    try:
        cli()
    except typer.BadParameter as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(2)
