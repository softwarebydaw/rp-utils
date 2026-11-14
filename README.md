# rp-utils

Utility programs that support LTC-Design work.

| Utility | What it does | Instructions |
|---|---|---|
| [pdf-replace](pdf-replace/) | Find and replace a text value in a PDF | [pdf-replace/INSTRUCTIONS.md](pdf-replace/INSTRUCTIONS.md) |

## Setup

Python 3.9 or newer. Each utility's run script creates a local virtual environment
(`.venv/` at the repo root) and installs `requirements.txt` the first time it runs.
To set it up by hand:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
