"""Review page: the proposed fields open in the Nutrient Web SDK Form Creator, ready to adjust and export.

Run detect.py first for the samples. Then: python server.py and open http://localhost:4710
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader

from formdetect.naming import llm_model
from formdetect.sdk import env

ROOT = Path(__file__).resolve().parent
FORMS = (ROOT / env("FORMS_FOLDER", "samples/forms")).resolve()
OUTPUT = (ROOT / env("OUTPUT_FOLDER", "output")).resolve()
UPLOADS, PROPOSALS, FILLABLE = OUTPUT / "uploads", OUTPUT / "proposals", OUTPUT / "fillable"
LABELS = env("LABELS", "llm")
for folder in (UPLOADS, PROPOSALS, FILLABLE):
    folder.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Form field detection review")


def forms() -> dict[str, Path]:
    found = {p.stem: p for p in sorted(FORMS.glob("*.pdf"))}
    found.update({p.stem: p for p in sorted(UPLOADS.glob("*.pdf"))})
    return found


def form_path(form_id: str) -> Path:
    path = forms().get(form_id)
    if not path:
        raise HTTPException(404, "Unknown form")
    return path


def detect(form_id: str) -> dict:
    """Run detect.py in its own process: each run is a fresh SDK session (evaluation sessions last one hour)."""
    path = form_path(form_id)
    cmd = [sys.executable, str(ROOT / "detect.py"), "--file", str(path), "--id", form_id,
           "--output", str(OUTPUT), "--labels", LABELS]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
    out = PROPOSALS / f"{form_id}.json"
    if done.returncode != 0 or not out.exists():
        raise HTTPException(500, f"Detection failed: {done.stderr[-800:]}")
    return json.loads(out.read_text(encoding="utf-8"))


@app.get("/api/config")
def config() -> dict:
    # The Web SDK license key is public by design (it is bound to a domain). Empty = evaluation mode.
    return {"web_sdk_license_key": env("WEB_SDK_LICENSE_KEY"), "labels": LABELS,
            "llm": llm_model() if LABELS.startswith("llm") else None}


@app.get("/api/forms")
def list_forms() -> list[dict]:
    items = []
    for form_id, path in forms().items():
        proposals = PROPOSALS / f"{form_id}.json"
        fields = len(json.loads(proposals.read_text(encoding="utf-8"))["fields"]) if proposals.exists() else None
        items.append({"id": form_id, "file": path.name, "uploaded": path.parent == UPLOADS, "fields": fields,
                      "exported": (FILLABLE / f"{form_id}-fillable.pdf").exists()})
    return items


@app.get("/api/forms/{form_id}/pdf")
def form_pdf(form_id: str) -> FileResponse:
    return FileResponse(form_path(form_id), media_type="application/pdf")


@app.get("/api/forms/{form_id}/proposals")
async def proposals(form_id: str) -> dict:
    form_path(form_id)
    cached = PROPOSALS / f"{form_id}.json"
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    return await run_in_threadpool(detect, form_id)


@app.post("/api/forms/{form_id}/detect")
async def redetect(form_id: str) -> dict:
    return await run_in_threadpool(detect, form_id)


@app.post("/api/upload")
async def upload(request: Request, name: str = "form.pdf") -> dict:
    data = await request.body()
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "Upload a PDF file")
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "PDF too large")
    form_id = "upload-" + (re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-") or "form")
    (UPLOADS / f"{form_id}.pdf").write_bytes(data)
    (PROPOSALS / f"{form_id}.json").unlink(missing_ok=True)
    return {"id": form_id}


@app.post("/api/forms/{form_id}/export")
async def export(form_id: str, request: Request) -> dict:
    """Store the PDF exported by the Web SDK and read its AcroForm back with pypdf, as a check."""
    form_path(form_id)
    data = await request.body()
    target = FILLABLE / f"{form_id}-fillable.pdf"
    target.write_bytes(data)
    reader = PdfReader(io.BytesIO(data))
    acroform = "/AcroForm" in reader.trailer["/Root"]
    fields = reader.get_fields() or {}
    kinds = {"/Tx": "text", "/Btn": "button", "/Sig": "signature", "/Ch": "choice"}
    return {"file": target.name, "acroform": acroform, "count": len(fields),
            "fields": [{"name": name, "kind": kinds.get(str(f.get("/FT")), str(f.get("/FT")))} for name, f in fields.items()]}


@app.get("/api/forms/{form_id}/fillable.pdf")
def fillable(form_id: str) -> FileResponse:
    path = FILLABLE / f"{form_id}-fillable.pdf"
    if not path.exists():
        raise HTTPException(404, "Not exported yet")
    return FileResponse(path, media_type="application/pdf", filename=f"{form_id}-fillable.pdf")


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host=env("HOST", "127.0.0.1"), port=int(env("PORT", "4710")))
