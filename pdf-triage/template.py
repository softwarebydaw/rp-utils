#!/usr/bin/env python3
"""Learn a reading template from one checked answer, then read same-layout illustrations without AI.

The first illustration of a layout goes to AI. Besides the values, the AI returns a short
reading guide: for each value the printed label next to it, and for each year-by-year table
its column headings and one or two sample rows. After a person checks it, that is the
"answer file". From it this program learns a template:

  fields   label text, which occurrence, value right of / below the label, value type,
           and how to find the page (first page with the label, or a page heading)
  tables   page heading(s), number of cells per row, which cell holds which column
  checks   rules that held on every row of the learned document (years count up by one,
           age = year + constant, annual = 12 x monthly, ...) and links between single values
           and table cells (premium = year-1 premium, pool at age 85 = table row at age 85)

A template holds labels, headings and positions only, never client values or names.

Commands (quote paths that contain spaces):
  template.py learn  PDF ANSWER     learn a template, replay it on the same PDF, save it
  template.py apply  PDF [--json]   read a PDF with its layout's template and run the checks
  template.py verify PDF ANSWER     read with the template and compare with a checked answer;
                                    3 agreements move a template from trial to trusted
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import triage as T  # noqa: E402

fitz = T.fitz
TEMPLATES = os.path.join(HERE, "templates")
TRUST_AFTER = 3          # agreements needed to move a template from trial to trusted
Y_TOL = 5.0              # points: label and value count as one line within this distance
ROW_TOL = 2.5            # points: table cells count as one row within this distance
NICE_RATIOS = (1, 2, 3, 4, 6, 12)


# ------------------------------------------------------------- text helpers ---
def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("‘", "'")
                  .replace("“", '"').replace("”", '"')).strip().lower()


def bare(tok):
    return norm(tok).strip(".,;:()*")


class Word:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "order")

    def __init__(self, w, order):
        self.x0, self.y0, self.x1, self.y1, self.text = w[0], w[1], w[2], w[3], w[4]
        self.order = order

    @property
    def yc(self):
        return (self.y0 + self.y1) / 2


class Page:
    def __init__(self, page):
        self.number = page.number + 1
        raw = page.get_text("words")
        if page.rotation:
            # Word boxes come in the unrotated frame; turn them to the page as printed so
            # "same line" and "right of" mean what a reader sees.
            m = page.rotation_matrix
            raw = [tuple(fitz.Rect(w[:4]) * m) + tuple(w[4:]) for w in raw]
        raw.sort(key=lambda w: (w[5], w[6], w[7]))  # block, line, word = reading order
        self.words = [Word(w, i) for i, w in enumerate(raw)]
        self.text = norm(page.get_text())

    def find_label(self, label, match_case=False):
        """All occurrences of the label, found by position: each next word is the nearest word
        to the right on the same line (reading order is unreliable on multi-column pages).
        Returned top-to-bottom, left-to-right. match_case for labels like "AGE" in a footer."""
        cut = (lambda s: s.strip(".,;:()*")) if match_case else bare
        toks = [cut(t) for t in label.split()]

        def match(w, j):
            b = cut(w.text)
            return b == toks[j] or (j == len(toks) - 1 and toks[j] and b.startswith(toks[j]))

        out = []
        for w in sorted(self.words, key=lambda w: (round(w.yc), w.x0)):
            if not match(w, 0):
                continue
            seq = [w]
            for j in range(1, len(toks)):
                nxt = [v for v in self.words if abs(v.yc - seq[-1].yc) < Y_TOL / 2
                       and -1 <= v.x0 - seq[-1].x1 <= 15]
                nxt = min(nxt, key=lambda v: v.x0, default=None)
                if nxt is None or not match(nxt, j):
                    seq = None
                    break
                seq.append(nxt)
            if seq:
                out.append(seq)
        return out

    def right_of(self, label_words):
        lx1 = max(w.x1 for w in label_words)
        yc = sum(w.yc for w in label_words) / len(label_words)
        cand = sorted((w for w in self.words if abs(w.yc - yc) < Y_TOL and w.x0 >= lx1 - 1
                       and w not in label_words), key=lambda w: w.x0)
        return cut_at_gap(cand)

    def below(self, label_words):
        lx0 = min(w.x0 for w in label_words)
        ly1 = max(w.y1 for w in label_words)
        h = max(w.y1 - w.y0 for w in label_words)
        cand = [w for w in self.words if ly1 - 0.5 * h < w.y0 < ly1 + 2.5 * h and w.x0 >= lx0 - 5
                and w not in label_words]
        if not cand:
            return []
        top = min(w.yc for w in cand)
        return cut_at_gap(sorted((w for w in cand if abs(w.yc - top) < Y_TOL), key=lambda w: w.x0))


def cut_at_gap(words, gap=30):
    out = []
    for w in words:
        if out and w.x0 - out[-1].x1 > gap:
            break
        out.append(w)
    return out


# ------------------------------------------------------------- value parsing ---
MONEY_RX = re.compile(r"^\(?-?\$?\d[\d,]*(?:\.\d+)?\)?[.,;]?$")


def to_number(tok):
    t = tok.strip().rstrip(".,;")
    neg = t.startswith("(") or t.startswith("-")
    t = re.sub(r"[()$,%\-]", "", t)
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def parse_value(kind, words, skip=0):
    toks = [w.text for w in words][skip:]
    if kind == "money":
        cands = [t for t in toks if "$" in t and MONEY_RX.match(t)] or \
                [t for t in toks if MONEY_RX.match(t) and ("," in t or "." in t)]
        return fmt_num(to_number(cands[0])) if cands else None
    if kind == "integer":
        for t in toks:
            m = re.match(r"^(\d+)(?:[,.;:]|-day)?$", t)
            if m:
                return m.group(1)
        return None
    if kind == "percent":
        for t in toks:
            if "%" in t:
                n = to_number(t)
                return fmt_num(n) if n is not None else None
        return None
    if kind == "sex":
        for t in toks:
            b = bare(t)
            if b in ("male", "female"):
                return b
        return None
    if kind == "state":
        for i, t in enumerate(toks):
            s = T.to_state(bare(t).upper())
            if s:
                return s
            if i + 1 < len(toks):
                s = T.to_state((bare(t) + " " + bare(toks[i + 1])).upper())
                if s:
                    return s
        return None
    if kind == "date":
        joined = " ".join(toks)
        m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", joined)
        if m:
            return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        m = re.search(r"([A-Za-z]+) (\d{1,2}), (\d{4})", joined)
        if m and m.group(1).lower() in T.MONTHS:
            return f"{int(m.group(3)):04d}-{T.MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
        return None
    # text: rest of the line
    return " ".join(toks).strip(" ,;") or None


def fmt_num(v):
    if v is None:
        return None
    return str(int(v)) if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")


def same(kind, a, b):
    if a is None or b is None:
        return False
    if kind in ("money", "integer", "percent"):
        x, y = to_number(str(a)), to_number(str(b))
        return x is not None and y is not None and abs(x - y) < 0.006
    return norm(str(a)) == norm(str(b))


def close(a, b, rel=0.0015, absol=1.01):
    return abs(a - b) <= max(absol, rel * max(abs(a), abs(b)))


# ------------------------------------------------------------------ fields ---
def pages_with_label(pages, label, match_case=False):
    return [p for p in pages if p.find_label(label, match_case)]


def marker_pages(pages, markers):
    ms = [norm(m) for m in markers]
    return [p for p in pages if all(m in p.text for m in ms)]


def read_field(pages, rule):
    if rule.get("page_marker"):
        cands = marker_pages(pages, rule["page_marker"])
    else:
        cands = pages_with_label(pages, rule["label"], rule.get("match_case", False))[:1]
    for p in cands:
        occ = p.find_label(rule["label"], rule.get("match_case", False))
        if len(occ) > rule["occurrence"]:
            lw = occ[rule["occurrence"]]
            words = p.right_of(lw) if rule["relation"] == "right" else p.below(lw)
            v = parse_value(rule["kind"], words, rule.get("skip_tokens", 0))
            if v is not None:
                return v, p.number
    return None, None


def learn_field(pages, ans):
    """Find a rule that reads the answer's value back from the page the answer cites."""
    order = [p for p in pages if p.number == ans.get("page")] or pages
    for p in order:
        for occ_i, lw in enumerate(p.find_label(ans["label"], ans.get("match_case", False))):
            for rel in ("right", "below"):
                words = p.right_of(lw) if rel == "right" else p.below(lw)
                v = parse_value(ans["kind"], words, ans.get("skip_tokens", 0))
                if not same(ans["kind"], v, ans["value"]):
                    continue
                rule = {"key": ans["key"], "kind": ans["kind"], "label": ans["label"],
                        "occurrence": occ_i, "relation": rel}
                for opt in ("skip_tokens", "match_case"):
                    if ans.get(opt):
                        rule[opt] = ans[opt]
                # Page rule: first page that has the label, or the page heading the AI gave.
                first = pages_with_label(pages, ans["label"], ans.get("match_case", False))[0]
                if first.number == p.number:
                    pass
                elif ans.get("page_marker"):
                    rule["page_marker"] = ans["page_marker"]
                else:
                    continue
                if read_field(pages, rule)[0] is not None and same(ans["kind"], read_field(pages, rule)[0], ans["value"]):
                    return rule, None
    return None, f"label '{ans['label']}' with value {ans['value']} not found (needs a page heading or a better label)"


