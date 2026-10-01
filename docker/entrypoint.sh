#!/usr/bin/env bash
# Thin dispatcher over the mvault CLI.
#   brain     — serve the web UI (default)
#   pipeline  — run the full ingest->tag->faces sweep once
#   mvault …  — any raw mvault subcommand
set -euo pipefail
cd /app/scripts
PY="python3 mvault"

case "${1:-brain}" in
  brain)
    $PY init || true
    exec $PY brain --host 0.0.0.0 --port 8484
    ;;
  pipeline)
    src="${MEMORYVAULT_SOURCE:-/photos}"
    $PY init
    $PY discover "$src" --kind local
    $PY ingest
    $PY curate || true
    $PY screen || echo "screen skipped (vault unavailable)"
    $PY tag || echo "tag skipped (is Ollama up with the model pulled?)"
    $PY geocode || true
    $PY describe || true
    $PY faces scan && $PY faces cluster || true
    $PY edges || true
    echo "pipeline pass complete"
    ;;
  mvault)
    shift
    exec $PY "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
