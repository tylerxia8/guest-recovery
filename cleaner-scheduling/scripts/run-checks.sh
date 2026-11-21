#!/usr/bin/env bash
# Local equivalent of the CI checks. Requires Postgres, Redis, uv, Node, and npm.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"

cd "${ROOT}/backend"
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest

cd "${ROOT}/frontend"
npm run typecheck
npm run lint
npm run test:e2e