# ------------------------------------------------------------------ tables ---
def page_rows(page):
    """Group words into rows by vertical position; keep rows that start with two integers (year, age)."""
    rows = []
    for w in sorted(page.words, key=lambda w: (round(w.yc), w.x0)):
        if rows and abs(rows[-1][0] - w.yc) < ROW_TOL:
            rows[-1][1].append(w)
        else:
            rows.append([w.yc, [w]])
    out = []
    for yc, ws in rows:
        ws.sort(key=lambda w: w.x0)
        toks = [w.text for w in ws]
        if len(toks) >= 3 and re.fullmatch(r"\d{1,3}", toks[0]) and re.fullmatch(r"\d{1,3}", toks[1]):
            out.append(toks)
    return out


def table_rows(pages, markers):
    rows = []
    for p in marker_pages(pages, markers):
        rows += page_rows(p)
    return rows


def learn_table(pages, ans):
    rows = table_rows(pages, ans["page_marker"])
    if not rows:
        return None, f"table {ans['key']}: no rows on pages with heading {ans['page_marker']}"
    cols, counts = {}, set()
    for sample in ans["sample_rows"]:
        row = next((r for r in rows if r[0] == sample["year"]), None)
        if not row:
            return None, f"table {ans['key']}: sample year {sample['year']} not found"
        counts.add(len(row))
        for c in ans["columns"]:
            idx = [i for i, t in enumerate(row) if same(c["kind"], cell_value(c["kind"], t), sample[c["key"]])]
            if not idx:
                return None, f"table {ans['key']}: column {c['key']} value {sample[c['key']]} not in year {sample['year']} row"
            prev = cols.get(c["key"])
            cols[c["key"]] = [i for i in idx if prev is None or i in prev]
    if len(counts) != 1:
        return None, f"table {ans['key']}: sample rows have different cell counts {sorted(counts)}"
    # Prefer the column order the AI gave when a value appears in several cells.
    used, mapping = set(), {}
    for c in ans["columns"]:
        free = [i for i in cols[c["key"]] if i not in used]
        if not free:
            return None, f"table {ans['key']}: cannot place column {c['key']}"
        mapping[c["key"]] = free[0]
        used.add(free[0])
    rule = {"key": ans["key"], "page_marker": ans["page_marker"], "cells": counts.pop(),
            "columns": [{"key": c["key"], "header": c["header"], "kind": c["kind"], "cell": mapping[c["key"]]}
                        for c in ans["columns"]]}
    return rule, None


