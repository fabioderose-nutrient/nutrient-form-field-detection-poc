"""End-to-end check of the export: for every sample form, open the review page in a headless browser,
click "Export fillable PDF", then fill the exported PDF with pypdf and read the values back.

Checks that the PDF exported by the Web SDK is a real AcroForm: every proposed field is there with
its name and kind (text, check box, signature; multi-line flag), text fields and check boxes can be
filled by another tool, and the values are read back from the saved file.

Start server.py first, then:
  pip install -r requirements-dev.txt && playwright install chromium
  python tests/check_export.py [--url http://localhost:4710]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
MULTILINE_FLAG = 1 << 12


def export_in_browser(url: str, form_id: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{url}?form={form_id}")
        page.wait_for_function("document.getElementById('viewer-status').textContent.includes('loaded')", timeout=600_000)
        page.click("#export")
        page.wait_for_selector("#export-result:not([hidden])", timeout=120_000)
        text = page.locator("#export-result").inner_text()
        browser.close()
    return text


def on_states(reader: PdfReader) -> dict[str, str]:
    """The "on" appearance name of every check box widget (for example /Yes)."""
    states = {}
    for page in reader.pages:
        for ref in page.get("/Annots", []) or []:
            a = ref.get_object()
            name = a.get("/T") or (a.get("/Parent").get_object().get("/T") if a.get("/Parent") else None)
            normal = (a.get("/AP") or {}).get("/N")
            if name and normal is not None and hasattr(normal, "keys"):
                on = [k for k in normal.keys() if k != "/Off"]
                if on:
                    states[str(name)] = on[0]
    return states


def check(form_id: str, proposals: dict) -> tuple[bool, str]:
    exported = OUTPUT / "fillable" / f"{form_id}-fillable.pdf"
    reader = PdfReader(exported)
    if "/AcroForm" not in reader.trailer["/Root"]:
        return False, "no AcroForm"
    fields = reader.get_fields() or {}
    expected = {f["name"]: f for f in proposals["fields"]}
    problems = []
    if set(fields) != set(expected):
        problems.append(f"names differ: missing {sorted(set(expected) - set(fields))}, extra {sorted(set(fields) - set(expected))}")
    kinds = {"checkbox": "/Btn", "signature": "/Sig"}
    for name, f in expected.items():
        got = fields.get(name)
        if got is None:
            continue
        if str(got.get("/FT")) != kinds.get(f["type"], "/Tx"):
            problems.append(f"{name}: {got.get('/FT')} instead of {kinds.get(f['type'], '/Tx')}")
        if f["type"] == "multiline" and not int(got.get("/Ff", 0)) & MULTILINE_FLAG:
            problems.append(f"{name}: not multi-line")

    # Fill every text field and tick every check box with pypdf, save, and read the values back.
    states = on_states(reader)
    values = {}
    for name, f in expected.items():
        if name not in fields or f["type"] == "signature":
            continue
        if f["type"] == "checkbox":
            values[name] = states.get(name, "/Yes")
        elif f["type"] == "date":
            values[name] = "31/12/2026"
        else:
            values[name] = f"Test {name}"[:40]
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    filled = OUTPUT / "fillable" / f"{form_id}-filled-by-pypdf.pdf"
    with open(filled, "wb") as fh:
        writer.write(fh)
    read_back = PdfReader(filled).get_fields() or {}
    wrong = [n for n, v in values.items() if str(read_back.get(n, {}).get("/V")) != str(NameObject(v) if v.startswith("/") else v)]
    if wrong:
        problems.append(f"values not read back: {wrong[:5]}")
    text = sum(1 for f in expected.values() if f["type"] not in ("checkbox", "signature"))
    boxes = sum(1 for f in expected.values() if f["type"] == "checkbox")
    signatures = sum(1 for f in expected.values() if f["type"] == "signature")
    summary = (f"{len(fields)} AcroForm fields ({text} text, {boxes} check boxes, {signatures} signature); "
               f"{len(values) - len(wrong)}/{len(values)} values filled and read back")
    return not problems, summary + ("" if not problems else " | " + "; ".join(problems))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:4710/")
    args = parser.parse_args()
    all_ok = True
    for path in sorted((OUTPUT / "proposals").glob("*.json")):
        proposals = json.loads(path.read_text(encoding="utf-8"))
        form_id = path.stem
        message = export_in_browser(args.url, form_id)
        ok, summary = check(form_id, proposals)
        all_ok &= ok
        print(f"{'ok ' if ok else 'BAD'} {form_id:26s} {summary}")
        if not ok:
            print(f"    page said: {message}")
    print("\nAll exports are fillable AcroForms." if all_ok else "\nSome exports failed.")


if __name__ == "__main__":
    main()
