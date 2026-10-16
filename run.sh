#!/usr/bin/env bash
# Start the dev server. No API keys, no network required.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "No interpreter at $PY — create one with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

exec "$PY" -m uvicorn app.main:app --reload --port "${PORT:-8000}"