CELL_NUM_RX = re.compile(r"^\(?-?\$?\d[\d,]*(?:\.\d+)?%?\)?$")


def cell_value(kind, tok):
    """A table cell: numbers may come with or without $ / % / thousands commas."""
    if kind == "text":
        return tok
    if not CELL_NUM_RX.match(tok.strip()):
        return None
    return fmt_num(to_number(tok))


def read_table(pages, rule):
    data, skipped = [], []
    for row in table_rows(pages, rule["page_marker"]):
        if len(row) != rule["cells"]:
            skipped.append(" ".join(row)[:60])
            continue
        rec = {}
        for c in rule["columns"]:
            v = cell_value(c["kind"], row[c["cell"]])
            rec[c["key"]] = to_number(v) if v is not None and c["kind"] != "text" else v
        data.append(rec)
    return data, skipped


# ---------------------------------------------------------------- invariants ---
def learn_invariants(rows, columns):
    inv = []
    if len(rows) < 5:
        return inv
    keys = [c["key"] for c in columns if c["kind"] in ("money", "integer", "percent")]
    if "year" in keys:
        ys = [r["year"] for r in rows]
        if ys == list(range(int(ys[0]), int(ys[0]) + len(ys))):
            inv.append({"type": "years_consecutive"})
        else:  # some carriers print years 1-20, then every 5th year
            inv.append({"type": "years_increasing"})
        if "age" in keys:
            offs = {r["age"] - r["year"] for r in rows if r["age"] is not None}
            if len(offs) == 1:
                inv.append({"type": "age_minus_year_constant"})
    money = [c["key"] for c in columns if c["kind"] == "money"]
    for a in money:
        for b in money:
            if a == b:
                continue
            pairs = [(r[a], r[b]) for r in rows if r.get(a) and r.get(b)]
            if len(pairs) < 5:
                continue
            ratios = [y / x for x, y in pairs]
            for n in NICE_RATIOS[1:]:
                if all(abs(r - n) <= n * 0.002 + 1.5 / x for r, (x, _) in zip(ratios, pairs)):
                    inv.append({"type": "ratio", "a": a, "b": b, "times": n})
    for k in money:
        vals = [r[k] for r in rows if r.get(k) is not None]
        if len(vals) == len(rows) and len(set(vals)) == 1:
            inv.append({"type": "constant", "col": k, "level": "warning"})
        elif len(vals) == len(rows) and all(y >= x for x, y in zip(vals, vals[1:])):
            inv.append({"type": "non_decreasing", "col": k, "level": "warning"})
    return inv


