"""Propose the fields of flat PDF forms: position, kind, label, name and type.

Writes output/proposals/<form>.json for every PDF of the input folder (or for one --file).

Usage: python detect.py [--input samples/forms] [--file form.pdf] [--output output]
                        [--labels llm|rules|llm-only|sdk-vlm] [--no-geometry]

  --labels llm       captions and types by layout rules, names by the local LLM (default)
  --labels rules     captions, types and names by layout rules only (no LLM, instant)
  --labels llm-only  the LLM decides captions, names and types from the text around each field
  --labels sdk-vlm   the Nutrient SDK's own AI labeling with a vision model (slow)
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from formdetect import pipeline
from formdetect.sdk import env


def run(pdf: Path, out: Path, labels: str, use_geometry: bool, form_id: str | None = None) -> dict:
    started = time.time()
    result = pipeline.analyse(pdf, labels=labels, use_geometry=use_geometry)
    result["form"] = form_id or pdf.stem
    result["file"] = pdf.name
    result["timings"]["total"] = round(time.time() - started, 2)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{result['form']}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    kinds: dict[str, int] = {}
    for f in result["fields"]:
        kinds[f["type"]] = kinds.get(f["type"], 0) + 1
    summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
    print(f"{pdf.name:34s} {len(result['fields']):3d} fields ({summary})  {result['timings']['total']:.1f} s")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default=env("FORMS_FOLDER", "samples/forms"))
    parser.add_argument("--file", help="one PDF instead of a folder")
    parser.add_argument("--id", help="form id used for the output file (with --file)")
    parser.add_argument("--output", default=env("OUTPUT_FOLDER", "output"))
    parser.add_argument("--labels", default=env("LABELS", "llm"), choices=["llm", "rules", "llm-only", "sdk-vlm"])
    parser.add_argument("--no-geometry", action="store_true")
    args = parser.parse_args()

    out = Path(args.output) / "proposals"
    pdfs = [Path(args.file)] if args.file else sorted(Path(args.input).glob("*.pdf"))
    for pdf in pdfs:
        run(pdf, out, args.labels, not args.no_geometry, args.id if args.file else None)


if __name__ == "__main__":
    main()
