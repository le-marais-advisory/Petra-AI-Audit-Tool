"""Compare models and effort levels on labelled documents.

    # what would run, and a cost estimate from earlier comparisons (no API calls)
    python -m src.evaluation.llm_compare run
    # run it: every case x variant x repeat, then write results.json and report.html
    python -m src.evaluation.llm_compare run --variant current --variant sonnet-5-5-medium --repeats 3 --yes
    # a local suite of real documents (keep it, and the documents, in temp/)
    python -m src.evaluation.llm_compare run --suite temp/my_suite.yaml --yes
    # re-render, or merge comparisons run separately (e.g. a variant added later)
    python -m src.evaluation.llm_compare report data/llm_compare/<run> [data/llm_compare/<other run> ...]
    # turn a run's verdicts into expected-verdict blocks to review and paste into a suite
    python -m src.evaluation.llm_compare bootstrap data/llm_compare/<run> --variant current

Live runs call the Claude API and cost money; ``run`` only does so with ``--yes``.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

from src.evaluation.llm_compare.report import merge_results, render_report
from src.evaluation.llm_compare.runner import RESULTS_FILE, run_comparison
from src.evaluation.llm_compare.suite import REPO_ROOT, load_suite
from src.evaluation.llm_compare.variants import load_variants

DEFAULT_SUITE = REPO_ROOT / "evals" / "llm_suite.yaml"
DEFAULT_VARIANTS = REPO_ROOT / "evals" / "llm_variants.yaml"
DEFAULT_OUT = REPO_ROOT / "data" / "llm_compare"


def _previous_costs(out_root: Path) -> dict[tuple[str, str], list[float]]:
    """Mean cost per run by (variant, case) across earlier comparisons, for the estimate."""
    costs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for path in sorted(out_root.glob(f"*/{RESULTS_FILE}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for run in data.get("runs", []):
            cost = (run.get("usage") or {}).get("totals", {}).get("cost_usd")
            if cost is not None and run.get("status") == "ok":
                costs[(run["variant"], run["case"])].append(cost)
    return costs


def _estimate(cases, variants, repeats, out_root: Path) -> tuple[float | None, int]:
    """(estimated USD, runs with no earlier measurement). A variant never run before is estimated from any
    earlier run of the same case."""
    history = _previous_costs(out_root)
    by_case: dict[str, list[float]] = defaultdict(list)
    for (_, case_id), values in history.items():
        by_case[case_id].extend(values)
    total, unknown = 0.0, 0
    for variant in variants:
        for case in cases:
            values = history.get((variant.name, case.id)) or by_case.get(case.id)
            if values:
                total += statistics.mean(values) * repeats
            else:
                unknown += repeats
    return (total if total else None), unknown


def cmd_run(args: argparse.Namespace) -> int:
    cases = load_suite(args.suite)
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]
    baseline, all_variants = load_variants(args.variants)
    names = args.variant or list(all_variants)
    unknown = [n for n in names if n not in all_variants]
    if unknown:
        print(f"Unknown variant(s): {', '.join(unknown)}. Defined: {', '.join(all_variants)}", file=sys.stderr)
        return 2
    if baseline not in names:
        names = [baseline] + names  # agreement is measured against it
    variants = [all_variants[n] for n in names]
    out_root = Path(args.out)
    runs = len(cases) * len(variants) * args.repeats
    estimate, unmeasured = _estimate(cases, variants, args.repeats, out_root)
    print(f"Suite {args.suite}: {len(cases)} case(s): {', '.join(c.id for c in cases)}")
    print(f"Variants: {', '.join(v.name for v in variants)} (baseline {baseline}); repeats {args.repeats}")
    print(f"Planned: {runs} validation run(s), {'warm' if args.warm_cache else 'cold'} prompt cache"
          + (f"; rules limited to {', '.join(args.rule)}" if args.rule else ""))
    if estimate is not None:
        note = f" plus {unmeasured} run(s) with no earlier measurement" if unmeasured else ""
        print(f"Estimated cost from earlier comparisons: ~${estimate:.2f}{note}")
    else:
        print("No earlier comparison to estimate cost from.")
    if not args.yes:
        print("Dry run: nothing was sent. Re-run with --yes to call the API.")
        return 0
    out_dir = out_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    results = run_comparison(cases, variants, baseline, args.repeats, out_dir,
                             rule_filter=set(args.rule) if args.rule else None, warm_cache=args.warm_cache,
                             suite_path=str(args.suite))
    report = render_report(results, out_dir / "report.html")
    print(f"Results: {out_dir / RESULTS_FILE}\nReport:  {report}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    parts = [json.loads((Path(d) / RESULTS_FILE).read_text(encoding="utf-8")) for d in args.run_dirs]
    results = merge_results(parts)
    out = Path(args.out) if args.out else Path(args.run_dirs[0]) / ("report-merged.html" if len(parts) > 1 else "report.html")
    print(render_report(results, out))
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    results = json.loads((Path(args.run_dir) / RESULTS_FILE).read_text(encoding="utf-8"))
    verdicts: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for run in results["runs"]:
        if run["variant"] == args.variant and run["status"] == "ok":
            for rule_id, r in run["rules"].items():
                verdicts[run["case"]][rule_id].append(r["verdict"])
    if not verdicts:
        print(f"No successful runs of variant {args.variant!r} in {args.run_dir}", file=sys.stderr)
        return 2
    blocks = []
    for case_id, by_rule in verdicts.items():
        expected = {rule_id: Counter(vs).most_common(1)[0][0] for rule_id, vs in sorted(by_rule.items())}
        unstable = sorted(r for r, vs in by_rule.items() if len(set(vs)) > 1)
        blocks.append({"id": case_id, "expected": expected, **({"unstable": unstable} if unstable else {})})
    print("# Review every verdict before using these as expectations; 'unstable' rules disagreed across repeats.")
    print(yaml.safe_dump({"cases": blocks}, sort_keys=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.evaluation.llm_compare", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the suite under each variant (dry run without --yes)")
    run.add_argument("--suite", default=str(DEFAULT_SUITE))
    run.add_argument("--variants", default=str(DEFAULT_VARIANTS))
    run.add_argument("--variant", action="append", help="variant to run (repeatable; default all)")
    run.add_argument("--case", action="append", help="case id to run (repeatable; default all)")
    run.add_argument("--rule", action="append", help="rule id to evaluate (repeatable; default all)")
    run.add_argument("--repeats", type=int, default=1, help="runs per case and variant (3 to measure stability)")
    run.add_argument("--warm-cache", action="store_true", help="let runs share prompt caches and layouts")
    run.add_argument("--out", default=str(DEFAULT_OUT))
    run.add_argument("--yes", action="store_true", help="call the API (costs money)")
    run.set_defaults(func=cmd_run)
    report = sub.add_parser("report", help="re-render a report, merging several runs")
    report.add_argument("run_dirs", nargs="+")
    report.add_argument("--out")
    report.set_defaults(func=cmd_report)
    boot = sub.add_parser("bootstrap", help="print expected-verdict blocks from a run's majority verdicts")
    boot.add_argument("run_dir")
    boot.add_argument("--variant", required=True)
    boot.set_defaults(func=cmd_bootstrap)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