def check_invariants(rows, inv, table):
    errs, warns = [], []
    for i in inv:
        bad = None
        if i["type"] == "years_consecutive":
            ys = [r["year"] for r in rows]
            if ys != list(range(int(ys[0]), int(ys[0]) + len(ys))) if ys else True:
                bad = "policy years are not consecutive"
        elif i["type"] == "years_increasing":
            ys = [r["year"] for r in rows]
            if any(b <= a for a, b in zip(ys, ys[1:])):
                bad = "policy years do not increase"
        elif i["type"] == "age_minus_year_constant":
            offs = {r["age"] - r["year"] for r in rows}
            if len(offs) != 1:
                bad = "age does not advance one per year"
        elif i["type"] == "ratio":
            for r in rows:
                if r.get(i["a"]) and r.get(i["b"]) is not None and not close(r[i["b"]], r[i["a"]] * i["times"], 0.002, 1.5):
                    bad = f"{i['b']} is not {i['times']} x {i['a']} in year {fmt_num(r['year'])}"
                    break
        elif i["type"] == "constant":
            if len({r[i["col"]] for r in rows}) != 1:
                bad = f"{i['col']} is not the same every year (it was in the learned document)"
        elif i["type"] == "non_decreasing":
            vals = [r[i["col"]] for r in rows]
            if any(y < x for x, y in zip(vals, vals[1:])):
                bad = f"{i['col']} goes down in some year (it never did in the learned document)"
        if bad:
            (warns if i.get("level") == "warning" else errs).append(f"{table}: {bad}")
    return errs, warns


