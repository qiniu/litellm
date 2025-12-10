# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Installation
- `make install-dev` - Install core development dependencies
- `make install-proxy-dev` - Install proxy development dependencies with full feature set
- `make install-test-deps` - Install all test dependencies (includes proxy-dev, pytest plugins, and enterprise packages)
- `make install-dev-ci` - CI-compatible install (pins OpenAI to 2.8.0)

### Testing
- `make test` - Run all tests directly via pytest
- `make test-unit` - Run unit tests (tests/test_litellm) with 4 parallel workers via pytest-xdist
- `make test-integration` - Run integration tests (excludes test_litellm directory)
- `poetry run pytest tests/path/to/test_file.py -v` - Run specific test file
- `poetry run pytest tests/path/to/test_file.py::test_function -v` - Run specific test function
- `make test-unit-helm` - Run Helm chart unit tests

### Code Quality
- `make lint` - Run all linting (Ruff, MyPy, Black check, circular imports, import safety)
- `make format` - Apply Black code formatting (auto-fixes issues)
- `make format-check` - Check Black formatting without changes (matches CI)
- `make lint-ruff` - Run Ruff linting only
- `make lint-mypy` - Run MyPy type checking only (auto-installs type stubs)
- `make check-circular-imports` - Check for circular imports
- `make check-import-safety` - Verify `from litellm import *` works

### Running the Proxy
- `poetry run litellm` - Start proxy server (uses default config)
- `poetry run litellm --config config.yaml` - Start with custom config
- `python litellm/proxy_cli.py` - Start proxy backend directly
- `docker compose up` - Start full stack (proxy + PostgreSQL + Prometheus)

## Pull Request Requirements

Before submitting a PR, ensure:

1. **Add at least 1 test** - Hard requirement, add mocked tests to `tests/test_litellm/`
2. **Run `make format`** - Auto-fix code formatting
3. **Run `make lint`** - Pass all linting checks (Black, Ruff, MyPy, circular imports)
4. **Run `make test-unit`** - Pass all unit tests with 4 parallel workers
5. **Sign the CLA** - Contributor License Agreement required before merge
6. **Keep scope isolated** - Each PR should solve 1 specific problem

Test file naming follows the source structure:
- `litellm/proxy/caching_routes.py` → `tests/test_litellm/proxy/test_caching_routes.py`
- `litellm/utils.py` → `tests/test_litellm/test_utils.py`

## Architecture Overview

LiteLLM is a unified interface for 100+ LLM providers with two main components:

### Core Library (`litellm/`)
- **Main entry point**: `litellm/main.py` - Contains core `completion()`, `acompletion()`, `embedding()` functions
- **Provider implementations**: `litellm/llms/` - Each provider has its own subdirectory (e.g., `openai/`, `anthropic/`, `bedrock/`)
- **Router system**: `litellm/router.py` + `litellm/router_utils/` - Load balancing, fallback logic, retry handling
- **Type definitions**: `litellm/types/` - Pydantic v2 models for request/response validation
- **Integrations**: `litellm/integrations/` - Third-party observability (Langfuse, MLflow), caching, logging callbacks
- **Caching**: `litellm/caching/` - Multiple backends (Redis, in-memory, S3, disk cache)
- **Logging**: `litellm/_logging.py` - Central logging and callback system
- **Exception handling**: `litellm/exceptions.py` - OpenAI-compatible error mappings

### Proxy Server (`litellm/proxy/`)
- **Main server**: `proxy_server.py` - FastAPI application with `/chat/completions`, `/embeddings`, `/images/generations` endpoints
- **Authentication**: `auth/` - API key management, JWT, OAuth2, SSO (via fastapi-sso)
- **Database**: `db/` - Prisma ORM with PostgreSQL/SQLite support, schema in `schema.prisma`
- **Management endpoints**: `management_endpoints/` - Admin APIs for keys, teams, models, users
- **Pass-through endpoints**: `pass_through_endpoints/` - Provider-specific API forwarding (e.g., `/anthropic/*`, `/vertex_ai/*`)
- **Guardrails**: `guardrails/` - Pre/post-call hooks for content filtering and validation
- **Hooks**: `hooks/` - Custom lifecycle hooks for logging, auth, and request modification
- **UI Dashboard**: Served from `_experimental/out/` (Next.js static build), accessible at `/ui`
- **Health checks**: `health_check.py` - `/health/liveliness` and `/health/readiness` endpoints
- **Middleware**: `middleware/` - Request/response processing, CORS, rate limiting

