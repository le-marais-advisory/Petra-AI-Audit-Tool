# Validation Pipeline

The validation pipeline is the core of Petra Vision. It takes a PDF and a set of rules, then produces a structured analysis result through multiple stages.

## Pipeline Overview

```
PDF File + Selected Rules
         |
         v
  +------------------+
  | 1. PDF Extraction |  (pdfplumber)
  |    - Text         |
  |    - Tables       |
  |    - Char counts  |
  +--------+---------+
           |
     +-----+------+
     |             |
     v             v
+----------+  +-----------+
| 2. Text  |  | 3. Page   |
|  Rule    |  |  Rendering|  (PyMuPDF)
| Analysis |  |  to Images|
| (LLM)   |  +-----+-----+
+----+-----+        |
     |               v
     |        +-----------+
     |        | 4. Vision |
     |        |  Rule     |
     |        | Analysis  |
     |        | (LLM)     |
     |        +-----+-----+
     |              |
     +------+-------+
            |
            v
   +------------------+
   | 5. Result        |
   |    Aggregation   |
   +------------------+
            |
            v
  DocumentValidationResponse
```

## Stage 1: PDF Extraction

**Module:** `src/pipeline/pdf_extractor.py` (`PdfExtractor`)

Uses **pdfplumber** to extract structured content from each page:

- **Text extraction** with layout preservation (`text_layout: true` in `app.yaml`)
- **Table detection** using line intersection analysis
- **Character counting** for each page
- Configurable text density via `text_x_density` and `text_y_density` settings

**Output:** A list of page dictionaries, each containing:
- `page` - 1-based page number
- `text` - Raw extracted text
- `tables` - List of tables, each with `index` and `rows`
- `char_count` - Character count of extracted text

## Stage 2: Text Rule Analysis

**Module:** `src/pipeline/text_rule_analyzer.py` (`TextRuleAnalyzer`)

Analyzes extracted text and tables against text-type rules using an LLM:

1. Filters selected rules to `analysis_type: "text"` only, then splits them by scope:
   `page` rules evaluate one page at a time, while `multi_page` and `document` rules
   evaluate whole sections or the whole document in a single call
2. Classifies every rule up front, single-threaded — which pages a page rule applies to
   (`page_classifier.rule_applies_to_page`), and which sections a broad-scope rule
   gathers. Rules with nothing to evaluate are marked `not_applicable` here and never
   reach the LLM
3. Submits all remaining work to **one thread pool**, broad-scope calls first: they carry
   the most content and take longest, so starting them early keeps them off the tail.
   Pool size is `pipeline.concurrent_requests` (see `docs/configuration.md`)
4. Uses a system prompt from `config/text_analysis_system_prompt.md`. Broad-scope
   payloads are prefixed with a `CONTENT SCOPE` note stating whether they carry the
   whole document or only the sections the rule covers, and warning that the
   `<page number="...">` tags are physical file positions rather than printed page
   numbers. Without it these rules inherit the prompt's single-page framing and hedge
   to `needs_review` on content they wrongly believe was withheld
5. The LLM evaluates each rule and returns structured JSON with verdicts, findings, and citations
6. Page-scope results are aggregated per rule; broad-scope results are committed directly
   (they carry their own `scope` and `matched_pages`) alongside one synthetic page result
   attributed to the first gathered page

The LLM provider (OpenAI or Claude) is determined by the `TEXT_PROVIDER` environment variable.
Provider clients are configured with a 180s timeout and 2 SDK retries; the SDK's own
429/5xx handling (which honours `retry-after`) is the rate-limit defence.

**Output:**
- `rule_results` - Dict of rule ID to aggregated `RuleAssessmentSchema`
- `page_results` - List of `PageRuleAssessmentSchema` (per-page, per-rule results)

## Stage 3: Page Rendering

**Module:** `src/pipeline/pdf_renderer.py`

Renders each PDF page to a high-resolution image using **PyMuPDF (fitz)**:

- Configurable DPI (default: 300, set in `config/app.yaml`)
- Output format: PNG or JPEG (configurable)
- Images are saved as temporary files in the working directory

These images are used as input for vision rule analysis.

## Stage 4: Vision Rule Analysis

**Module:** `src/pipeline/vision_rule_analyzer.py` (`VisionRuleAnalyzer`)

Analyzes rendered page images against vision-type rules using an LLM with vision capabilities:

1. Filters selected rules to `analysis_type: "vision"` only
2. For each page image, sends the image + rule to the LLM vision model
3. Uses a system prompt from `config/vision_analysis_system_prompt.md`
4. Supports concurrent requests (configurable via `concurrent_requests` and `global_max_concurrent` in `app.yaml`)
5. The LLM evaluates visual elements and returns structured verdicts

The vision provider is determined by the `VISION_PROVIDER` environment variable.

**Output:** Same structure as text analysis — `rule_results` and `page_results`.

## Stage 5: Result Aggregation

**Module:** `src/pipeline/result_builder.py` (`build_document_result`)

Combines all outputs into a `DocumentValidationResponse`:

1. Merges text and vision rule results into a unified `rule_assessments` list
2. Collects all page-level results (`text_page_results`, `visual_page_results`)
3. Generates an `overview` with summary metrics (total rules, pass/fail counts)
4. Attaches the extracted `pages` data (text + tables)

## Orchestration

**Module:** `src/pipeline/orchestrator.py` (`ValidationPipeline`)

The `ValidationPipeline` class coordinates all stages:

```python
pipeline = ValidationPipeline(app_config=app_config, settings=settings)
result = pipeline.run(
    pdf_path="/path/to/document.pdf",
    rules=selected_rules,
    source_filename="document.pdf",
)
```

The `run()` method:
1. Generates a unique document ID
2. Calls `PdfExtractor.extract()` for text/tables
3. Calls `TextRuleAnalyzer.analyze()` for text rules
4. Calls `VisionRuleAnalyzer.analyze()` for vision rules
5. Merges results via `build_rule_assessments()` and `build_document_result()`
6. Returns the complete result dict

## Configuration

Pipeline behavior is controlled by `config/app.yaml`:

```yaml
pdf:
  dpi: 300                 # Image rendering resolution
  image_format: "png"      # Image output format
  text_layout: true        # Preserve text layout during extraction
  text_x_density: 7.25     # Horizontal text density for layout
  text_y_density: 13.0     # Vertical text density for layout

vision:
  max_images_per_request: 10     # Max images per LLM call
  temperature: 0.1               # LLM temperature for vision
  seed: 42                        # Reproducibility seed
  max_completion_tokens: 1600     # Max tokens in LLM response
  image_detail: "high"            # Image detail level
  concurrent_requests: 12         # Concurrent vision requests per rule
  global_max_concurrent: 24       # Global concurrency cap
```

## Key Files

- `src/pipeline/orchestrator.py` - Pipeline coordination
- `src/pipeline/pdf_extractor.py` - PDF text/table extraction
- `src/pipeline/pdf_renderer.py` - PDF page rendering
- `src/pipeline/text_rule_analyzer.py` - Text rule LLM analysis
- `src/pipeline/vision_rule_analyzer.py` - Vision rule LLM analysis
- `src/pipeline/result_builder.py` - Result aggregation
- `config/text_analysis_system_prompt.md` - Text analysis system prompt
- `config/vision_analysis_system_prompt.md` - Vision analysis system prompt