def learn_links(fields, tables_data):
    """Single values that equal a table cell.

    Year-1 cells are tried first. A later row is used only when its attained age is printed
    in the value's label (for example "Age 85:"), so coincidental matches are not learned.
    """
    links = []
    for key, (val, kind, label) in fields.items():
        if kind not in ("money", "integer") or val is None:
            continue
        v = to_number(val)
        tol = 0 if kind == "integer" else 1.01  # whole numbers must match exactly
        found = None
        for tkey, rows in tables_data.items():
            if not rows:
                continue
            for col in rows[0]:
                if col != "year" and rows[0].get(col) is not None and close(rows[0][col], v, 0.0005, tol):
                    found = {"field": key, "table": tkey, "col": col, "where": {"year": rows[0]["year"]},
                             "exact": kind == "integer"}
                    break
            if found:
                break
            ages = [int(a) for a in re.findall(r"\bage (\d{2,3})\b", norm(label))]
            for col in rows[0]:
                hit = next((r for r in rows if r.get("age") in ages and r.get(col) is not None
                            and close(r[col], v, 0.0005, tol)), None)
                if hit and col not in ("age", "year"):
                    found = {"field": key, "table": tkey, "col": col, "where": {"age": hit["age"]},
                             "exact": kind == "integer"}
                    break
            if found:
                break
        if found:
            links.append(found)
    return links


def check_links(fields, tables_data, links):
    errs = []
    for l in links:
        rows = tables_data.get(l["table"]) or []
        k, want = next(iter(l["where"].items()))
        row = next((r for r in rows if r.get(k) == want), None)
        f = fields.get(l["field"])
        if row is None or f is None or row.get(l["col"]) is None:
            errs.append(f"cannot cross-check {l['field']} against {l['table']}.{l['col']} ({k} {fmt_num(want)})")
            continue
        if not close(row[l["col"]], to_number(f), 0 if l.get("exact") else 0.0005, 0 if l.get("exact") else 1.01):
            errs.append(f"{l['field']} ({f}) does not match {l['table']}.{l['col']} at {k} {fmt_num(want)} "
                        f"({fmt_num(row[l['col']])})")
    return errs


# --------------------------------------------------------------- learn/apply ---
def load_pages(path):
    doc = fitz.open(path)
    return [Page(p) for p in doc]


def template_path(layout_key):
    return os.path.join(TEMPLATES, f"{layout_key}.json")


def find_template(layout_key):
    p = template_path(layout_key)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def learn(pdf, answer_path):
    with open(answer_path) as f:
        ans = json.load(f)
    catalog = json.load(open(T.CATALOG))
    info = T.analyze(pdf, catalog)
    pages = load_pages(pdf)

    rules, problems = [], []
    headings = ans.get("page_headings", {})
    for a in ans["fields"]:
        if not a.get("page_marker") and str(a.get("page")) in headings:
            a = {**a, "page_marker": headings[str(a["page"])]}
        rule, err = learn_field(pages, a)
        (rules.append(rule) if rule else problems.append(err))
    trules, tdata = [], {}
    for t in ans.get("tables", []):
        rule, err = learn_table(pages, t)
        if rule:
            rows, _ = read_table(pages, rule)
            rule["invariants"] = learn_invariants(rows, rule["columns"])
            trules.append(rule)
            tdata[rule["key"]] = rows
        else:
            problems.append(err)

    tmpl = {
        "layout_key": info["layout_key"],
        "carrier": ans.get("carrier") or info["layout"]["carrier"],
        "product": ans.get("product") or info["layout"]["product"],
        "layout": info["layout"],
        "status": "trial",
        "agreements": 0,
        "learned_on": dt.date.today().isoformat(),
        "fields": rules,
        "tables": trules,
    }
    fields_now = {r["key"]: (read_field(pages, r)[0], r["kind"], r["label"]) for r in rules}
    tmpl["links"] = learn_links(fields_now, tdata)

    # Replay on the learning document: every value must come back exactly.
    result = apply_template(pdf, tmpl, pages)
    replay = []
    for a in ans["fields"]:
        got = result["fields"].get(a["key"])
        replay.append((a["key"], a["value"], got, same(a["kind"], got, a["value"])))
    for t in ans.get("tables", []):
        rows = result["tables"].get(t["key"], [])
        for s in t["sample_rows"]:
            row = next((r for r in rows if fmt_num(r["year"]) == s["year"]), None)
            ok = row is not None and all(same(c["kind"], fmt_num(row[c["key"]]) if c["kind"] != "text" else row[c["key"]],
                                              s[c["key"]]) for c in t["columns"])
            replay.append((f"{t['key']} year {s['year']}", "row", "row" if row else None, ok))
    tmpl["fields"] = [r for r in rules if all(ok for k, _, _, ok in replay if k == r["key"])]

    blob = json.dumps(tmpl).lower()
    private = [p for p in ans.get("private", []) if p.lower() in blob]
    values = [str(a["value"]).lower() for a in ans["fields"]
              if len(re.sub(r"\D", "", str(a["value"]))) >= 4 and str(a["value"]).lower() in blob]
    leaks = private + values
    return tmpl, replay, problems, result, leaks


