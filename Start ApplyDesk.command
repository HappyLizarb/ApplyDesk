#!/bin/zsh -f
set -eu
program="${0:A:h}"
resources="${program:h}"
if [[ -f "$resources/../Info.plist" && -x "$resources/Runtime/python/bin/python3" ]]; then
  interpreter="$resources/Runtime/python/bin/python3"
  export APPLYDESK_SOFFICE="$resources/Runtime/LibreOffice.app/Contents/MacOS/soffice"
  export APPLYDESK_PDFTOPPM="$resources/pdf-preview"
  export APPLYDESK_LAUNCHER="$resources/../MacOS/ApplyDesk"
else
  interpreter="${APPLYDESK_PYTHON:-$program/.venv/bin/python3}"
  if [[ ! -x "$interpreter" ]]; then
    print "Set up the Python environment as described in README.md."
    exit 1
  fi
fi
cd -- "${TMPDIR:-/tmp}"
exec "$interpreter" -E -s -B "$program/run.py" "$@"