## Key Patterns

### Provider Implementation
Each LLM provider follows a standard pattern:
- **Base classes**: Inherit from `litellm/llms/base.py` for common functionality
- **Transformation functions**: Convert between provider-specific and OpenAI-compatible formats
  - Input: `litellm/llms/<provider>/<provider>.py` contains `validate_environment()` and transformation logic
  - Output: Map provider responses to OpenAI `ChatCompletionResponse` format
- **Async support**: All providers implement both sync and async (`acompletion()`) operations
- **Streaming**: Providers handle streaming responses via generator/async generator patterns
- **Function calling**: Providers map their tool/function calling formats to OpenAI's format

### Request Flow
1. **User calls** `completion(model="provider/model-name", messages=[...])`
2. **Router** (if used) selects deployment based on load balancing/fallback rules
3. **Provider handler** in `litellm/llms/<provider>/` validates environment and transforms input
4. **HTTP client** (`httpx`) sends request to provider API
5. **Response handler** transforms provider output to OpenAI format
6. **Callbacks** fire for logging/observability (via `litellm/_logging.py`)
7. **Return** standardized OpenAI-compatible response

### Error Handling
- **Provider exceptions**: Mapped to OpenAI-compatible errors in `litellm/exceptions.py`
- **Fallback logic**: Router system automatically retries with fallback models
- **Logging**: All errors logged via `litellm/_logging.py` callback system
- **Timeouts**: Configurable per-request via `timeout` parameter

### Configuration
- **Proxy YAML config**: Define models, API keys, routing rules in `proxy/example_config_yaml/`
  - Models configured with `model_list` array
  - Litellm settings in `litellm_settings`
  - Guardrails and routing in separate sections
- **Environment variables**: API keys typically loaded via `.env` file (e.g., `OPENAI_API_KEY`)
- **Database schema**: Managed via Prisma in `litellm/proxy/schema.prisma`
  - Run migrations: `cd litellm/proxy && prisma migrate dev`
  - Generate client: `cd litellm/proxy && prisma generate`

## Development Notes

### Code Style
- **Black** for code formatting (line length 88, Google Python Style Guide)
- **Ruff** for fast linting (replaces flake8, isort, pyupgrade)
- **MyPy** for type checking with Pydantic plugin
- **Pydantic v2** for all data validation and type coercion
- **Async/await** patterns throughout (uses `httpx` for async HTTP)
- **Type hints** required for all public APIs

### Testing Strategy
- **Unit tests**: `tests/test_litellm/` - Mocked tests, no real API calls, mirrors `litellm/` structure
- **Integration tests**: `tests/llm_translation/` - Real API calls to test provider integrations
- **Proxy tests**: `tests/proxy_unit_tests/` - Proxy-specific unit tests
- **Load tests**: `tests/load_tests/` - Performance and stress testing
- **Parallel execution**: Unit tests run with 4 workers via `pytest-xdist` for speed

### Database Migrations (Prisma)
```bash
cd litellm/proxy
prisma migrate dev --name your_migration_name  # Creates and applies migration
prisma generate                                  # Regenerates Python client
prisma db push                                   # Push schema without creating migration
```
- Schema file: `litellm/proxy/schema.prisma`
- Test migrations against both PostgreSQL and SQLite
- Migrations stored in `litellm/proxy/migrations/`

### Enterprise Features
- **Location**: `enterprise/` directory (separate package: `litellm-enterprise`)
- **Installation**: Automatically included with `make install-test-deps`
- **Optional features**: SSO, advanced auth, email notifications, secret detection
- **Environment variables**: Enable features via env vars (e.g., `LITELLM_LICENSE`)

### Docker Development
- **Dockerfiles**: Multiple variants in `docker/` directory
  - `docker/Dockerfile.non_root` - Non-root user (recommended)
  - `docker/Dockerfile` - Standard build
- **Docker Compose**: `docker-compose.yml` sets up proxy + PostgreSQL + Prometheus
- **Services**:
  - `litellm` - Proxy server on port 4000
  - `db` - PostgreSQL on port 5432
  - `prometheus` - Metrics on port 9090

### Poetry Dependency Management
- **Core deps**: Listed in `[tool.poetry.dependencies]`
- **Optional extras**: `[tool.poetry.extras]` - install with `poetry install --extras proxy`
- **Dev deps**: `[tool.poetry.group.dev.dependencies]`
- **Version**: Managed by commitizen in `pyproject.toml`