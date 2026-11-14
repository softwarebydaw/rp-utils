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
| Known layout? | Carrier + product + form numbers + PDF producer | `parse with rules` when `catalog.json` lists a parser, else `send to AI` |

A layout seen several times without a parser is reported as a **parser candidate**, which shows
which carriers are worth a rule-based parser first.

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

## Limits (prototype)

- Age, sex, state, premium and date are read with patterns from the sample carriers (Securian,
  Brighthouse, EquiTrust). A new carrier may need a pattern or a `catalog.json` entry; missing
  inputs are reported, and matching then falls back to fingerprints only.
- The quote profile does not include benefit period, inflation or riders. That is why a profile
  match never reuses values for a comparison; it only stops a duplicate library example.
- Scanned PDFs are only detected, not read. OCR is a later step.
