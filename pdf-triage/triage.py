#!/usr/bin/env python3
"""First-pass check for uploaded insurance illustrations (no AI).

Answers, for each PDF, before any AI call:
  1. Can we read it?            (password, text layer / scanned)
  2. What is it?                (illustration, rate-increase letter, policy, comparison report, other)
  3. Seen this exact file?      (SHA-256 of the bytes)
  4. Seen these same numbers?   (fingerprint of every $, % and amount in the document;
                                 names, dates and case IDs do not change it)
  5. Seen this same quote?      (carrier + product + forms + age + sex + state + premium + class,
                                 within a recent date window; illustrations are deterministic)
  6. Known layout with a parser? (catalog.json "parsers")
and recommends what to do for the two uses:
  comparison  - extract this client's values for a side-by-side report
  library     - add a new example to the shared repository

The index (default .triage/index.json) stores fingerprints and the quote profile only:
no names, file names, agent details, case IDs or document text.

Commands:
  triage.py check  PATH [PATH ...]            report, change nothing
  triage.py record PATH [PATH ...] --source ai|parser|manual
                                              add to the index after the PDF was processed
  triage.py list                              summary of the index
PATH may be a PDF or a folder of PDFs. Quote paths that contain spaces.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import Counter

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        sys.exit("PyMuPDF is not installed. Run: pip install -r requirements.txt")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_INDEX = os.path.join(ROOT, ".triage", "index.json")
CATALOG = os.path.join(HERE, "catalog.json")

SAME_VALUES_THRESHOLD = 0.97   # share of value tokens in common to call two PDFs "same numbers"
PROFILE_WINDOW_MONTHS = 6      # a matching quote older than this is treated as stale (rates change)
MIN_TEXT_CHARS_PER_PAGE = 40   # below this on most pages, the PDF is treated as scanned

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_BY_NAME = {v.upper(): k for k, v in STATES.items()}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], 1)}

KIND_WORDS = {
    "illustration": [r"\billustrat", r"non-?guaranteed", r"guaranteed values", r"hypothetical",
                     r"not a contract", r"end of year", r"\bproposal\b", r"surrender value",
                     r"cash value", r"death benefit", r"policy year"],
    "rate_action": [r"rate increase", r"premium increase", r"rate adjustment", r"increase in your premium",
                    r"reduced benefit option", r"landing spot"],
    "policy": [r"\bspecimen\b", r"right to examine", r"free look", r"policy schedule", r"this policy is",
               r"entire contract"],
    "comparison_report": [r"\bcomparison\b", r"at a glance", r"side by side", r"for discussion purposes only"],
}
# Minimum number of DIFFERENT signals from the lists above (density misleads on short or long files).
KIND_MIN_SIGNALS = {"comparison_report": 2, "rate_action": 2, "illustration": 3, "policy": 2}


# ---------------------------------------------------------------- reading ---
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def short_hash(text, n=16):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def read_pdf(path):
    doc = fitz.open(path)
    if doc.needs_pass:
        return {"locked": True, "pages": len(doc), "producer": "", "texts": []}
    texts = [p.get_text() for p in doc]
    return {"locked": False, "pages": len(doc), "producer": doc.metadata.get("producer") or "",
            "texts": texts}


# ----------------------------------------------------------- fingerprints ---
DATE_RX = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b")
VALUE_RX = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?|\d+(?:\.\d+)?\s?%|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")


def value_tokens(texts):
    """Every money amount, percent and comma-grouped number, in document order.

    Names, dates, ages and case/ID numbers are not included, so a copy of the same
    illustration prepared for a different name or on a different day has the same tokens.
    """
    out = []
    for t in texts:
        t = DATE_RX.sub(" ", t)
        for m in VALUE_RX.findall(t):
            out.append(re.sub(r"[\s$,]", "", m))
    return out


def token_set(tokens):
    """Hashed multiset of value tokens (stored instead of the values themselves)."""
    c = Counter(tokens)
    return sorted(f"{short_hash(tok, 10)}x{n}" for tok, n in c.items())


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


# ------------------------------------------------------- classify/profile ---
def classify(full, carriers_named):
    """Document type from how many different signal phrases appear.

    Checked in order; a document naming two or more carriers with comparison wording is a
    comparison report (illustrations come from one carrier).
    """
    low = full.lower()
    signals = {k: sum(1 for p in pats if re.search(p, low)) for k, pats in KIND_WORDS.items()}
    ok = {k: signals[k] >= KIND_MIN_SIGNALS[k] for k in signals}
    if ok["comparison_report"] and carriers_named >= 2:
        kind = "comparison_report"
    elif ok["rate_action"]:
        kind = "rate_action"
    elif ok["illustration"]:
        kind = "illustration"
    elif ok["policy"]:
        kind = "policy"
    else:
        kind = "other"
    return kind, {**signals, "carriers_named": carriers_named}


def carrier_counts(full, catalog):
    return {name: sum(full.count(a) for a in aliases) for name, aliases in catalog["carriers"].items()}


def detect_carrier(counts):
    name, n = max(counts.items(), key=lambda kv: kv[1])
    return name if n else None


def detect_product(full, carrier, catalog):
    names = catalog["products"].get(carrier, []) if carrier else []
    hits = [(full.count(p), len(p), p) for p in names if p in full]
    return max(hits)[2] if hits else None


FORM_RX = re.compile(r"\bICC\d{2}-[A-Z0-9][A-Z0-9-]*\b")


def detect_forms(full):
    """Policy/rider form numbers (ICCyy-...). Long all-digit ones are case IDs, not forms."""
    forms = Counter(f.rstrip("-") for f in FORM_RX.findall(full))
    return sorted(f for f in forms if not re.fullmatch(r"ICC\d{2}-\d{6,}", f))


def producer_family(producer):
    p = re.sub(r"[\d.]+", "", producer or "").strip().lower()
    return re.sub(r"\s+", " ", p)[:40]


def first_match(full, patterns):
    """Most common capture of the first pattern that matches anything."""
    for rx in patterns:
        found = re.findall(rx, full)
        if found:
            return Counter(found).most_common(1)[0][0]
    return None


def detect_age(full):
    v = first_match(full, [
        r"Issue Age:?\s*(\d{2})\b",
        r"(?:Male|Female|MALE|FEMALE)\W{1,3}(?:[Aa]ge\s*)?(\d{2})\b",
        r"\bAGE (\d{2})\b",
        r"\bAge:\s*(\d{2})\b",
    ])
    return int(v) if v and 18 <= int(v) <= 95 else None


def detect_sex(full):
    v = first_match(full, [r"Gender:\s*(Male|Female)", r"\b(Male|Female)\b", r"\b(MALE|FEMALE)\b"])
    return v.lower() if v else None


def to_state(s):
    s = s.strip().upper()
    return s if s in STATES else STATE_BY_NAME.get(s)


def detect_state(full):
    v = first_match(full, [
        r"Issue State:?\s*([A-Za-z]+(?: [A-Za-z]+)?)",
        r"State of Issue:?\s*([A-Za-z]+(?: [A-Za-z]+)?)",
        r"Presented in:?\s*([A-Z]{2})\b",
        r"\bAGE \d{2},\s*([A-Z]+(?: [A-Z]+)?)\b",
    ])
    if not v:
        return None
    return to_state(v) or to_state(v.split()[0])


def money(s):
    try:
        return int(round(float(s.replace(",", "").replace("$", ""))))
    except ValueError:
        return None


def detect_premium(full):
    v = first_match(full, [
        r"(?:Initial|Planned|Single|Total|Annual|Modal)\s+Premium:?\s*\$\s?([\d,]+(?:\.\d\d)?)",
        r"(?:Initial|Planned|Single|Total|Annual|Modal)\s+Premium:?\s*\n\s*\$?\s?([\d,]{4,}(?:\.\d\d)?)",
        r"premium of \$\s?([\d,]+(?:\.\d\d)?)",
        r"\bTP\s+([\d,]+\.\d\d)\b",
    ])
    return money(v) if v else None


def detect_class(full):
    low = full.lower()
    if re.search(r"\b(non-?tobacco|nonsmoker|non-?smoker|\bns\b)", low):
        base = "nontobacco"
    elif re.search(r"\b(tobacco|smoker)\b", low):
        base = "tobacco"
    else:
        base = None
    if re.search(r"underwriting class:\s*preferred|risk class:\s*preferred", low):
        base = "preferred" if not base else base + "+preferred"
    couples = bool(re.search(r"couples? discount", low))
    return (base or "not_stated") + ("+couples" if couples else "")


def parse_date(s):
    s = s.strip()
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    m = re.match(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


DATE_TEXT = r"([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"


def detect_date(full):
    v = first_match(full, [
        r"Prepared [Oo]n:?\s*" + DATE_TEXT,
        r"DATE PREPARED\s*" + DATE_TEXT,
        r"Illustration Date:?\s*" + DATE_TEXT,
        r"Date:\s*" + DATE_TEXT,
        r"\bon " + DATE_TEXT,
    ])
    if not v:
        dates = [parse_date(x) for x in re.findall(DATE_TEXT, full)]
        dates = [d for d in dates if d]
        v_date = Counter(dates).most_common(1)[0][0] if dates else None
    else:
        v_date = parse_date(v)
    return v_date.strftime("%Y-%m") if v_date else None


def months_between(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return abs((ya - yb) * 12 + (ma - mb))


# --------------------------------------------------------------- analysis ---
def analyze(path, catalog):
    info = {"file": path, "sha256": sha256_file(path)}
    pdf = read_pdf(path)
    info["pages"] = pdf["pages"]
    if pdf["locked"]:
        info.update(readable="locked")
        return info
    texts = pdf["texts"]
    thin = sum(1 for t in texts if len(t.strip()) < MIN_TEXT_CHARS_PER_PAGE)
    info["readable"] = "scanned" if thin > len(texts) / 2 else "text"
    full = "\n".join(texts)
    counts = carrier_counts(full, catalog)
    named = sum(1 for n in counts.values() if n >= 2)
    info["kind"], info["kind_signals"] = classify(full, named) if info["readable"] == "text" else ("unknown", {})

    tokens = value_tokens(texts)
    info["value_count"] = len(tokens)
    info["values_hash"] = short_hash("|".join(tokens)) if tokens else None
    info["value_set"] = token_set(tokens)

    carrier = detect_carrier(counts)
    forms = detect_forms(full)
    info["layout"] = {
        "carrier": carrier,
        "product": detect_product(full, carrier, catalog),
        "forms": forms[:8],
        "producer": producer_family(pdf["producer"]),
    }
    info["layout_key"] = short_hash(json.dumps(
        [carrier, info["layout"]["product"], forms[:8], info["layout"]["producer"]]), 12)
    info["profile"] = {
        "age": detect_age(full),
        "sex": detect_sex(full),
        "state": detect_state(full),
        "premium": detect_premium(full),
        "class": detect_class(full),
        "date": detect_date(full),
    }
    p = info["profile"]
    complete = all(p[k] is not None for k in ("age", "sex", "premium")) and carrier
    info["profile_key"] = short_hash(json.dumps(
        [info["layout_key"], p["age"], p["sex"], p["state"], p["premium"], p["class"]]), 12) if complete else None
    return info


def decide(info, index, catalog):
    """Return (comparison_action, library_action, reasons)."""
    reasons = []
    if info.get("readable") == "locked":
        return "stop: password protected", "skip", ["PDF needs a password; ask for an unlocked copy."]
    if info["readable"] == "scanned":
        return "ocr then re-check", "skip until OCR", ["Most pages have no text layer (scanned images)."]
    if info["kind"] != "illustration":
        route = {"rate_action": "route to rate-increase extraction",
                 "policy": "route to policy extraction",
                 "comparison_report": "skip: already a comparison report"}.get(info["kind"], "skip: not an illustration")
        return route, "skip", [f"Looks like: {info['kind']}."]

    recs = index["records"]
    same_file = next((r for r in recs if r["sha256"] == info["sha256"]), None)
    if same_file:
        reasons.append(f"Exact file already processed ({same_file['source']}, {same_file['recorded_at'][:10]}).")
        return "reuse earlier result (exact file)", "skip: already in library", reasons

    best, best_sim = None, 0.0
    for r in recs:
        if r.get("values_hash") and r["values_hash"] == info["values_hash"]:
            best, best_sim = r, 1.0
            break
        s = jaccard(r.get("value_set", []), info["value_set"])
        if s > best_sim:
            best, best_sim = r, s
    if best and best_sim >= SAME_VALUES_THRESHOLD:
        reasons.append(f"Same numbers as an earlier illustration ({best_sim:.0%} of values match; "
                       f"only names/dates/IDs differ).")
        return "reuse earlier result (same numbers)", "skip: already in library", reasons
    if best and best_sim >= 0.5:
        reasons.append(f"Closest earlier illustration shares {best_sim:.0%} of values "
                       f"(likely same product with different options).")

    # The numbers are new, so the comparison needs this document read (parser or AI).
    # A matching quote profile only means the library already has an example like it.
    library = "add to library"
    p = info["profile"]
    if info["profile_key"]:
        match = [r for r in recs if r.get("profile_key") == info["profile_key"]]
        fresh = [r for r in match if p["date"] and r["profile"].get("date")
                 and months_between(p["date"], r["profile"]["date"]) <= PROFILE_WINDOW_MONTHS]
        if fresh:
            reasons.append(f"Library already has these quote inputs (carrier, product, age, sex, state, premium, "
                           f"class) from {fresh[0]['profile']['date']}, but the numbers differ: options or "
                           f"rates changed.")
            library = "skip: profile already in library (review the difference)"
        elif match:
            reasons.append(f"Same quote inputs seen, but older than {PROFILE_WINDOW_MONTHS} months; "
                           f"rates may have changed.")
            library = "add to library (newer rates)"
    else:
        missing = [k for k in ("age", "sex", "premium") if p[k] is None]
        reasons.append("Quote inputs incomplete (" + (", ".join(missing) or "carrier") + " not found); "
                       "cannot match on profile.")

    parser = next((x for x in catalog.get("parsers", []) if x.get("layout_key") == info["layout_key"]), None)
    if parser:
        reasons.append(f"Known layout with rule-based parser: {parser.get('name')}.")
        return f"parse with rules ({parser.get('name')})", library, reasons

    seen = sum(1 for r in recs if r.get("layout_key") == info["layout_key"])
    if seen:
        reasons.append(f"Layout seen {seen} time(s) before, no parser yet (parser candidate).")
    else:
        reasons.append("New layout (not seen before).")
    return "send to AI", library, reasons


# ------------------------------------------------------------------ index ---
def load_index(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"version": 1, "records": []}


def save_index(path, index):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(index, f, indent=1)
    os.replace(tmp, path)


def to_record(info, source):
    """What is stored: fingerprints and the quote profile. No names, file names or text."""
    return {
        "sha256": info["sha256"],
        "values_hash": info["values_hash"],
        "value_set": info["value_set"],
        "layout_key": info["layout_key"],
        "layout": info["layout"],
        "profile_key": info["profile_key"],
        "profile": info["profile"],
        "pages": info["pages"],
        "source": source,
        "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


# -------------------------------------------------------------------- CLI ---
def expand(paths):
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            out += sorted(os.path.join(p, f) for f in os.listdir(p) if f.lower().endswith(".pdf"))
        elif os.path.isfile(p):
            out.append(p)
        else:
            print(f"Not found: {p}", file=sys.stderr)
    return out


def show(info, comparison, library, reasons):
    print(f"\n{os.path.basename(info['file'])}")
    print(f"  pages {info['pages']}, {info.get('readable')}, kind: {info.get('kind', '-')}")
    if info.get("layout"):
        L, P = info["layout"], info["profile"]
        print(f"  carrier: {L['carrier'] or '?'} | product: {L['product'] or '?'} | forms: {', '.join(L['forms'][:4]) or '-'}")
        prem = f"${P['premium']:,}" if P["premium"] else "?"
        print(f"  quote: age {P['age'] or '?'}, {P['sex'] or '?'}, {P['state'] or '?'}, premium {prem}, "
              f"class {P['class']}, dated {P['date'] or '?'}")
        print(f"  fingerprints: file {info['sha256'][:12]}  values {info['values_hash'] or '-'} "
              f"({info['value_count']})  layout {info['layout_key']}  quote {info['profile_key'] or '-'}")
    for r in reasons:
        print(f"  - {r}")
    print(f"  => comparison: {comparison}")
    print(f"  => library:    {library}")


def main():
    ap = argparse.ArgumentParser(description="First-pass check for illustration PDFs (no AI).")
    ap.add_argument("--index", default=DEFAULT_INDEX, help=f"index file (default {DEFAULT_INDEX})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="report what to do with each PDF; changes nothing")
    c.add_argument("paths", nargs="+")
    c.add_argument("--json", action="store_true", help="print JSON instead of text")
    r = sub.add_parser("record", help="add PDFs to the index after they were processed")
    r.add_argument("paths", nargs="+")
    r.add_argument("--source", choices=["ai", "parser", "manual"], required=True,
                   help="how the PDF was processed")
    sub.add_parser("list", help="summary of the index")
    a = ap.parse_args()

    with open(CATALOG) as f:
        catalog = json.load(f)
    index = load_index(a.index)

    if a.cmd == "list":
        recs = index["records"]
        print(f"{len(recs)} record(s) in {a.index}")
        by = Counter((r["layout"]["carrier"] or "?", r["layout"]["product"] or "?", r["layout_key"]) for r in recs)
        for (carrier, product, key), n in by.most_common():
            print(f"  {n:3}  {carrier:<18} {product:<20} layout {key}")
        return

    files = expand(a.paths)
    if not files:
        sys.exit("No PDF files found.")

    if a.cmd == "check":
        results = []
        for f in files:
            info = analyze(f, catalog)
            comparison, library, reasons = decide(info, index, catalog)
            if a.json:
                info.pop("value_set", None)
                results.append({**info, "comparison": comparison, "library": library, "reasons": reasons})
            else:
                show(info, comparison, library, reasons)
            # Later files in the same run are checked against earlier ones too.
            if info.get("kind") == "illustration":
                index["records"].append(to_record(info, "this-run"))
        if a.json:
            print(json.dumps(results, indent=1))
        return

    if a.cmd == "record":
        added = 0
        for f in files:
            info = analyze(f, catalog)
            name = os.path.basename(f)
            if info.get("kind") != "illustration":
                print(f"skip {name}: not an illustration ({info.get('kind', info.get('readable'))})")
                continue
            if any(r["sha256"] == info["sha256"] for r in index["records"]):
                print(f"skip {name}: already recorded")
                continue
            index["records"].append(to_record(info, a.source))
            added += 1
            print(f"recorded {name}")
        save_index(a.index, index)
        print(f"{added} added, {len(index['records'])} in index")


if __name__ == "__main__":
    main()