def apply_template(pdf, tmpl, pages=None):
    pages = pages or load_pages(pdf)
    fields, where = {}, {}
    for r in tmpl["fields"]:
        v, pn = read_field(pages, r)
        fields[r["key"]], where[r["key"]] = v, pn
    tables, skipped = {}, {}
    errs, warns = [], []
    for t in tmpl["tables"]:
        rows, skip = read_table(pages, t)
        tables[t["key"]], skipped[t["key"]] = rows, skip
        if not rows:
            errs.append(f"{t['key']}: no rows found")
            continue
        e, w = check_invariants(rows, t.get("invariants", []), t["key"])
        errs += e
        warns += w
        if skip:
            warns.append(f"{t['key']}: {len(skip)} row(s) with an unexpected number of cells were skipped")
    missing = [k for k, v in fields.items() if v is None]
    if missing:
        errs.append("not found: " + ", ".join(missing))
    errs += check_links(fields, tables, tmpl.get("links", []))
    return {"fields": fields, "pages": where, "tables": tables, "skipped": skipped,
            "errors": errs, "warnings": warns, "summary": summarize(fields, tables)}


def at_age(rows, by_age, age):
    """Row for an attained age. When the table skips that age (years 1-20, then every 5th),
    use the neighbouring rows if they hold the same values (a flat stretch)."""
    if age in by_age:
        return by_age[age]
    before = [r for r in rows if r.get("age") is not None and r["age"] < age]
    after = [r for r in rows if r.get("age") is not None and r["age"] > age]
    if before and after:
        a, b = before[-1], after[0]
        keys = ("ltc_pool", "ltc_monthly")
        if all(a.get(k) == b.get(k) for k in keys) and a.get("ltc_pool") is not None:
            return {k: a[k] for k in keys}
    return None


def summarize(fields, tables):
    """The numbers a side-by-side comparison uses.

    Tables are merged by policy year in template order (the first table that has a column
    wins), so a carrier that prints the LTC pool in a separate table is covered too.
    """
    merged = {}
    for rows in tables.values():
        for r in rows:
            m = merged.setdefault(r.get("year"), {})
            for k, v in r.items():
                m.setdefault(k, v)
    rows = [merged[y] for y in sorted(k for k in merged if k is not None)]
    if not rows:
        return {}
    by_age = {r.get("age"): r for r in rows}
    by_year = {r.get("year"): r for r in rows}
    first = rows[0]
    prem = to_number(fields.get("premium")) if fields.get("premium") else first.get("premium")
    s = {"premium": prem}
    for label, row in (("day1", first), ("age80", at_age(rows, by_age, 80)), ("age85", at_age(rows, by_age, 85))):
        if row:
            s[f"ltc_pool_{label}"] = row.get("ltc_pool")
            s[f"ltc_monthly_{label}"] = row.get("ltc_monthly")
    if prem and first.get("ltc_pool"):
        s["leverage_day1"] = round(first["ltc_pool"] / prem, 2)
        y5 = by_year.get(5)
        if y5 and y5.get("ltc_pool") and y5["ltc_pool"] > first["ltc_pool"] * 1.25:
            # Benefits that build up (vest) over the first years: show year 5 too.
            s["ltc_pool_year5"] = y5["ltc_pool"]
            s["ltc_monthly_year5"] = y5.get("ltc_monthly")
            s["leverage_year5"] = round(y5["ltc_pool"] / prem, 2)
    s["death_benefit_year1"] = first.get("death_benefit")
    for y in (1, 10):
        if by_year.get(y):
            s[f"surrender_value_year{y}"] = by_year[y].get("surrender_value")
            s[f"surrender_received_year{y}"] = by_year[y].get("surrender_received")
    return {k: v for k, v in s.items() if v is not None}


