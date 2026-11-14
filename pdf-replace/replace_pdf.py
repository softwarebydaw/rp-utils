#!/usr/bin/env python3
"""Find and replace a text value in a PDF.

Each match is removed with a redaction (no white box is painted) and the new
text is written at the same baseline, size and color as the original span.

Values with spaces must be quoted on the command line:

    python replace_pdf.py "in file.pdf" "out file.pdf" "OLD VALUE" "NEW VALUE"
"""
import argparse
import os
import re
import sys

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    try:
        import fitz  # older PyMuPDF
    except ImportError:
        sys.exit("PyMuPDF is not installed. Run: pip install -r requirements.txt")


def span_at(page, rect):
    """Return the text span on the same line as rect.

    Uses the span containing the match's center point, so a neighbouring
    line whose box merely touches the match is not picked.
    """
    center = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
    best, best_area = None, 0.0
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                box = fitz.Rect(span["bbox"])
                if box.contains(center):
                    return span
                area = abs(box & rect)
                if area > best_area:
                    best, best_area = span, area
    return best


def core_rect(rect):
    """Middle band of a match box.

    Search boxes include font ascent/descent and can overlap the lines above
    and below in tightly spaced text; redacting the full box would erase
    letters on those lines.
    """
    inset = rect.height * 0.3
    return fitz.Rect(rect.x0 + 0.5, rect.y0 + inset, rect.x1 - 0.5, rect.y1 - inset)


def int_to_rgb(c):
    return (((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255)


def text_width(text, size, fontfile=None):
    font = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font("helv")
    return font.text_length(text, fontsize=size)


def replace_in_pdf(src, dst, old, new, ignore_case=False, fontfile=None,
                   dry_run=False, fit=False):
    doc = fitz.open(src)
    if doc.needs_pass:
        sys.exit("PDF is password protected; remove the password first.")

    total = 0
    for page in doc:
        hits = page.search_for(old)  # PyMuPDF search is case-insensitive
        if not ignore_case:
            hits = [r for r in hits if old in page.get_textbox(r)]
        if not hits:
            continue

        print(f"page {page.number + 1}: {len(hits)} match(es)")
        total += len(hits)

        inserts = []
        for r in hits:
            s = span_at(page, r)
            size = s["size"] if s else r.height * 0.8
            rgb = int_to_rgb(s["color"] if s else 0)
            baseline = s["origin"][1] if s else r.y1 - 2
            width = text_width(new, size, fontfile)
            if width > r.width + 0.5:
                if fit:
                    size *= r.width / width
                    print(f"  page {page.number + 1}: shrank new text to {size:.1f}pt to fit")
                else:
                    print(f"  page {page.number + 1}: WARNING new text is "
                          f"{width - r.width:.0f}pt wider than old and may overlap "
                          f"(use --fit to shrink it)")
            if dry_run:
                continue
            inserts.append((fitz.Point(r.x0, baseline), size, rgb))
            page.add_redact_annot(core_rect(r), fill=False)  # remove old text, no white box
        if dry_run:
            continue
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

        for pt, size, rgb in inserts:
            if fontfile:
                page.insert_text(pt, new, fontsize=size, color=rgb,
                                 fontname="custom", fontfile=fontfile)
            else:
                page.insert_text(pt, new, fontsize=size, color=rgb, fontname="helv")

    # Document properties (title, author, subject, keywords) can hold the value too
    meta = dict(doc.metadata)
    flags = 0 if not ignore_case else re.IGNORECASE
    changed = [k for k in ("title", "author", "subject", "keywords")
               if meta.get(k) and re.search(re.escape(old), meta[k], flags)]
    if changed:
        print(f"metadata: {', '.join(changed)}")
        total += len(changed)
        for k in changed:
            meta[k] = re.sub(re.escape(old), lambda _: new, meta[k], flags=flags)
        if not dry_run:
            doc.set_metadata(meta)

    if total and not dry_run:
        doc.save(dst, garbage=3, deflate=True)
    doc.close()
    return total


def main():
    p = argparse.ArgumentParser(description="Find and replace a text value in a PDF.")
    p.add_argument("input", help="source PDF path")
    p.add_argument("output", help="new PDF path (must differ from input)")
    p.add_argument("old", help="text to find")
    p.add_argument("new", help="replacement text")
    p.add_argument("--ignore-case", action="store_true",
                   help="match regardless of upper/lower case")
    p.add_argument("--font-file", help="TTF/OTF font for the new text (default Helvetica)")
    p.add_argument("--fit", action="store_true",
                   help="shrink new text when it is wider than the old text")
    p.add_argument("--dry-run", action="store_true",
                   help="report matches without writing a file")
    a = p.parse_args()

    if not os.path.isfile(a.input):
        sys.exit(f"Input not found: {a.input}")
    if not a.dry_run and os.path.abspath(a.input) == os.path.abspath(a.output):
        sys.exit("Output must be a different file than input.")
    if a.font_file and not os.path.isfile(a.font_file):
        sys.exit(f"Font file not found: {a.font_file}")
    if not a.old:
        sys.exit("Text to find cannot be empty.")

    n = replace_in_pdf(a.input, a.output, a.old, a.new,
                       ignore_case=a.ignore_case, fontfile=a.font_file,
                       dry_run=a.dry_run, fit=a.fit)
    if n == 0:
        print(f'No matches for "{a.old}". No file written.')
        sys.exit(1)
    if a.dry_run:
        print(f"{n} match(es) found (dry run, nothing written).")
    else:
        print(f"Replaced {n} match(es). Saved: {a.output}")


if __name__ == "__main__":
    main()
