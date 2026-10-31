"""From a flat PDF form to a list of proposed fields: position, kind, label, name and type."""

from __future__ import annotations

import time
from pathlib import Path

from . import geometry, naming, sdk
from .textlayer import context, read_pages

DPI = 200


def detect(pdf: Path, use_model: bool = True, use_geometry: bool = True, vlm_labels: bool = False,
           min_confidence: float | None = None) -> dict:
    """Detection only (no names yet): the SDK model's fields, geometry proposals, and the combined list."""
    min_confidence = float(sdk.env("MIN_CONFIDENCE", "0.5")) if min_confidence is None else min_confidence
    timings = {}
    t = time.time()
    pages = read_pages(pdf)
    model = sdk.detect_fields(pdf, vlm_labels) if use_model else []
    timings["model"] = round(time.time() - t, 2)
    t = time.time()
    found: list[dict] = []
    if use_geometry:
        for page, image in zip(pages, sdk.page_images(pdf, DPI)):
            found.extend(geometry.proposals(image, DPI, page))
    timings["geometry"] = round(time.time() - t, 2)
    return {"pages": pages, "model": model, "geometry": found, "timings": timings, "min_confidence": min_confidence}


def assemble(detected: dict, use_model: bool = True, use_geometry: bool = True, labels: str = "llm") -> dict:
    """Combine the proposals, find the text around each field, then name and type it."""
    pages = detected["pages"]
    model = detected["model"] if use_model else []
    found = detected["geometry"] if use_geometry else []
    fields = [dict(f) for f in geometry.combine(model, found, detected["min_confidence"])]

    def has_left_label(f: dict) -> bool:
        return bool(context(f, pages[f["page"] - 1], fields)["left"])

    fields = geometry.merge_ruled_blocks(fields, has_left_label)
    fields.sort(key=lambda f: (f["page"], round(f["rect"][1] / 6), f["rect"][0]))
    for i, f in enumerate(fields, start=1):
        f["id"] = i
        f["context"] = context(f, pages[f["page"] - 1], fields)

    _row_questions(fields)
    _continuation_lines(fields)

    t = time.time()
    if labels in ("llm", "llm-only"):
        naming.name_with_llm(fields, mode="mix" if labels == "llm" else "llm-only")
    else:
        if labels == "sdk-vlm":
            for f in fields:
                f["sdk_label_text"] = (f.get("sdk_label") or "").rstrip(": ")
        naming.name_with_rules(fields)
    naming.make_unique(fields)
    seconds_naming = round(time.time() - t, 2)

    out_fields = []
    for f in fields:
        out_fields.append({
            "id": f["id"], "page": f["page"], "rect": [round(v, 1) for v in f["rect"]],
            "name": f["name"], "label": f["label"], "type": f["type"],
            "kind": f["kind"], "source": f["source"], "confidence": f["confidence"], "snapped_to": f.get("snapped_to"),
            "counted_name": bool(f.get("counted")),
            "context": f["context"],
        })
    return {
        "pages": [{"page": p.number, "width": p.width, "height": p.height} for p in pages],
        "fields": out_fields,
        "settings": {"model": use_model, "geometry": use_geometry, "labels": labels,
                     "min_confidence": detected["min_confidence"], "llm": naming.llm_model() if labels.startswith("llm") else None},
        "timings": {**detected["timings"], "naming": seconds_naming},
    }


def _row_questions(fields: list[dict]) -> None:
    """The question of a row of check boxes: the text left of its first box; for the second row of a grid of
    options, the question of the row above; otherwise the text line above the row, or the section."""
    rows: dict[tuple[int, int], list[dict]] = {}
    for f in fields:
        if f["kind"] == "checkbox":
            rows.setdefault((f["page"], round(f["rect"][1] / 4)), []).append(f)
    previous = None
    for key in sorted(rows):
        row = sorted(rows[key], key=lambda o: o["rect"][0])
        c = row[0]["context"]
        if c["right"] or c["left"] == c["left_near"]:
            question = c["left"]  # options on the right of the boxes
        else:
            question = c["left"][: len(c["left"]) - len(c["left_near"])].strip()  # options on the left
        if (not question and previous and previous["page"] == key[0] and abs(previous["x"] - row[0]["rect"][0]) < 6
                and 0 < row[0]["rect"][1] - previous["top"] < 40):
            question = previous["question"]
        question = question or c["line_above"] or c["section"]
        for f in row:
            f["row_question"] = question
        previous = {"page": key[0], "x": row[0]["rect"][0], "top": row[0]["rect"][1], "question": question}


def _continuation_lines(fields: list[dict]) -> None:
    """A line with no caption right under a captioned line of the same width continues it ("Home address")."""
    for f in fields:
        if f["kind"] != "text" or f["context"]["left"]:
            continue
        for o in fields:
            if (o is not f and o["page"] == f["page"] and o["kind"] == "text" and o["context"]["left"]
                    and 0 <= f["rect"][1] - o["rect"][3] < 12 and abs(o["rect"][2] - f["rect"][2]) < 10):
                f["continues"] = o


def analyse(pdf: Path, labels: str = "llm", use_model: bool = True, use_geometry: bool = True) -> dict:
    detected = detect(pdf, use_model, use_geometry, vlm_labels=(labels == "sdk-vlm"))
    result = assemble(detected, use_model, use_geometry, labels)
    result["form"] = pdf.stem
    return result
