"""Comparison suites: the labelled documents a comparison runs on.

A suite YAML lists cases. Each entry either imports labelled cases from an existing file or defines one inline:

    cases:
      - from: tests/integration/cases.yaml          # PDF cases (expected verdicts per rule, optional pages)
      - from: tests/evals/capital_event_cases.yaml  # synthetic workbooks, generated on demand
        only: [call_clean, distribution_clean]      # optional subset by case id
      - id: ftv-v-dist14                            # inline: a local real document (keep it in temp/)
        document: temp/test-run/FTV V - Distribution 14.xlsx
        document_type: capital_event_workbook
        options: {event_type: net_event}
        prior_document: temp/test-run/FTV V - Capital Call 18.xlsx
        rules: [CE-TIE-SUMMARY, CE-ITD-EVENT-BLOCK]  # optional; default every rule of the type
        expected: {CE-TIE-SUMMARY: pass, CE-ITD-EVENT-BLOCK: fail}

Synthetic workbook cases also carry the fixture's expected verdicts for the deterministic rules and its
golden layouts, so a layout or role change is scored too.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
VERDICTS = {"pass", "fail", "needs_review", "not_applicable"}


@dataclass(frozen=True)
class Check:
    """One expected verdict: for a rule, or for a rule re-aggregated over some pages."""

    rule_id: str
    verdict: str
    pages: tuple[int, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.rule_id}[p{','.join(map(str, self.pages))}]" if self.pages else self.rule_id


@dataclass
class Case:
    id: str
    document: Path
    document_type: str = "financial_statements"
    options: dict[str, Any] = field(default_factory=dict)
    prior_document: Path | None = None
    rules: list[str] | None = None  # None: every rule of the document type (and event type)
    checks: list[Check] = field(default_factory=list)
    golden_layouts: dict[str, dict] = field(default_factory=dict)  # sheet -> golden layout (synthetic only)
    source: str = "inline"


def _resolve(path: str | Path, base: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else (REPO_ROOT / path if (REPO_ROOT / path).exists() else base / path)


def _checks_from_mapping(expected: dict[str, str]) -> list[Check]:
    return [Check(rule_id, verdict) for rule_id, verdict in (expected or {}).items() if verdict is not None]


def _integration_cases(path: Path, only: set[str] | None) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = []
    for raw in data.get("cases") or []:
        if only and raw["id"] not in only:
            continue
        checks = [Check(e["rule_id"], e["verdict"], tuple(e.get("pages") or ())) for e in raw.get("expected") or []]
        cases.append(Case(id=raw["id"], document=_resolve(raw["document"], path.parent),
                          rules=list(raw.get("rules") or []) or None, checks=checks, source=str(path)))
    return cases


_FIXTURE_DIR: Path | None = None
_FIXTURE_CACHE: dict[str, Any] = {}


def _fixture(event_type: str, variant: str, defect: str | None, with_prior: bool):
    """A generated synthetic capital-event workbook (and its manifest), one temp dir per process."""
    global _FIXTURE_DIR
    from tests.fixtures.generate_capital_event_fixtures import FixtureSpec, build_fixture

    spec = FixtureSpec(event_type=event_type, variant=variant, defect=defect, with_prior=with_prior)
    if spec.fixture_id not in _FIXTURE_CACHE:
        _FIXTURE_DIR = _FIXTURE_DIR or Path(tempfile.mkdtemp(prefix="llm_compare_fixtures_"))
        _FIXTURE_CACHE[spec.fixture_id] = build_fixture(spec, _FIXTURE_DIR)
    return _FIXTURE_CACHE[spec.fixture_id]


def _workbook_fixture_cases(path: Path, only: set[str] | None) -> list[Case]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = []
    for raw in data.get("cases") or []:
        if only and raw["id"] not in only:
            continue
        fx = raw["fixture"]
        manifest = _fixture(fx["event_type"], fx.get("variant", "standard"), fx.get("defect"),
                            fx.get("with_prior", False))
        expected = {k: v for k, v in (manifest.expected_verdicts or {}).items() if v is not None}
        expected.update(raw.get("expected") or {})
        cases.append(Case(
            id=raw["id"], document=Path(manifest.path), document_type="capital_event_workbook",
            options={"event_type": fx["event_type"]},
            prior_document=Path(manifest.prior.path) if manifest.prior else None,
            checks=_checks_from_mapping(expected), golden_layouts=dict(manifest.layouts or {}), source=str(path),
        ))
    return cases


def _inline_case(raw: dict[str, Any], base: Path) -> Case:
    expected = raw.get("expected") or {}
    if isinstance(expected, list):  # the integration-file shape
        checks = [Check(e["rule_id"], e["verdict"], tuple(e.get("pages") or ())) for e in expected]
    else:
        checks = _checks_from_mapping(expected)
    return Case(
        id=raw["id"], document=_resolve(raw["document"], base),
        document_type=raw.get("document_type", "financial_statements"), options=dict(raw.get("options") or {}),
        prior_document=_resolve(raw["prior_document"], base) if raw.get("prior_document") else None,
        rules=list(raw.get("rules") or []) or None, checks=checks, source="inline",
    )


def load_suite(path: str | Path) -> list[Case]:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases: list[Case] = []
    for entry in data.get("cases") or []:
        if "from" in entry:
            source = _resolve(entry["from"], path.parent)
            only = set(entry.get("only") or []) or None
            text = source.read_text(encoding="utf-8")
            loader = _workbook_fixture_cases if "fixture:" in text else _integration_cases
            cases.extend(loader(source, only))
        else:
            cases.append(_inline_case(entry, path.parent))
    problems = [f"{c.id}: document not found ({c.document})" for c in cases if not c.document.exists()]
    problems += [f"{c.id}: unknown verdict {ch.verdict!r} for {ch.rule_id}" for c in cases for ch in c.checks
                 if ch.verdict not in VERDICTS]
    ids = [c.id for c in cases]
    problems += [f"duplicate case id {i!r}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        raise ValueError(f"Invalid suite {path}:\n  " + "\n  ".join(problems))
    return cases
