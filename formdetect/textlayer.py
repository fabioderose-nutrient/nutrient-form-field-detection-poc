"""Words and their positions from the PDF text layer, and the text around each field.

Flat forms printed from a word processor keep their text layer, so the labels can be read with their
exact positions. Underscores and dots typed as text ("Name: ________") are "filler": they mark a
place to write, not a label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

FILLER = re.compile(r"^[_.…\-]{3,}$")
TRAILING_FILLER = re.compile(r"[_.…]{3,}$")


@dataclass
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool

    @property
    def filler(self) -> bool:
        return bool(FILLER.match(self.text))

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class PageText:
    number: int
    width: float
    height: float
    words: list[Word]

    @property
    def labels(self) -> list[Word]:
        return [w for w in self.words if not w.filler]


def read_pages(pdf: Path) -> list[PageText]:
    pages = []
    with pdfplumber.open(str(pdf)) as doc:
        for i, page in enumerate(doc.pages, start=1):
            words = []
            for w in page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False):
                text = w["text"]
                if not FILLER.match(text) and TRAILING_FILLER.search(text):
                    # "name:_____" written without a space: keep the label part.
                    text = TRAILING_FILLER.sub("", text)
                words.append(Word(text, w["x0"], w["top"], w["x1"], w["bottom"], round(w["size"], 1),
                                  "bold" in w["fontname"].lower()))
            pages.append(PageText(i, float(page.width), float(page.height), words))
    return pages


def _join(words: list[Word]) -> str:
    return " ".join(w.text for w in sorted(words, key=lambda w: w.x0)).strip()


def _same_line(w: Word, band: tuple[float, float]) -> bool:
    overlap = min(w.bottom, band[1]) - max(w.top, band[0])
    return overlap >= 0.5 * (w.bottom - w.top)


def context(field: dict, page: PageText, others: list[dict]) -> dict:
    """Text to the left, right, above and below a field, and the section heading above it."""
    x0, y0, x1, y1 = field["rect"]
    words = page.labels
    # The band where a label on the same line sits: the lower part of a field drawn on a line, the
    # middle of a box.
    h = y1 - y0
    band = (y1 - 16, y1 + 2) if h < 34 and field.get("kind") != "checkbox" else ((y0 + y1) / 2 - 7, (y0 + y1) / 2 + 7)

    def blocked(a: float, b: float, w: Word) -> bool:
        """Another field between a and b, at the height of the word w."""
        for o in others:
            if o is field or o["page"] != field["page"]:
                continue
            ox0, oy0, ox1, oy1 = o["rect"]
            if ox1 > a + 1 and ox0 < b - 1 and min(oy1, w.bottom) - max(oy0, w.top) > 0.5 * (w.bottom - w.top):
                return True
        return False

    left = []
    for w in sorted((w for w in words if w.x1 <= x0 + 2 and _same_line(w, band)), key=lambda w: -w.x1):
        edge = left[-1].x0 if left else x0
        # The first word may be far away (a label in the next table cell); then words must be close.
        if edge - w.x1 > (24 if left else 160) or blocked(w.x1, edge, w):
            break
        left.append(w)

    right = []
    for w in sorted((w for w in words if w.x0 >= x1 - 2 and _same_line(w, band)), key=lambda w: w.x0):
        edge = right[-1].x1 if right else x1
        if w.x0 - edge > 12 or blocked(edge, w.x0, w):
            break
        right.append(w)

    def line_near(candidates: list[Word], nearest_first) -> list[Word]:
        if not candidates:
            return []
        first = sorted(candidates, key=nearest_first)[0]
        return [w for w in candidates if abs(w.cy - first.cy) < 3]

    horizontal = [w for w in words if w.x1 > x0 - 4 and w.x0 < x1 + 4]
    above = line_near([w for w in horizontal if y0 - 40 <= w.bottom <= y0 + 3 and w.top < y0], lambda w: y0 - w.bottom)
    # A caption under a line ("Signature") is small, plain text close to it; a bold line is a heading.
    below = line_near([w for w in horizontal if y1 - 3 <= w.top <= y1 + 16 and not w.bold], lambda w: w.top - y1)
    header = line_near([w for w in horizontal if y0 - 130 <= w.bottom < y0 - 40 and w.bold], lambda w: y0 - w.bottom)
    # The whole text line above, wherever it starts: the question over a row of check boxes.
    line_above = line_near([w for w in words if y0 - 40 <= w.bottom <= y0 + 1 and w.top < y0], lambda w: y0 - w.bottom)
    headings = [w for w in words if w.bold and w.size >= 11 and w.bottom <= y0 + 2]
    section = line_near(headings, lambda w: y0 - w.bottom)

    # Words on the left split where the gap is wide: "Was first aid given?   Yes [ ]" -> question, option.
    left_words = list(reversed(left))
    near = left_words
    for i in range(len(left_words) - 1, 0, -1):
        if left_words[i].x0 - left_words[i - 1].x1 > 8:
            near = left_words[i:]
            break

    return {
        "left": _join(left_words),
        "left_near": _join(near),
        "right": _join(right),
        "above": _join(above),
        "above_gap": round(y0 - max(w.bottom for w in above), 1) if above else None,
        "below": _join(below),
        "below_gap": round(min(w.top for w in below) - y1, 1) if below else None,
        "line_above": _join(line_above),
        "column_header": _join(header),
        "section": _join(section),
    }


TEXT_KEYS = ("left", "right", "above", "below", "line_above", "column_header", "section")
