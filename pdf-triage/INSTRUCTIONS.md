# pdf-triage: first-pass check for illustrations (no AI)

A fast check that runs on an uploaded PDF **before** any AI call. It answers two questions:

- **Comparison:** do we need to read this document to build the client's side-by-side, or do we
  already have its values?
- **Library:** does it add a new example to the repository, or do we already have one like it?

It takes well under a second per file and costs nothing.

## Quick start (prompted)

```bash
cd rp-utils
./pdf-triage/run.sh
```

Choose **check**, **record** or **list**. For check and record, enter one PDF or folder per line
(drag from Finder works; spaces are fine), then press Enter on an empty line.

## Command line

Quote paths that contain spaces.

```bash
.venv/bin/python pdf-triage/triage.py check "samples/input"
.venv/bin/python pdf-triage/triage.py check "Client A.pdf" "Client B.pdf" --json
.venv/bin/python pdf-triage/triage.py record "samples/input" --source ai
.venv/bin/python pdf-triage/triage.py list
```

- `check` reports and changes nothing. Files in one run are also compared with each other.
- `record` adds illustrations to the index once they have been processed (`--source ai|parser|manual`).
- `--index FILE` uses a different index (default `.triage/index.json`, not committed).

## What it checks, in order

| Step | How | Result |
|---|---|---|
| Readable? | Password, and whether most pages have a text layer | `stop: password protected` / `ocr then re-check` |
| What is it? | Counts different signal phrases; two or more carriers plus comparison wording means a comparison report | rate letter and policy are routed; comparison reports and other documents are skipped |
| Same file? | SHA-256 of the bytes | `reuse earlier result (exact file)` |
| Same numbers? | Fingerprint of every $, % and amount in order; names, dates, ages and case IDs are left out. 97% or more in common counts as the same | `reuse earlier result (same numbers)` |
| Same quote inputs? | Carrier + product + form numbers + age + sex + state + premium + rate class, within 6 months | library: `skip: profile already in library`. The comparison still needs this document read, because the numbers differ |
| Known layout? | Carrier + product + form numbers + PDF producer | trusted template: `read with template (no AI)`; trial template: `read with template and AI, compare`; none: `send to AI` |

A layout seen several times without a template is reported as a **parser candidate**.

## Reading templates (learn once with AI, then read without it)

The first illustration of a new layout goes to AI. Besides the values, the AI returns a short
**reading guide**; a person checks it, and the result is an **answer file**. `template.py`
learns a template from it, and later illustrations with the same layout are read without AI.

```bash
.venv/bin/python pdf-triage/template.py learn   "Illustration.pdf" "answer.json"
.venv/bin/python pdf-triage/template.py apply   "Another one.pdf"
.venv/bin/python pdf-triage/template.py compare "A.pdf" "B.pdf" "C.pdf"
.venv/bin/python pdf-triage/template.py verify  "Another one.pdf" "its-answer.json"
```

- **learn** finds every value by its printed label, records the table layout, learns checks that
  held on every row (years count up, age = year + constant, annual = 12 x monthly, a column that
  never goes down, ...) and links between single values and table cells (premium = year-1
  premium, pool at age 85 = table row at age 85). It then **replays** the template on the same
  PDF: only values that come back exactly are kept. Saved to `templates/<layout>.json`.
- **apply** reads a PDF with its layout's template and runs the checks. Any failed check means:
  send to AI and relearn from that document.
- **compare** prints the side-by-side comparison numbers for several PDFs (pool and monthly
  benefit on day 1, year 5 when benefits build up, ages 80 and 85, leverage, death benefit,
  surrender values).
- **verify** compares a template read with a checked answer for a *different* illustration of the
  same layout. Three agreements move a template from **trial** (template and AI both run) to
  **trusted** (template only). A disagreement sends it back to trial.

### What the AI adds to its answer (the reading guide)

For each value: the printed **label** next to it and the **page**. For a page where the label
is not unique, the page's **heading** (`page_headings`). For each year-by-year table: its page
heading, the column headings in order, and **two sample rows** (year 1 and the last year).
Optional per value: `skip_tokens` (ignore the first words after the label) and `match_case`
(for an upper-case label such as `AGE` in a footer). See `samples/answers/` (not in git) for the
three learned layouts.

### Testing a template before a second illustration exists

```bash
.venv/bin/python pdf-triage/variants.py samples/variants/securian.json
```

Makes altered copies with pdf-replace (new premium, sex, class, face amount; one inconsistent
cell; a renamed label) and checks the template reads the new values and its checks catch the
broken ones. Specs live in `samples/variants/` (not in git).

### What a template stores

Labels, page headings, column positions, check rules and link rules. No client values, names,
agent details or case IDs; `learn` refuses to save if any value or name from the answer file's
`private` list appears in it. Templates hold no client data, so they can be shared across agencies.

## What the index stores

File and value fingerprints, the layout, and the quote inputs (carrier, product, forms, age, sex,
state, premium, rate class, month). It does **not** store names, file names, agent details, case
IDs or document text. Value tokens are stored only as hashes.

## Files

- `triage.py` the check
- `catalog.json` carrier names and aliases, product names, and the list of layouts that have a
  rule-based parser (empty for now). Add carriers and products here as you meet them.
- `selftest.py` builds made-up PDFs for every decision path and checks the results:
  `.venv/bin/python pdf-triage/selftest.py`
- `template.py` learn, apply, compare and verify reading templates
- `templates/` learned templates, one per layout (currently Securian SecureCare IV, Brighthouse
  SmartCare, EquiTrust Bridge; all on trial until confirmed by three more illustrations each)
- `variants.py` stress-tests a template with altered copies of its learning PDF

## Limits (prototype)

- Age, sex, state, premium and date are read with patterns from the sample carriers (Securian,
  Brighthouse, EquiTrust). A new carrier may need a pattern or a `catalog.json` entry; missing
  inputs are reported, and matching then falls back to fingerprints only.
- The quote profile does not include benefit period, inflation or riders. That is why a profile
  match never reuses values for a comparison; it only stops a duplicate library example.
- Scanned PDFs are only detected, not read. OCR is a later step.
- Templates were each learned from one illustration and tested with altered copies. The real test
  is the second genuine illustration of each product, read in trial mode next to AI.
- A table row must start with policy year and age; a carrier that prints them differently needs
  a small change to the row finder.
