#!/usr/bin/env bash
# Prompt for what triage.py needs, then run it.
# Every value is read whole (IFS= read -r) and passed quoted, so spaces are kept.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# --- one-time setup -------------------------------------------------------
if [[ ! -x "$PY" ]]; then
  echo "First run: creating virtual environment in $VENV ..."
  python3 -m venv "$VENV"
fi
if ! "$PY" -c "import pymupdf" 2>/dev/null; then
  echo "Installing PyMuPDF ..."
  "$PY" -m pip install --quiet --upgrade pip
  "$PY" -m pip install --quiet -r "$ROOT/requirements.txt"
fi

# --- helpers --------------------------------------------------------------
# Clean a path typed or dragged in from Finder:
# trims spaces, strips surrounding quotes, turns "\ " into " ", expands ~.
clean_path() {
  local p="$1"
  p="${p#"${p%%[![:space:]]*}"}"
  p="${p%"${p##*[![:space:]]}"}"
  if [[ "$p" == \"*\" || "$p" == \'*\' ]]; then
    p="${p:1:${#p}-2}"
  fi
  p="${p//\\ / }"
  [[ "$p" == "~/"* ]] && p="$HOME/${p:2}"
  printf '%s' "$p"
}

ask() {  # ask "Prompt" varname  -> reads one whole line, spaces kept
  local __v
  IFS= read -r -e -p "$1" __v
  printf -v "$2" '%s' "$__v"
}

# --- prompts --------------------------------------------------------------
echo
echo "Illustration first-pass check (no AI)"
echo "  1) check   - report what to do with each PDF (changes nothing)"
echo "  2) record  - add PDFs to the index after they were processed"
echo "  3) list    - show what the index holds"
ask "Choose 1, 2 or 3 [1]: " choice
case "${choice:-1}" in
  1|check)  CMD=check ;;
  2|record) CMD=record ;;
  3|list)   CMD=list ;;
  *) echo "Unknown choice: $choice"; exit 1 ;;
esac

if [[ "$CMD" == list ]]; then
  exec "$PY" "$HERE/triage.py" list
fi

echo
echo "Enter PDF files or folders, one per line. Drag from Finder works."
echo "Press Enter on an empty line when done."
PATHS=()
while :; do
  ask "Path: " raw
  [[ -z "$raw" ]] && break
  p="$(clean_path "$raw")"
  if [[ -e "$p" ]]; then
    PATHS+=("$p")
  else
    echo "  Not found: $p"
  fi
done
if [[ ${#PATHS[@]} -eq 0 ]]; then
  echo "No paths given."; exit 1
fi

EXTRA=()
if [[ "$CMD" == record ]]; then
  ask "How were they processed? ai / parser / manual [ai]: " src
  EXTRA+=(--source "${src:-ai}")
fi

echo
"$PY" "$HERE/triage.py" "$CMD" "${PATHS[@]}" ${EXTRA[@]+"${EXTRA[@]}"}
