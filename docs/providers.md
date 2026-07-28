# AI Providers

Petra Vision supports two AI providers for document analysis: **OpenAI** and **Anthropic (Claude)**. Each provider can be configured independently for text analysis and vision analysis.

## Provider Architecture

The provider system uses an abstract base class pattern:

```
providers/
  text/
    base.py       # TextAnalysisProvider (abstract)
    openai.py     # OpenAI GPT implementation
    claude.py     # Anthropic Claude implementation
    factory.py    # Factory for instantiating text provider
  vision/
    base.py       # VisionProvider (abstract)
    openai.py     # OpenAI vision implementation
    claude.py     # Claude vision implementation
    factory.py    # Factory for instantiating vision provider
```

The factory pattern selects the provider based on the `TEXT_PROVIDER` and `VISION_PROVIDER` environment variables.

## Switching Providers

Set these environment variables in your `.env` file:

```env
# Use OpenAI for both text and vision
TEXT_PROVIDER=openai
VISION_PROVIDER=openai

# Use Claude for both
TEXT_PROVIDER=claude
VISION_PROVIDER=claude

# Mix: Claude for text, OpenAI for vision
TEXT_PROVIDER=claude
VISION_PROVIDER=openai
```

Provider aliases are accepted: `"openai"`, `"open ai"` map to OpenAI; `"claude"`, `"anthropic"` map to Claude.

## OpenAI Configuration

### API Key

```env
OPENAI_API_KEY=sk-...
```

The key also accepts the alias `OPEN_AI_API_KEY`.

### Models

```env
OPENAI_TEXT_MODEL=gpt-5.4-mini       # Default text model
OPENAI_VISION_MODEL=gpt-5.4          # Default vision model (falls back to text model)
```

### Parameters

```env
OPENAI_TEXT_TEMPERATURE=              # Optional, uses provider default
OPENAI_TEXT_MAX_COMPLETION_TOKENS=    # Optional, uses provider default
```

Vision model parameters are configured in `config/app.yaml` under the `vision` section.

### Behavior

- Text analysis: sends extracted text + rule as a structured prompt, expects JSON response
- Vision analysis: sends page images as base64-encoded data URLs or HTTP URLs
- Uses JSON schema enforcement for structured output
- Retry logic with exponential backoff (via `tenacity`)

## Claude (Anthropic) Configuration

### API Key

```env
ANTHROPIC_API_KEY=sk-ant-...
```

Also accepts aliases `ANTHROPIC_AI_API_KEY` and `ANTROPIC_AI_API_KEY`.

### Models

```env
CLAUDE_TEXT_MODEL=claude-sonnet-5         # Default text model
CLAUDE_VISION_MODEL=claude-sonnet-5       # Default vision model (falls back to text model)
```

### Parameters

```env
CLAUDE_TEXT_TEMPERATURE=           # Optional — leave unset on Sonnet 5 (see below)
CLAUDE_VISION_TEMPERATURE=         # Optional — leave unset on Sonnet 5 (see below)
CLAUDE_TEXT_MAX_TOKENS=1600        # Max tokens for text analysis
CLAUDE_VISION_MAX_TOKENS=1600     # Max tokens for vision analysis
```

Both temperature settings are unset by default and are omitted from the request
when unset. Leave them unset unless you have pointed `CLAUDE_TEXT_MODEL` /
`CLAUDE_VISION_MODEL` at an older model that accepts sampling parameters —
Claude Sonnet 5 rejects a non-default `temperature` with a 400, so setting
either one will fail every analysis call. Steer the model through the system
prompts in `config/` instead.

### Behavior

- Text analysis: uses the Anthropic messages API with system prompts
- Vision analysis: sends page images as base64-encoded media blocks
- Supports image formats: JPEG, PNG, GIF, WebP
- Concurrency controlled via semaphores
- Retry logic with exponential backoff

## Provider Interface

Both text and vision providers implement a common evaluation method:

### Text Provider

```python
class TextAnalysisProvider(ABC):
    @abstractmethod
    def evaluate_rule(
        self,
        document_content: str,
        rule: dict,
        system_prompt: str,
    ) -> dict:
        """Evaluate a single rule against document text content."""
```

### Vision Provider

```python
class VisionProvider(ABC):
    @abstractmethod
    def evaluate_rule(
        self,
        page_image: str,  # base64 data URL or file path
        rule: dict,
        system_prompt: str,
    ) -> dict:
        """Evaluate a single rule against a page image."""
```

Both return a structured dict matching the `RuleAssessmentSchema` format (verdict, summary, findings, citations, etc.).

## Retry and Error Handling

Both providers use `tenacity` for retry logic:

- Exponential backoff on transient failures (rate limits, server errors)
- Configurable retry count
- Structured error responses when all retries are exhausted

## Vision-Specific Configuration

Additional vision settings in `config/app.yaml`:

```yaml
vision:
  provider: "openai"              # Default vision provider
  model_id: "gpt-5.4"            # Default vision model
  max_images_per_request: 10     # Max images per single LLM request
  temperature: 0.1               # Low temperature for consistency
  seed: 42                        # Seed for reproducibility
  max_completion_tokens: 1600     # Max response tokens
  image_detail: "high"            # Image detail level (OpenAI-specific)
  concurrent_requests: 12         # Per-rule concurrent requests
  global_max_concurrent: 24       # Global concurrent request cap
```

## Key Files

- `src/providers/text/base.py` - Text provider abstract base
- `src/providers/text/openai.py` - OpenAI text implementation
- `src/providers/text/claude.py` - Claude text implementation
- `src/providers/text/factory.py` - Text provider factory
- `src/providers/vision/base.py` - Vision provider abstract base
- `src/providers/vision/openai.py` - OpenAI vision implementation
- `src/providers/vision/claude.py` - Claude vision implementation
- `src/providers/vision/factory.py` - Vision provider factory
- `src/providers/analysis_result.py` - JSON schema and payload utilities
