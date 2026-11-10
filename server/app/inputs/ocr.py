"""OCR struk di CPU: model PaddleOCR (PP-OCRv4 det/rec + cls) lewat RapidOCR + onnxruntime.

Sengaja di CPU: VRAM sudah dipakai LLM (+Whisper), dan paddlepaddle-gpu belum jelas mendukung RTX 50 (Blackwell).
Koordinat bbox dikembalikan dalam piksel GAMBAR ASLI (setelah koreksi EXIF) agar sandbox bisa menggambar kotak.
"""
import io
import math
import statistics
import threading
import time

import numpy as np
from PIL import Image, ImageOps

from ..config import get_settings

_engine = None
_lock = threading.Lock()
ENGINE_NAME = "rapidocr-onnxruntime (PaddleOCR PP-OCRv4 det+rec, mobile cls), CPU"


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def skew_angle(boxes: list[dict]) -> float:
    """Perkiraan kemiringan struk (radian) dari median sudut tepi atas kotak teks yang lebar."""
    angs = []
    for b in boxes:
        (x0, y0), (x1, y1) = b["quad"][0], b["quad"][1]
        w, h = x1 - x0, b["bbox"][3] - b["bbox"][1]
        if w > 2 * h and w > 0:
            angs.append(math.atan2(y1 - y0, x1 - x0))
    return statistics.median(angs) if angs else 0.0


def _rows(boxes: list[dict]) -> list[dict]:
    """Gabungkan kotak teks menjadi baris (struk: nama item di kiri, harga di kanan pada baris yang sama).
    Pengelompokan dilakukan pada sumbu y yang sudah dikoreksi kemiringannya, supaya struk miring tetap benar."""
    if not boxes:
        return []
    a = skew_angle(boxes)
    sin_a, cos_a = math.sin(a), math.cos(a)

    def cy(b: dict) -> float:
        x = sum(p[0] for p in b["quad"]) / 4
        y = sum(p[1] for p in b["quad"]) / 4
        return -x * sin_a + y * cos_a

    heights = [b["bbox"][3] - b["bbox"][1] for b in boxes]
    tol = max(4.0, statistics.median(heights) * 0.5 * cos_a)
    items = sorted(boxes, key=cy)
    rows: list[list[dict]] = []
    for b in items:
        if rows and abs(cy(b) - statistics.mean(cy(x) for x in rows[-1])) <= tol:
            rows[-1].append(b)
            continue
        rows.append([b])
    out = []
    for r in rows:
        r.sort(key=lambda b: b["bbox"][0])
        out.append({
            "text": " ".join(b["text"] for b in r),
            "conf": round(min(b["conf"] for b in r), 3),
            "bbox": [min(b["bbox"][0] for b in r), min(b["bbox"][1] for b in r),
                     max(b["bbox"][2] for b in r), max(b["bbox"][3] for b in r)],
            "box_index": [b["index"] for b in r],
        })
    return out


def run_ocr(image_bytes: bytes) -> dict:
    """Blocking; panggil via asyncio.to_thread."""
    s = get_settings()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    orig_w, orig_h = img.size
    scale = 1.0
    if max(img.size) > s.ocr_max_side:
        scale = s.ocr_max_side / max(img.size)
        img = img.resize((round(orig_w * scale), round(orig_h * scale)), Image.LANCZOS)
    arr = np.asarray(img)[:, :, ::-1]  # RGB -> BGR (konvensi OpenCV)
    t0 = time.time()
    with _lock:
        result, elapse = _get_engine()(arr)
    dur = int((time.time() - t0) * 1000)
    boxes = []
    for i, (quad, text, conf) in enumerate(result or []):
        q = [[round(x / scale, 1), round(y / scale, 1)] for x, y in quad]
        xs, ys = [p[0] for p in q], [p[1] for p in q]
        boxes.append({"index": i, "text": text, "conf": round(float(conf), 3), "quad": q,
                      "bbox": [min(xs), min(ys), max(xs), max(ys)]})
    lines = _rows(boxes)
    return {
        "engine": ENGINE_NAME,
        "image_size": [orig_w, orig_h],
        "ocr_input_size": list(img.size),
        "coord_space": "piksel gambar asli (x0,y0,x1,y1); quad = 4 titik searah jarum jam dari kiri atas",
        "boxes": boxes,
        "lines": lines,
        "n_boxes": len(boxes),
        "skew_deg": round(math.degrees(skew_angle(boxes)), 2) if boxes else 0.0,
        "mean_conf": round(statistics.mean(b["conf"] for b in boxes), 3) if boxes else 0.0,
        "min_conf": round(min(b["conf"] for b in boxes), 3) if boxes else 0.0,
        "timings_ms": {"total": dur, **({k: int(v * 1000) for k, v in zip(("det", "cls", "rec"), elapse)} if elapse else {})},
    }


def warmup() -> None:
    _get_engine()
