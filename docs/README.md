# Petra Vision Documentation

Petra Vision is an AI-powered document validation and audit tool. It validates PDF financial statements (text extraction and computer vision) and capital-event Excel workbooks (deterministic checks plus LLM judgment) against configurable rules, then generates structured audit reports.

## Table of Contents

- [Getting Started](getting-started.md) - Prerequisites, setup, and running locally
- [Architecture](architecture.md) - System design, tech stack, and folder structure
- [API Reference](api-reference.md) - REST endpoints, request/response formats
- [Authentication](authentication.md) - Microsoft Entra ID integration
- [Rule System](rules.md) - Validation rules, schema, and customization
- [Validation Pipeline](pipeline.md) - PDF extraction, text analysis, vision analysis
- [Document Types](document-types.md) - Choosing a document type; the capital-event workbook pipeline
- [AI Models](providers.md) - Claude models, effort levels, per-rule overrides
- [Comparing Models](model-comparison.md) - Measure a model or effort change before making it
- [Frontend](frontend.md) - React UI components and workflows
- [Deployment](deployment.md) - Docker, Azure Container Apps, infrastructure
- [Configuration](configuration.md) - Environment variables and app.yaml reference

## Quick Links

| Task | Guide |
|------|-------|
| Run locally for the first time | [Getting Started](getting-started.md) |
| Add or modify a validation rule | [Rule System](rules.md) |
| Validate a capital-event workbook | [Document Types](document-types.md) |
| Change the model or effort for a rule or a call type | [AI Models](providers.md) |
| Compare models or effort levels on labelled documents | [Comparing Models](model-comparison.md) |
| Deploy to Azure | [Deployment](deployment.md) |
| Understand the API | [API Reference](api-reference.md) |
| Configure authentication | [Authentication](authentication.md) |
