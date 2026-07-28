# Flag Analysis

A small companion tool that reads Petra flag/feedback data and asks Claude to
suggest areas of improvement. It auto-detects two input shapes:

| Input | Source | "Errors" mean |
|---|---|---|
| `feedback.json` | `/app/data/feedback.json` (written by `src/api/routers/feedback.py`) | User-marked `assessment == "incorrect"` |
| Validation report | `python -m src.main validate --out report.json` | Rules with verdict `fail` / `needs_review` |

## Conventions

By default the script reads from and writes to a folder on your Desktop:

```
~/Desktop/feedback_audit_tool/
├── feedback.json       # download from the petra-data file share
└── improvements.md     # produced by the analyzer
```

Drop `feedback.json` (downloaded from Azure Storage Explorer / Portal / CLI)
into that folder and the analyzer will pick it up.

## Usage

```powershell
# Default: reads ~/Desktop/feedback_audit_tool/feedback.json
# and writes ~/Desktop/feedback_audit_tool/improvements.md
python flag_analysis\analyze_flags.py

# Override the input path
python flag_analysis\analyze_flags.py C:\path\to\other.json

# Override the output path
python flag_analysis\analyze_flags.py --out C:\path\to\report.md

# Stats only (no LLM call)
python flag_analysis\analyze_flags.py --skip-llm
```

## Environment

- `ANTHROPIC_API_KEY` (required unless `--skip-llm`)
- `CLAUDE_TEXT_MODEL` (optional, defaults to `claude-sonnet-5`)

## Output

For `feedback.json`:
- Total records, model accuracy, count marked incorrect
- Top rules generating incorrect assessments
- Verdict / analysis-type breakdown of errors
- Markdown feedback from Claude (Overview, Top Failure Modes, Areas for
  Improvement, Quick Wins)

For a validation report:
- Total rule assessments, pass rate, flagged count by verdict and group
- Markdown feedback from Claude (Overview, Top Issues, Areas for Improvement,
  Quick Wins)
