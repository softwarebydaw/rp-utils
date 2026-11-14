#!/usr/bin/env python3
"""Stress-test a reading template with altered copies of the illustration it was learned from.

Until a second real illustration of a layout arrives, this is the way to show a template
reads values by their labels (not by remembering them) and that its checks catch
inconsistent numbers. Each variant applies text replacements with pdf-replace, then reads
the result with template.py apply.

usage: variants.py SPEC.json [SPEC.json ...]

SPEC (paths relative to the repo root):
{
  "pdf": "samples/input/Example.pdf",
  "out": "samples/variants/out-example",
  "variants": {
    "new_values": {"expect": "pass", "changes": [["$100,000", "$150,000"]], "show": ["premium"]},
    "inconsistent": {"expect": "fail", "changes": [["$5,265", "$5,999"]]}
  }
}
"expect": "pass" means the template's checks should pass, "fail" that they should catch it.
"""
import contextlib
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pdf-replace"))
from replace_pdf import replace_in_pdf  # noqa: E402

PY = sys.executable


def run(spec_path):
    spec = json.load(open(spec_path))
    pdf = os.path.join(ROOT, spec["pdf"])
    out = os.path.join(ROOT, spec["out"])
    os.makedirs(out, exist_ok=True)
    bad = 0
    print(f"== {os.path.basename(spec_path)}")
    for name, v in spec["variants"].items():
        src = pdf
        for i, (old, new) in enumerate(v["changes"]):
            dst = os.path.join(out, f"{name}.step{i}.pdf")
            with contextlib.redirect_stdout(io.StringIO()):
                n = replace_in_pdf(src, dst, old, new, fit=True)
            if not n:
                print(f"  [{name}] change not applied: {old!r}")
                bad += 1
            src = dst
        final = os.path.join(out, f"{name}.pdf")
        os.replace(src, final)
        for f in os.listdir(out):
            if f.startswith(f"{name}.step"):
                os.remove(os.path.join(out, f))
        r = subprocess.run([PY, os.path.join(ROOT, "pdf-triage", "template.py"), "apply", final],
                           capture_output=True, text=True)
        passed = r.returncode == 0
        ok = passed == (v["expect"] == "pass")
        bad += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}: checks {'passed' if passed else 'failed'} (expected {v['expect']})")
        for line in (r.stdout + r.stderr).splitlines():
            if any(k in line for k in v.get("show", [])) or "ERROR" in line or "No template" in line:
                print("      " + line.strip())
    return bad


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = sum(run(p) for p in sys.argv[1:])
    print("all as expected" if not bad else f"{bad} unexpected")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
