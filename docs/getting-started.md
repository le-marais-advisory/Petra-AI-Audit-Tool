# Getting Started

## Prerequisites

- **Python 3.11+** (backend)
- **Node.js 18+** and **npm** (frontend)
- **Docker** and **Docker Compose** (recommended for local dev)
- An **Anthropic** API key (for AI-powered analysis)

## Quick Start with Docker Compose

The fastest way to run the full stack locally:

1. **Clone the repository** and navigate to the project root:

   ```bash
   cd petra-data-file
   ```

2. **Create your `.env` file** from the template:

   ```bash
   cp env.example .env
   ```

   Edit `.env` and set at minimum:

   ```env
   ANTHROPIC_API_KEY=sk-ant-...
   AUTH_ENABLED=false            # disable auth for local dev
   ```

3. **Start the services**:

   ```bash
   docker compose up --build
   ```

   This starts:
   - **Backend API** on `http://localhost:8000`
   - **Frontend** on `http://localhost:5173`

4. **Verify** the setup:

   ```bash
   curl http://localhost:8000/api/v1/health
   # {"status":"healthy"}
   ```

   Open `http://localhost:5173` in your browser to access the UI.

## Running Without Docker

### Backend

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create `.env` from the template and configure it:

   ```bash
   cp env.example .env
   ```

4. Start the API server:

   ```bash
   python -m src.main serve
   ```

   The API starts on `http://localhost:8000` by default.

### Frontend

1. Navigate to the frontend directory:

   ```bash
   cd frontend
   ```

2. Install dependencies:

   ```bash
   npm install
   ```

3. Create `.env` from the template:

   ```bash
   cp .env.example .env
   ```

   The defaults point to `http://localhost:8000` for the API and disable auth.

4. Start the dev server:

   ```bash
   npm run dev
   ```

   The frontend starts on `http://localhost:5173`.

## First Validation

1. Open `http://localhost:5173` in your browser.
2. The rules sidebar loads available validation rules from the API.
3. Select one or more rules (e.g., "Whole-Dollar Values Check", "Heading Alignment").
4. Upload a PDF document using the upload panel.
5. Wait for the analysis to complete. Results appear across the tabs:
   - **Source** - view the original PDF
   - **Extracted** - view extracted text and tables
   - **Text Analysis** - text-based rule results
   - **Visual Analysis** - vision-based rule results
6. Optionally export the analysis as a PDF report via the export button.

## Project Structure Overview

```
petra-data-file/
  src/                 # Python backend (FastAPI)
    api/               # API routers, middleware, dependencies
    core/              # Configuration, auth, logging
    pipeline/          # PDF extraction and analysis pipeline
    providers/         # Claude providers, model registry and per-call routing
    schemas/           # Pydantic request/response models
    services/          # Business logic and job management
  frontend/            # React/TypeScript frontend
    src/components/    # UI components
    src/auth/          # MSAL authentication
  config/              # app.yaml and prompt templates
  rules/               # Validation rule definitions (JSON)
  infra/               # Azure Bicep infrastructure-as-code
  docker/              # Dockerfiles
  tests/               # Test files
```

See [Architecture](architecture.md) for a detailed breakdown.
