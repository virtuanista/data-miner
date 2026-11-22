#!/usr/bin/env sh
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$DIR/.venv/Scripts/python.exe" ]; then PY="$DIR/.venv/Scripts/python.exe"; else PY="$DIR/.venv/bin/python"; fi
cd "$DIR" && exec "$PY" -m dataminer "$@"
