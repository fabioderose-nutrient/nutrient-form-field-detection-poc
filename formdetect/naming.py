"""Name and type every field from the text around it: with simple rules, or with a local LLM."""

from __future__ import annotations

import json
import re
import time

from openai import OpenAI

from .sdk import env
from .textlayer import TEXT_KEYS

TYPES = ["text", "multiline", "date", "email", "phone", "number", "checkbox", "signature"]

SYSTEM = """You prepare a flat PDF form to become a fillable form. For each detected field you get the text printed around it on the page. Decide what each field is for.

Reply with JSON only: {"fields": [{"id": 1, "label": "...", "name": "...", "type": "..."}]}, one entry for every field you were given, with the same ids.

- label: the caption a person filling in the form reads for this field, taken from the nearby text: usually the text on the left, the text above a box, or the text under a signature line. For a table cell, the column header (and the row label if there is one). For a check box: the question and the option, for example "Membership type: Adult". The option of a check box is normally the text on its right; the text on its left is often the option of the previous box.
- name: a short snake_case identifier made from the label, unique within the form, for example full_name, date_of_birth, membership_adult. In a table with several rows, end it with the row number: item_1, item_2.
- type: one of text, multiline, date, email, phone, number, checkbox, signature.
  checkbox only for check boxes; signature where a person signs; date for dates; email and phone for contact details;
  number for quantities, years and purely numeric codes such as bank account numbers and sort codes; multiline for large
  areas meant for several lines; otherwise text. Identifiers that can contain letters (registration, VAT or staff numbers) are text.
Underscores and dots are only places to write, not labels."""

NAMING = """You name the fields of a PDF form that is being made fillable. For each field you get its kind, its caption
(the text printed next to it, found by layout rules) and the other text around it.

Reply with JSON only: {"fields": [{"id": 1, "name": "...", "type": "..."}]}, one entry for every field, same ids.

- name: a short snake_case identifier for the field, unique within the form, built from the caption, for example
  full_name, date_of_birth, membership_adult. In a table with several rows, end it with the row number: item_1, item_2.
- type: one of text, multiline, date, email, phone, number, checkbox, signature (see the meaning of each type below).
  checkbox only for check boxes; signature where a person signs; date for dates; email and phone for contact details;
  number for quantities, years and purely numeric codes such as bank account numbers and sort codes; multiline for large
  areas meant for several lines; otherwise text. Identifiers that can contain letters (registration, VAT or staff numbers) are text."""


def slug(text: str, max_words: int = 6) -> str:
    text = re.sub(r"\(.*?\)", " ", text.lower())
    words = re.findall(r"[a-z0-9]+", text)
    return "_".join(words[:max_words]) or "field"


def _clean_label(text: str) -> str:
    text = re.sub(r"^\d+(\.\d+)*\.?\s+", "", text.strip())  # "3. Type of business" -> "Type of business"
    return re.sub(r"[\s:]+$", "", text).strip()


def rule_type(label: str, field: dict) -> str:
    if field["kind"] == "checkbox":
        return "checkbox"
    l = label.lower()
    if re.search(r"\bsign(ature|ed)?\b", l):
        return "signature"
    if re.search(r"\bdate\b|\bdob\b|birth", l):
        return "date"
    if re.search(r"e-?mail", l):
        return "email"
    if re.search(r"phone|telephone|mobile|\btel\b|extension|\bfax\b", l):
        return "phone"
    if field.get("multiline") or field["rect"][3] - field["rect"][1] >= 40:
        return "multiline" if field["kind"] != "signature" else "signature"
    if re.search(r"quantity|\bqty\b|\byears\b|amount|account number|sort code", l):
        return "number"
    return "signature" if field["kind"] == "signature" else "text"


def rule_label(field: dict) -> str:
    """Nearest caption: left; then just above (a box); then just below (a line); then further away."""
    c = field["context"]
    if field["kind"] == "checkbox":
        option = c["right"] or c["left_near"]
        group = _clean_label(field.get("row_question", ""))
        option = _clean_label(option)
        return f"{group}: {option}" if group and group != option else option
    if c["left"]:
        return _clean_label(c["left"])
    if c["above"] and c["above_gap"] is not None and c["above_gap"] <= 10:
        return _clean_label(c["above"])
    for key in ("below", "above", "column_header", "section"):
        if c[key]:
            return _clean_label(c[key])
    return "Field"


