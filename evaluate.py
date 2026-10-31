"""Measure field detection and naming against the ground truth written by samples/generate_forms.py.

A proposed field matches a true field when they are on the same page and their rectangles overlap
with an intersection over union of at least 0.5 (one-to-one, best overlaps first).

- precision: matched / proposed; recall: matched / true fields; mean IoU: how exactly the matched
  rectangles sit on the true ones (1.0 = same rectangle);
- kind: text area, check box or signature, as the detector or the naming step decided;
- type: the fine type (text, multiline, date, email, phone, number, checkbox, signature);
- name: exact (same snake_case name) or close (at least half of the words in common, ignoring
  little words such as "of"); label: close, with the same rule.

The forms marked "held_out" in their ground truth were not used to write the rules; their scores are
also shown on their own.

Configurations: the SDK model alone, the geometry alone, both, each named with rules; then both,
named by the LLM (the default pipeline). --with-sdk-vlm adds the SDK's own labeling with a local
vision model (slow: a few minutes per page).

Usage: python evaluate.py [--forms samples/forms] [--output output] [--with-sdk-vlm]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from formdetect import naming, pipeline
from formdetect.geometry import iou

LITTLE_WORDS = {"of", "the", "a", "an", "and", "or", "if", "please", "your", "you", "in", "for", "to", "is", "was"}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+|\d+", text.lower().replace("_", " ")) if t not in LITTLE_WORDS}


def close(a: str, b: str) -> bool:
    ta, tb = tokens(a), tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


def kind_of(t: str) -> str:
    return t if t in ("checkbox", "signature") else "text"


def match(truth: list[dict], proposed: list[dict]) -> list[tuple[dict, dict]]:
    pairs = sorted(((iou(t["rect"], p["rect"]), i, j) for i, t in enumerate(truth) for j, p in enumerate(proposed)
                    if t["page"] == p["page"]), reverse=True)
    used_t, used_p, matched = set(), set(), []
    for score, i, j in pairs:
        if score < 0.5:
            break
        if i in used_t or j in used_p:
            continue
        used_t.add(i)
        used_p.add(j)
        matched.append((truth[i], proposed[j]))
    return matched


def score(results: list[tuple[dict, dict]]) -> dict:
    n_true = sum(len(t["fields"]) for t, _ in results)
    n_prop = sum(len(r["fields"]) for _, r in results)
    pairs = [p for t, r in results for p in match(t["fields"], r["fields"])]
    m = len(pairs)
    pct = lambda k, n: f"{k}/{n} ({100 * k / n:.0f}%)" if n else "-"
    return {
        "proposed": n_prop, "true": n_true, "matched": m,
        "precision": pct(m, n_prop), "recall": pct(m, n_true),
        "mean_iou": round(sum(iou(t["rect"], p["rect"]) for t, p in pairs) / m, 3) if m else None,
        "kind": pct(sum(kind_of(t["type"]) == kind_of(p["type"]) for t, p in pairs), m),
        "type": pct(sum(t["type"] == p["type"] for t, p in pairs), m),
        "name_exact": pct(sum(t["name"] == p["name"] for t, p in pairs), m),
        "name_close": pct(sum(close(t["name"], p["name"]) for t, p in pairs), m),
        "label_close": pct(sum(close(t["label"], p["label"]) for t, p in pairs), m),
        "counted_names": sum(f.get("counted_name", False) for _, r in results for f in r["fields"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--forms", default="samples/forms")
    parser.add_argument("--output", default=naming.env("OUTPUT_FOLDER", "output"))
    parser.add_argument("--with-sdk-vlm", action="store_true")
    args = parser.parse_args()

    forms = sorted(Path(args.forms).glob("*.pdf"))
    truths = {f.stem: json.loads(f.with_name(f.stem + ".fields.json").read_text(encoding="utf-8")) for f in forms}
    configs = [("SDK model, rule names", True, False, "rules"),
               ("Geometry, rule names", False, True, "rules"),
               ("SDK model + geometry, rule names", True, True, "rules"),
               (f"SDK model + geometry, LLM only ({naming.llm_model()})", True, True, "llm-only"),
               (f"SDK model + geometry, rule captions and types + LLM names ({naming.llm_model()})", True, True, "llm")]
    if args.with_sdk_vlm:
        configs.append((f"SDK model + geometry, SDK labeling ({naming.env('VLM_MODEL', 'qwen2.5vl:7b')})", True, True, "sdk-vlm"))

    detected = {}
    for f in forms:
        detected[f.stem] = pipeline.detect(f)
    report = {"configurations": {}, "details": {}}
    for title, use_model, use_geometry, labels in configs:
        results, seconds = [], 0.0
        for f in forms:
            t = time.time()
            d = pipeline.detect(f, vlm_labels=True) if labels == "sdk-vlm" else detected[f.stem]
            r = pipeline.assemble(d, use_model, use_geometry, labels)
            seconds += time.time() - t + (d["timings"]["model"] if use_model else 0) + (d["timings"]["geometry"] if use_geometry else 0)
            results.append((truths[f.stem], r))
            if labels in ("llm", "llm-only", "sdk-vlm"):
                report["details"][f"{labels}:{f.stem}"] = [
                    {"truth": {k: t[k] for k in ("name", "type", "label")}, "proposed": {k: p[k] for k in ("name", "type", "label", "source")}}
                    for t, p in match(truths[f.stem]["fields"], r["fields"])]
        s = score(results)
        s["seconds"] = round(seconds, 1)
        held_out = [(t, r) for t, r in results if t.get("held_out")]
        report["configurations"][title] = {"all forms": s, "held-out form": score(held_out) if held_out else None}
        print(f"\n{title}")
        h = score(held_out) if held_out else {}
        for k, v in s.items():
            print(f"  {k:14s} {str(v):16s} {'held out: ' + str(h[k]) if k in h else ''}")

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"\nDetails in {out / 'evaluation.json'}")


if __name__ == "__main__":
    main()
