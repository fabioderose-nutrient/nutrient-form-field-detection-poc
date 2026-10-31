"""Field proposals from the page image (OpenCV) and the text layer, and the rules that combine them
with the SDK model's fields.

Geometry finds what a form designer draws to collect an answer:
- empty boxes and empty table cells (white areas closed by lines; a cell with text is a label);
- lines with free space above them, where you write; a line right under text is a separator;
- underscores and dots typed as text ("Name: ______", "Witness: ........");
- small empty squares: check boxes.
"""

from __future__ import annotations

import cv2
import numpy as np

from .textlayer import PageText

LINE_FIELD_HEIGHT = 18.0  # a field on a line covers this many points above it


def iou(a: list[float], b: list[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def overlap_of_smaller(a: list[float], b: list[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / smaller if smaller > 0 else 0.0


def _has_text(page: PageText, rect: list[float]) -> bool:
    """A label word has its centre inside the rectangle."""
    x0, y0, x1, y1 = rect
    return any(x0 < (w.x0 + w.x1) / 2 < x1 and y0 < w.cy < y1 for w in page.labels)


def _text_overlaps(page: PageText, rect: list[float]) -> bool:
    x0, y0, x1, y1 = rect
    return any(w.x1 > x0 and w.x0 < x1 and w.bottom > y0 and w.top < y1 for w in page.labels)


def proposals(gray: np.ndarray, dpi: int, page: PageText) -> list[dict]:
    s = 72.0 / dpi  # points per pixel
    px = lambda pt: max(1, int(round(pt / s)))
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (px(25), 1)))
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, px(14))))
    found: list[dict] = []

    def add(rect: list[float], kind: str, how: str) -> None:
        found.append({"page": page.number, "rect": [round(v, 2) for v in rect], "kind": kind,
                      "confidence": None, "source": "geometry", "geometry": how})

    # Boxes and table cells: white areas fully closed by lines.
    grid = cv2.dilate(horiz | vert, np.ones((3, 3), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(255 - grid, connectivity=4)
    height, width = gray.shape
    cells = []
    for x, y, w, h, area in stats[1:]:
        if x == 0 or y == 0 or x + w >= width or y + h >= height:
            continue  # the page background
        if w * s < 25 or h * s < 12 or h * s > 300 or area < 0.85 * w * h:
            continue
        rect = [(x - 1) * s, (y - 1) * s, (x + w + 1) * s, (y + h + 1) * s]
        cells.append(rect)
        if not _has_text(page, rect):
            add(rect, "text", "box")

    # Lines with free space above them.
    n, _, stats, _ = cv2.connectedComponentsWithStats(horiz, connectivity=8)
    for x, y, w, h, _ in stats[1:]:
        if w * s < 40 or h * s > 4:
            continue
        x0, x1, ly = x * s, (x + w) * s, (y + h / 2) * s
        on_cell_edge = any(min(x1, c[2]) - max(x0, c[0]) > 0.5 * (x1 - x0) and (abs(ly - c[1]) < 3 or abs(ly - c[3]) < 3)
                           for c in cells)
        if on_cell_edge or _text_overlaps(page, [x0, ly - 14, x1, ly - 1]):
            continue
        add([x0, ly - LINE_FIELD_HEIGHT, x1, ly + 1], "text", "line")

    # Underscores and dots typed as text.
    for w in page.words:
        if w.filler and (w.text.count("_") >= 4 or w.text.count(".") >= 6):
            add([w.x0, w.bottom - LINE_FIELD_HEIGHT, w.x1, w.bottom], "text", "typed")

    # Check boxes: small, square, empty, not a letter of the text layer.
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (6 <= w * s <= 18 and 6 <= h * s <= 18 and 0.8 <= w / h <= 1.25):
            continue
        if cv2.contourArea(c) < 0.6 * w * h or len(cv2.approxPolyDP(c, 0.08 * cv2.arcLength(c, True), True)) != 4:
            continue
        inner = binary[y + 3:y + h - 3, x + 3:x + w - 3]
        if inner.size == 0 or inner.mean() / 255 > 0.08:
            continue
        rect = [x * s, y * s, (x + w) * s, (y + h) * s]
        if not _has_text(page, rect):
            add(rect, "checkbox", "square")

    # One proposal per place: the same line can be found as a drawn line and as typed underscores.
    kept: list[dict] = []
    for f in sorted(found, key=lambda f: (f["rect"][2] - f["rect"][0]) * (f["rect"][3] - f["rect"][1])):
        if all(iou(f["rect"], k["rect"]) < 0.5 for k in kept):
            kept.append(f)
    return kept


def _snap(field: dict, geometry: list[dict]) -> dict | None:
    """The geometry proposal that draws the same field exactly: the line under a text field, the same box
    or the same check box."""
    x0, y0, x1, y1 = field["rect"]
    best, best_score = None, 0.0
    for g in geometry:
        if g["page"] != field["page"] or (g["kind"] == "checkbox") != (field["kind"] == "checkbox"):
            continue
        gx0, gy0, gx1, gy1 = g["rect"]
        if g.get("geometry") in ("line", "typed"):
            across = max(0.0, min(x1, gx1) - max(x0, gx0)) / max(x1 - x0, gx1 - gx0)
            score = across if across >= 0.8 and y1 - 8 <= gy1 <= y1 + 4 and y1 - y0 < 34 else 0.0
        else:
            score = iou(field["rect"], g["rect"])
            score = score if score >= 0.7 else 0.0
        if score > best_score:
            best, best_score = g, score
    return best


def combine(model: list[dict], geometry: list[dict], min_confidence: float) -> list[dict]:
    """The model's fields above the confidence threshold, snapped to the exact lines and boxes that geometry
    found, plus geometry proposals in places the model left empty."""
    fields = []
    for f in model:
        if f["confidence"] < min_confidence:
            continue
        f = dict(f)
        g = _snap(f, geometry) if geometry else None
        if g:
            f["rect"] = list(g["rect"])
            f["snapped_to"] = g["geometry"]
        fields.append(f)
    for g in geometry:
        clash = any(f["page"] == g["page"] and (iou(f["rect"], g["rect"]) >= 0.3 or overlap_of_smaller(f["rect"], g["rect"]) >= 0.6)
                    for f in fields)
        if not clash:
            fields.append(g)
    return fields


def merge_ruled_blocks(fields: list[dict], has_left_label) -> list[dict]:
    """Consecutive lines of the same width with no label of their own form one multi-line answer area,
    like the ruled lines under "Describe what happened:"."""
    lines = sorted((f for f in fields if f["kind"] == "text" and f["rect"][3] - f["rect"][1] < 34 and not has_left_label(f)),
                   key=lambda f: (f["page"], f["rect"][1]))
    runs: list[list[dict]] = []
    for f in lines:
        last = runs[-1][-1] if runs else None
        if (last and last["page"] == f["page"] and abs(last["rect"][0] - f["rect"][0]) <= 10
                and abs(last["rect"][2] - f["rect"][2]) <= 10 and 12 <= f["rect"][1] - last["rect"][1] <= 40):
            runs[-1].append(f)
        else:
            runs.append([f])
    merged_away = set()
    result = []
    for run in runs:
        if len(run) < 2:
            continue
        merged_away.update(id(f) for f in run)
        confidences = [f["confidence"] for f in run if f["confidence"] is not None]
        result.append({"page": run[0]["page"],
                       "rect": [min(f["rect"][0] for f in run), run[0]["rect"][1], max(f["rect"][2] for f in run), run[-1]["rect"][3]],
                       "kind": "text", "multiline": True, "confidence": min(confidences) if confidences else None,
                       "source": "merged", "merged_lines": len(run)})
    return [f for f in fields if id(f) not in merged_away] + result