def name_with_rules(fields: list[dict]) -> None:
    for f in fields:
        label = f.get("sdk_label_text") or rule_label(f)
        if f.get("continues") is not None and not f.get("sdk_label_text"):
            label = f"{rule_label(f['continues'])}, line 2"
        f.update(label=label, name=slug(label), type=rule_type(label, f))


def _client() -> OpenAI:
    return OpenAI(base_url=env("LLM_BASE_URL", "http://localhost:11434/v1"), api_key=env("LLM_API_KEY", "not-needed"), timeout=300)


def llm_model() -> str:
    return env("LLM_MODEL", "qwen2.5:7b-instruct")


def _ask(system: str, user: str) -> dict:
    response = _client().chat.completions.create(
        model=llm_model(), temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    try:
        answer = json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return {}
    return answer if isinstance(answer, dict) else {}


def name_with_llm(fields: list[dict], mode: str = "mix") -> float:
    """One LLM call per page; fields the model skips keep the rule values. Returns the seconds spent.

    mode "mix": the rules give the caption (label) and the type; the LLM gives the name, and may only turn a
    plain "text" type into date, email, phone or number. mode "llm-only": the LLM decides label, name and type
    from the text around each field."""
    name_with_rules(fields)
    started = time.time()
    used: list[str] = []
    for page in sorted({f["page"] for f in fields}):
        on_page = [f for f in fields if f["page"] == page]
        items = []
        for f in on_page:
            item = {"id": f["id"], "kind": "check box" if f["kind"] == "checkbox" else ("signature area" if f["kind"] == "signature" else "text area"),
                    "size_pt": f"{f['rect'][2] - f['rect'][0]:.0f} x {f['rect'][3] - f['rect'][1]:.0f}"}
            if mode == "mix":
                item["caption"] = f["label"]
            if f.get("merged_lines"):
                item["note"] = f"{f['merged_lines']} ruled lines"
            item.update({k: f["context"][k] for k in TEXT_KEYS if f["context"][k]})
            if f.get("row_question"):
                item["question_on_this_row"] = f["row_question"]
            items.append(item)
        user = (f"Fields on page {page}, in reading order:\n{json.dumps(items, indent=1)}"
                + (f"\n\nNames already used on earlier pages: {', '.join(used)}" if used else ""))
        answer = _ask(NAMING if mode == "mix" else SYSTEM, user)
        by_id = {f["id"]: f for f in on_page}
        for item in answer.get("fields", []):
            f = by_id.get(item.get("id")) if isinstance(item, dict) else None
            if not f:
                continue
            ftype = str(item.get("type") or "").strip().lower()
            label = str(item.get("label") or "").strip() if mode != "mix" else f["label"]
            raw_name = str(item.get("name") or "").strip() or label
            if raw_name:
                f["name"] = slug(raw_name, max_words=8)
            if mode == "mix":
                if f["type"] == "text" and ftype in ("date", "email", "phone", "number"):
                    f["type"] = ftype
            else:
                if f["kind"] == "checkbox":
                    ftype = "checkbox"  # the detector is reliable on check boxes; a text field cannot become one
                elif ftype not in TYPES or ftype == "checkbox":
                    ftype = rule_type(label or f["label"], {**f, "kind": "text"})
                f.update(label=label or f["label"], type=ftype)
            f["named_by"] = "llm"
        used.extend(f["name"] for f in on_page)
    return time.time() - started


def make_unique(fields: list[dict]) -> None:
    """Add a counter to repeated names (date, date_2). A counted name says less about its field."""
    seen: dict[str, int] = {}
    for f in fields:
        base = f["name"]
        if base in seen:
            seen[base] += 1
            f["name"] = f"{base}_{seen[base]}"
            f["counted"] = True
        else:
            seen[base] = 1
