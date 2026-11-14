# pdf-replace: find and replace text in a PDF

Replaces every occurrence of a text value in a PDF and writes a **new** PDF.
The original file is never changed.

## Quick start (prompted)

First time only:

```bash
git clone https://github.com/softwarebydaw/rp-utils.git
```

Then, from the folder you cloned into:

```bash
cd rp-utils
./pdf-replace/run.sh
```

The script asks for:

| Prompt | Notes |
|---|---|
| Input PDF | Type the path or drag the file from Finder into the terminal. Spaces, quotes and Finder's `\ ` escapes are all handled. |
| Output PDF | Press Enter to accept `<input>-edited.pdf`. |
| Text to find | Typed as-is, spaces included. No quotes needed. |
| Replace with | Typed as-is, spaces included. Blank removes the text. |
| Ignore case | Default **No**: `Smith` does not match `SMITH`. |
| Shrink new text | Default **No**. Yes lowers the font size of a longer replacement so it fits the old space. |
| Font file | Optional `.ttf`/`.otf` for the new text. Blank uses Helvetica. |

It then shows a summary, does a dry run that lists matches per page,
and asks before writing. First run creates `.venv/` and installs PyMuPDF.

## Direct use (command line)

Quote any value that contains spaces:

```bash
.venv/bin/python pdf-replace/replace_pdf.py "Client Report.pdf" "Client Report v2.pdf" "John Q. Smith" "Jane R. Smith"
```

Options:

- `--dry-run` list matches, write nothing
- `--ignore-case` match regardless of case
- `--fit` shrink new text that is wider than the old text
- `--font-file "/Library/Fonts/Arial.ttf"` draw new text in that font

Exit code is `1` when nothing matched (no file is written).

## How it works

1. Finds each match on each page with PyMuPDF.
2. Reads the size, color and baseline from the line the match is on.
3. Removes the old text with a redaction (no white box is drawn, images are untouched).
   Only the middle band of each match is redacted, so letters on tightly spaced
   lines above and below are not erased.
4. Writes the new text at the same spot.
5. Also replaces the value in document properties (title, author, subject, keywords).

## Limits

- **Font**: new text is Helvetica unless you pass a font file, so it can look
  slightly different from the surrounding text.
- **Length**: a longer replacement can run into text to its right. Nothing reflows.
  The dry run warns when this will happen; `--fit` shrinks the new text instead.
- **Line breaks**: a value split across two lines is not found.
- **Scanned PDFs**: no text layer means no matches. Run OCR first.
- **Form fields**: values inside fillable fields are not changed by this tool.
- **Password-protected PDFs** are refused. Remove the password first.

Always open the output and check it before sending it on.
