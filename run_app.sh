#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
chmod +x "$HERE/run_app.sh" >/dev/null 2>&1 || true
exec streamlit run app.py "$@"
