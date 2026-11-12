#!/bin/zsh
set -euo pipefail
cd -- "${0:A:h}"
applydesk_python="${APPLYDESK_PYTHON:-$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3}"
if [[ ! -x "$applydesk_python" ]]; then
  print "The bundled Python runtime is missing. See README.md for setup instructions."
  read "?Press Return to close."
  exit 1
fi
exec "$applydesk_python" run.py "$@"
