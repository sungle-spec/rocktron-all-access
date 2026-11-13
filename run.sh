#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# A failed or stale installation must be retried on the next launch.
if [ ! -f .venv/.requirements-installed ] || [ requirements.txt -nt .venv/.requirements-installed ]; then
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.requirements-installed
fi
exec .venv/bin/python app.py "$@"
