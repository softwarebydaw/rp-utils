#!/usr/bin/env bash
# Prompt for the values replace_pdf.py needs, then run it.
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
  p="${p#"${p%%[![:space:]]*}"}"   # trim leading whitespace
  p="${p%"${p##*[![:space:]]}"}"   # trim trailing whitespace
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

yes_no() {  # yes_no "Prompt" default(y|n)
  local a
  IFS= read -r -p "$1" a
  a="${a:-$2}"
  [[ "$a" =~ ^[Yy] ]]
}

# --- prompts --------------------------------------------------------------
echo
echo "PDF find and replace"
echo "Tip: drag a file from Finder into this window to paste its path."
echo

while :; do
  ask "Input PDF: " raw
  IN="$(clean_path "$raw")"
  [[ -f "$IN" ]] && break
  echo "  Not found: $IN"
done

DEFAULT_OUT="${IN%.[pP][dD][fF]}-edited.pdf"
ask "Output PDF [$DEFAULT_OUT]: " raw
OUT="$(clean_path "$raw")"
OUT="${OUT:-$DEFAULT_OUT}"

while :; do
  ask "Text to find: " OLD
  [[ -n "$OLD" ]] && break
  echo "  Cannot be empty."
done

ask "Replace with (blank = remove): " NEW

EXTRA=()
if yes_no "Ignore upper/lower case? [y/N]: " n; then EXTRA+=(--ignore-case); fi
if yes_no "Shrink new text if longer than old? [y/N]: " n; then EXTRA+=(--fit); fi

ask "Font file for new text (blank = Helvetica): " raw
FONT="$(clean_path "$raw")"
[[ -n "$FONT" ]] && EXTRA+=(--font-file "$FONT")

# --- confirm --------------------------------------------------------------
echo
echo "  Input : $IN"
echo "  Output: $OUT"
echo "  Find  : [$OLD]"
echo "  With  : [$NEW]"
[[ ${#EXTRA[@]} -gt 0 ]] && echo "  Opts  : ${EXTRA[*]}"
echo

if [[ -e "$OUT" ]] && ! yes_no "Output exists. Overwrite? [y/N]: " n; then
  echo "Cancelled."; exit 1
fi

echo "Matches:"
"$PY" "$HERE/replace_pdf.py" "$IN" "$OUT" "$OLD" "$NEW" --dry-run ${EXTRA[@]+"${EXTRA[@]}"} || exit 1

if yes_no "Write the new PDF? [Y/n]: " y; then
  "$PY" "$HERE/replace_pdf.py" "$IN" "$OUT" "$OLD" "$NEW" ${EXTRA[@]+"${EXTRA[@]}"}
  if [[ "$(uname)" == "Darwin" ]] && yes_no "Open it now? [y/N]: " n; then
    open "$OUT"
  fi
else
  echo "Cancelled."
fi
