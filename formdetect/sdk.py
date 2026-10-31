"""Nutrient Python SDK: form field detection and page images.

- Vision.detect_forms() runs the SDK's form field detection model on every page, offline. It returns
  each field's kind (Text, Checkbox or Signature), its box in pixels of the page image the model saw,
  and a confidence. With AI labeling turned on, a vision model also gives each field a label.
- Document.export_as_image() renders the pages that the geometry pass reads with OpenCV.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import cv2
import nutrient_sdk as ns
import numpy as np

_registered = False


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def register_license() -> None:
    """Register NUTRIENT_LICENSE_KEY once. An empty key runs the SDK in evaluation mode."""
    global _registered
    if not _registered:
        key = env("NUTRIENT_LICENSE_KEY")
        if key:
            ns.License.register_key(key)
        _registered = True


def detect_fields(pdf: Path, vlm_labels: bool = False) -> list[dict]:
    """Fields found by the SDK model, in PDF points with the origin at the top left of the page."""
    register_license()
    with ns.Document.open(str(pdf)) as doc:
        if vlm_labels:
            # The SDK's own labeling: numbered marks over the page image, sent to a vision model.
            doc.settings.vision_settings.provider = ns.VlmProvider.CUSTOM
            vlm = doc.settings.custom_vlm_api_settings
            vlm.api_endpoint = env("VLM_BASE_URL", env("LLM_BASE_URL", "http://localhost:11434/v1"))
            vlm.model = env("VLM_MODEL", "qwen2.5vl:7b")
            key = env("VLM_API_KEY", env("LLM_API_KEY"))
            if key:
                vlm.api_key = key
            doc.settings.form_labeling_settings.enable_ai_labeling = True
        result = json.loads(ns.Vision.set(doc).detect_forms())

    scale = {m["pageNumber"]: (72 / m["dpiX"], 72 / m["dpiY"]) for m in result.get("metadata", [])}
    fields = []
    for element in result.get("elements", []):
        page = element["pageNumber"]
        sx, sy = scale.get(page, (72 / 200, 72 / 200))
        for f in element.get("fields", []):
            b = f["bounds"]
            fields.append({
                "page": page,
                "rect": [b["x"] * sx, b["y"] * sy, (b["x"] + b["width"]) * sx, (b["y"] + b["height"]) * sy],
                "kind": f["fieldType"].lower(),  # text, checkbox or signature
                "confidence": round(float(f.get("confidence", 0)), 3),
                "source": "model",
                "sdk_label": f.get("label"),
            })
    return fields


def page_images(pdf: Path, dpi: int = 200) -> list[np.ndarray]:
    """Grayscale image of every page. export_as_image writes one file per page: name-1.png, name-2.png..."""
    register_license()
    with tempfile.TemporaryDirectory() as tmp:
        with ns.Document.open(str(pdf)) as doc:
            settings = doc.settings.image_settings
            settings.export_format = ns.ImageExportFormat.PNG
            settings.rasterization_dpi = dpi
            doc.export_as_image(str(Path(tmp) / "page.png"))
        files = sorted(Path(tmp).glob("page*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]) if "-" in p.stem else 1)
        return [cv2.imread(str(f), cv2.IMREAD_GRAYSCALE) for f in files]
