#!/bin/zsh
# Double-click in Finder: starts the bridge and opens the interface in the browser.
cd "$(dirname "$0")" || exit 1
if ! .venv/bin/python -c "import mido, rtmidi, yaml" 2>/dev/null; then
  echo "Setting up the Python environment…"
  python3 -m venv .venv && .venv/bin/python -m pip install --quiet -r requirements.txt || exit 1
fi
exec .venv/bin/python bridge.py --open
