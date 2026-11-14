#!/usr/bin/env python3
"""Self-test for triage.py using made-up PDFs (no client data).

Builds one PDF per decision path in a temp folder, records the first one in a temp
index, and checks the decision for each. Run: .venv/bin/python pdf-triage/selftest.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import triage as T  # noqa: E402

fitz = T.fitz


def illustration(path, name, date, ledger_seed, premium="100,000.00"):
    d = fitz.open()
    p = d.new_page()
    p.insert_text((50, 60), "SecureCare IV  Your proposal for long-term care", fontsize=12)
    p.insert_text((50, 80), "Issued by Minnesota Life Insurance Company (Securian Financial)", fontsize=9)
    p.insert_text((50, 100), f"Prepared for: {name}, Male, age 64, Non-Tobacco", fontsize=9)
    p.insert_text((50, 115), f"Prepared on: {date}    Presented in: TX", fontsize=9)
    p.insert_text((50, 130), f"Total Premium: ${premium}", fontsize=9)
    p.insert_text((50, 145), "This illustration is not a contract. Values are hypothetical.", fontsize=9)
    p.insert_text((50, 160), "Policy form ICC20-20212   Rider ICC21-20220", fontsize=9)
    for page in range(2):
        q = d.new_page()
        q.insert_text((50, 60), "Guaranteed values by policy year: cash value, death benefit, surrender value", fontsize=9)
        for yr in range(1, 21):
            v = 100000 + yr * 1000 * ledger_seed + page * 7
            q.insert_text((50, 70 + yr * 14),
                          f"{yr}  {63 + yr}  ${v:,}  ${v * 2:,}  ${v * 5:,}  {3 + yr / 100:.2f}%", fontsize=8)
    d.save(path)


def text_pdf(path, lines):
    d = fitz.open()
    p = d.new_page()
    for i, line in enumerate(lines):
        p.insert_text((50, 60 + i * 16), line, fontsize=10)
    d.save(path)


def main():
    tmp = tempfile.mkdtemp(prefix="triage-selftest-")
    f = lambda n: os.path.join(tmp, n)  # noqa: E731
    catalog = json.load(open(T.CATALOG))

    illustration(f("A.pdf"), "Pat Example", "11-11-2026", 1)
    illustration(f("B_same_numbers.pdf"), "Chris Sample", "12-02-2026", 1)            # name/date only
    illustration(f("C_same_profile.pdf"), "Pat Example", "01-15-2027", 3)            # new numbers, 2 months
    illustration(f("D_stale_profile.pdf"), "Pat Example", "11-11-2028", 4)           # new numbers, 2 years
    illustration(f("E_other_premium.pdf"), "Pat Example", "11-11-2026", 5, "150,000.00")

    img = fitz.open(f("A.pdf"))                                                      # scanned: images only
    scanned = fitz.open()
    for pg in img:
        pix = pg.get_pixmap(dpi=50)
        np = scanned.new_page(width=pg.rect.width, height=pg.rect.height)
        np.insert_image(np.rect, pixmap=pix)
    scanned.save(f("F_scanned.pdf"))

    locked = fitz.open(f("A.pdf"))
    locked.save(f("G_locked.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")

    text_pdf(f("H_rate_letter.pdf"), [
        "Important notice about a premium rate increase on your long-term care policy",
        "Your premium increase takes effect on your next anniversary.",
        "You may choose a reduced benefit option instead of the rate increase.",
    ])
    text_pdf(f("I_comparison.pdf"), [
        "Long-Term Care Options Comparison - At a Glance",
        "Securian SecureCare IV vs Brighthouse SmartCare. Securian / Brighthouse.",
        "For discussion purposes only. See carrier illustrations for complete details.",
    ])

    index = {"version": 1, "records": [T.to_record(T.analyze(f("A.pdf"), catalog), "ai")]}
    # file: (comparison action, library action starts with)
    expect = {
        "A.pdf": ("reuse earlier result (exact file)", "skip"),
        "B_same_numbers.pdf": ("reuse earlier result (same numbers)", "skip"),
        "C_same_profile.pdf": ("send to AI", "skip: profile already in library"),
        "D_stale_profile.pdf": ("send to AI", "add to library (newer rates)"),
        "E_other_premium.pdf": ("send to AI", "add to library"),
        "F_scanned.pdf": ("ocr then re-check", "skip"),
        "G_locked.pdf": ("stop: password protected", "skip"),
        "H_rate_letter.pdf": ("route to rate-increase extraction", "skip"),
        "I_comparison.pdf": ("skip: already a comparison report", "skip"),
    }
    failed = 0
    for name, (want, want_lib) in expect.items():
        info = T.analyze(f(name), catalog)
        got, library, reasons = T.decide(info, index, catalog)
        ok = got == want and library.startswith(want_lib)
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name:<22} {got} | library: {library}"
              + ("" if ok else f"   (expected: {want} | {want_lib})"))
        if not ok or name in ("C_same_profile.pdf", "D_stale_profile.pdf"):
            for r in reasons:
                print(f"       - {r}")

    # The index must not hold names or file names.
    blob = json.dumps(index)
    leak = [s for s in ("Pat Example", "A.pdf", "Chris") if s in blob]
    print(("FAIL" if leak else "ok  ") + " index holds no names or file names" + (f": {leak}" if leak else ""))
    failed += bool(leak)
    print(f"\n{len(expect) + 1 - failed}/{len(expect) + 1} passed  ({tmp})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