# ---------------------------------------------------------------------- CLI ---
def print_result(res):
    print("  values:")
    for k, v in res["fields"].items():
        print(f"    {k:<22} {v if v is not None else '(not found)'}" + (f"   p{res['pages'][k]}" if res["pages"].get(k) else ""))
    for k, rows in res["tables"].items():
        print(f"  table {k}: {len(rows)} rows" + (f", years {fmt_num(rows[0]['year'])}-{fmt_num(rows[-1]['year'])}" if rows else ""))
    if res["summary"]:
        print("  comparison numbers:")
        for k, v in res["summary"].items():
            print(f"    {k:<22} {fmt_num(v) if isinstance(v, float) else v}")
    for e in res["errors"]:
        print(f"  ERROR   {e}")
    for w in res["warnings"]:
        print(f"  warning {w}")
    print("  => " + ("checks passed" if not res["errors"] else "checks FAILED: send to AI and relearn"))


def main():
    ap = argparse.ArgumentParser(description="Learn and apply reading templates for illustrations.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("learn")
    l.add_argument("pdf")
    l.add_argument("answer")
    l.add_argument("--dry-run", action="store_true", help="do not save the template")
    a = sub.add_parser("apply")
    a.add_argument("pdf")
    a.add_argument("--template", help="template file (default: found by layout)")
    a.add_argument("--json", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("pdf")
    v.add_argument("answer")
    c = sub.add_parser("compare", help="side-by-side comparison numbers for several PDFs")
    c.add_argument("pdfs", nargs="+")
    args = ap.parse_args()

    if args.cmd == "compare":
        catalog = json.load(open(T.CATALOG))
        cols = []
        for pdf in args.pdfs:
            info = T.analyze(pdf, catalog)
            tmpl = find_template(info["layout_key"])
            if not tmpl:
                cols.append((os.path.basename(pdf), None, "no template: send to AI"))
                continue
            res = apply_template(pdf, tmpl)
            note = "checks passed" if not res["errors"] else f"{len(res['errors'])} check(s) failed: send to AI"
            cols.append((f"{tmpl['carrier']} {tmpl['product']}", res["summary"], note))
        rows = ["premium", "ltc_pool_day1", "ltc_monthly_day1", "ltc_pool_year5", "ltc_monthly_year5",
                "ltc_pool_age80", "ltc_monthly_age80", "ltc_pool_age85", "ltc_monthly_age85",
                "leverage_day1", "leverage_year5", "death_benefit_year1", "surrender_value_year1",
                "surrender_value_year10", "surrender_received_year1", "surrender_received_year10"]
        w = 24
        print("\n" + " " * 26 + "".join(f"{c[0][:w - 1]:<{w}}" for c in cols))
        for r in rows:
            vals = [c[1].get(r) if c[1] else None for c in cols]
            if all(v is None for v in vals):
                continue
            cells = []
            for v in vals:
                if v is None:
                    cells.append("-")
                elif r.startswith("leverage"):
                    cells.append(f"{v:.2f}x")
                else:
                    cells.append(f"${v:,.0f}")
            print(f"{r:<26}" + "".join(f"{x:<{w}}" for x in cells))
        print(f"{'':<26}" + "".join(f"{c[2][:w - 1]:<{w}}" for c in cols))
        return

    if args.cmd == "learn":
        tmpl, replay, problems, result, leaks = learn(args.pdf, args.answer)
        print(f"\nlearned {tmpl['carrier']} {tmpl['product']}  layout {tmpl['layout_key']}")
        print("  replay on the same PDF:")
        for key, want, got, ok in replay:
            print(f"    {'ok  ' if ok else 'MISS'} {key:<26} {got if got is not None else '(not found)'}"
                  + ("" if ok else f"   expected {want}"))
        for p in problems:
            print(f"  could not learn: {p}")
        for t in tmpl["tables"]:
            print(f"  table {t['key']}: {t['cells']} cells per row; checks learned: "
                  + ", ".join(i["type"] + (f" {i['b']}={i['times']}x{i['a']}" if i["type"] == "ratio" else
                                           f" {i['col']}" if "col" in i else "") for i in t["invariants"]))
        print(f"  links: " + ", ".join(f"{x['field']}={x['table']}.{x['col']}@{next(iter(x['where']))} "
                                       f"{fmt_num(next(iter(x['where'].values())))}" for x in tmpl["links"]))
        print_result(result)
        if leaks:
            print(f"  NOT SAVED: template would contain private values: {leaks}")
            sys.exit(1)
        if not args.dry_run:
            os.makedirs(TEMPLATES, exist_ok=True)
            with open(template_path(tmpl["layout_key"]), "w") as f:
                json.dump(tmpl, f, indent=1)
            print(f"  saved {os.path.relpath(template_path(tmpl['layout_key']), os.path.dirname(HERE))} (status: trial)")
        return

    catalog = json.load(open(T.CATALOG))
    if args.cmd == "apply" and args.template:
        tmpl = json.load(open(args.template))
    else:
        info = T.analyze(args.pdf, catalog)
        tmpl = find_template(info["layout_key"])
        if not tmpl:
            sys.exit(f"No template for layout {info['layout_key']} ({info['layout']['carrier']} "
                     f"{info['layout']['product']}): send to AI.")
    res = apply_template(args.pdf, tmpl)

    if args.cmd == "apply":
        if args.json:
            print(json.dumps(res, indent=1, default=str))
            return
        print(f"\n{os.path.basename(args.pdf)}: {tmpl['carrier']} {tmpl['product']} "
              f"(template {tmpl['layout_key']}, {tmpl['status']})")
        print_result(res)
        sys.exit(1 if res["errors"] else 0)

    if args.cmd == "verify":
        ans = json.load(open(args.answer))
        diffs = [(a["key"], a["value"], res["fields"].get(a["key"])) for a in ans["fields"]
                 if a["key"] in res["fields"] and not same(a["kind"], res["fields"][a["key"]], a["value"])]
        print(f"\n{os.path.basename(args.pdf)} vs checked answer:")
        for k, want, got in diffs:
            print(f"  DIFF {k}: template {got}, answer {want}")
        fp = T.sha256_file(args.pdf)[:16]
        seen = tmpl.setdefault("confirmed_files", [])
        if not diffs and not res["errors"] and fp in seen:
            print(f"  agrees, but this file was already counted ({tmpl.get('agreements', 0)}/{TRUST_AFTER}); "
                  f"confirmations must come from different illustrations")
            return
        if not diffs and not res["errors"]:
            seen.append(fp)
            tmpl["agreements"] = tmpl.get("agreements", 0) + 1
            if tmpl["agreements"] >= TRUST_AFTER:
                tmpl["status"] = "trusted"
            print(f"  agrees ({tmpl['agreements']}/{TRUST_AFTER}); status {tmpl['status']}")
        else:
            tmpl["status"], tmpl["agreements"], tmpl["confirmed_files"] = "trial", 0, []
            print("  disagrees: template back to trial; relearn from this document")
        with open(template_path(tmpl["layout_key"]), "w") as f:
            json.dump(tmpl, f, indent=1)


if __name__ == "__main__":
    main()
