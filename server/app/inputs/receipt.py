"""Ekstraksi struk belanja -> JSON terstruktur. Tiga mode (RECEIPT_MODE):

- ocr_llm  : OCR CPU (RapidOCR/PaddleOCR) -> LLM teks saja -> validasi terhadap teks OCR -> fallback vision bila gagal.
- ocr_only : OCR + aturan/regex, tanpa LLM.
- vision   : model vision Ollama membaca gambar langsung (jalur awal).
"""
import asyncio
import base64
import io
import json
import time
from collections.abc import Callable

from PIL import Image, ImageOps

from .. import llm
from ..config import get_settings
from ..db import CATEGORY_NAMES
from . import ocr as ocr_mod
from . import receipt_rules
from ..util import err

RECEIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "toko": {"type": "string"},
        "tanggal": {"type": "string", "description": "YYYY-MM-DD atau kosong"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"nama": {"type": "string"}, "qty": {"type": "number"},
                               "harga_satuan": {"type": "integer"}, "subtotal": {"type": "integer"}},
                "required": ["nama", "subtotal"],
            },
        },
        "total": {"type": "integer"},
        "diskon": {"type": "integer", "description": "total potongan/diskon (positif), 0 bila tidak ada"},
        "pajak_layanan": {"type": "integer", "description": "PPN/pajak/service yang ditambahkan ke total, 0 bila tidak ada"},
        "metode_bayar": {"type": "string"},
        "kategori": {"type": "string", "enum": CATEGORY_NAMES},
        "keyakinan": {"type": "number", "description": "0..1"},
        "catatan": {"type": "string"},
    },
    "required": ["toko", "tanggal", "items", "total", "diskon", "pajak_layanan", "kategori", "keyakinan"],
}

PROMPT = (
    "Kamu mengekstrak data dari foto struk belanja Indonesia. Kembalikan JSON sesuai skema.\n"
    "Aturan angka: format rupiah memakai titik sebagai pemisah ribuan, jadi '28.800' = 28800 dan '3.500' = 3500. "
    "Semua nominal adalah bilangan bulat rupiah. 'total' adalah TOTAL yang dibayar (bukan TUNAI/KEMBALI). "
    "Tanggal struk dalam format YYYY-MM-DD (struk Indonesia biasanya DD-MM-YYYY, ubah urutannya), string kosong bila tidak terbaca. "
    f"Pilih kategori dari: {', '.join(CATEGORY_NAMES)}. "
    "Isi 'diskon' (jumlah potongan, positif) dan 'pajak_layanan' (pajak/service yang ditambahkan), 0 bila tidak ada. "
    "Isi 'keyakinan' 0..1 sesuai seberapa jelas struk terbaca. Tulis JSON ringkas dalam SATU baris tanpa indentasi."
)

OCR_PROMPT = (
    "Berikut teks hasil OCR sebuah struk belanja Indonesia, satu baris per nomor (teks kiri-ke-kanan pada baris yang "
    "sama sudah digabung). OCR bisa salah baca (huruf O vs angka 0, spasi hilang). Susun JSON sesuai skema.\n"
    "Aturan: format rupiah memakai titik sebagai pemisah ribuan ('47.700' = 47700); semua nominal bilangan bulat. "
    "'total' = TOTAL / GRAND TOTAL / TOTAL BAYAR yang dibayar, BUKAN subtotal, TUNAI, atau KEMBALI. "
    "'items' = barang/jasa yang dibeli saja (bukan baris diskon, pajak, subtotal, pembayaran); baris 'N x harga' "
    "adalah qty dan harga satuan untuk nama barang di baris atasnya; tanpa pola itu qty = 1 (angka di dalam nama barang "
    "seperti 5S, 1KG, 250ML adalah bagian nama, bukan qty). 'diskon' = total potongan (angka positif), "
    "'pajak_layanan' = PPN/pajak/service yang DITAMBAHKAN ke total (0 bila tertulis termasuk/incl). "
    "Tanggal dalam format YYYY-MM-DD (DD-MM-YYYY di struk harus dibalik), string kosong bila tidak ada. "
    "Nama toko = nama usaha di kepala struk, perbaiki spasi yang hilang bila jelas. "
    f"Kategori dari: {', '.join(CATEGORY_NAMES)}. JANGAN mengarang angka yang tidak ada di teks. "
    "'keyakinan' 0..1. Tulis JSON ringkas dalam SATU baris tanpa indentasi."
)


def prepare_image(data: bytes, max_side: int = 1280) -> tuple[str, dict]:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")
    orig = img.size
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), {"ukuran_asli": orig, "ukuran_dikirim": img.size, "bytes": len(buf.getvalue())}


def _fix_rupiah(v):
    """Model kadang membaca '28.800' sebagai 28.8. Nominal rupiah < 1000 berpecahan -> kali 1000."""
    if isinstance(v, (int, float)) and 0 < v < 1000 and float(v) != int(v):
        return int(round(v * 1000))
    return int(round(v)) if isinstance(v, (int, float)) else v


def normalize(d: dict) -> dict:
    d["total"] = _fix_rupiah(d.get("total") or 0)
    for it in d.get("items") or []:
        for k in ("harga_satuan", "subtotal"):
            if k in it and it[k] is not None:
                it[k] = _fix_rupiah(it[k])
    for k in ("diskon", "pajak_layanan"):
        d[k] = abs(_fix_rupiah(d.get(k) or 0)) if isinstance(d.get(k) or 0, (int, float)) else 0
    s = sum((it.get("subtotal") or 0) for it in d.get("items") or [])
    expected = s - d["diskon"] + d["pajak_layanan"]
    d["cek_konsistensi"] = {"jumlah_subtotal": s, "diskon": d["diskon"], "pajak_layanan": d["pajak_layanan"],
                            "total": d["total"], "cocok": abs(expected - d["total"]) <= max(100, d["total"] * 0.01)}
    return d


async def extract(image_b64: str, llm_call=None) -> dict:
    """llm_call: fungsi pemanggil LLM ber-trace (agent.llm_step); default llm.chat langsung."""
    s = get_settings()
    t0 = time.time()
    call = llm_call or (lambda **kw: llm.chat(**kw))
    res = await call(
        messages=[{"role": "user", "content": PROMPT, "images": [image_b64]}],
        model=s.vision_model, format=RECEIPT_SCHEMA,
    )
    raw = res["message"].get("content") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"error": "output bukan JSON", "raw": raw}
    else:
        data = normalize(data)
    return {"data": data, "raw": raw, "llm": {k: res[k] for k in ("model", "prompt_tokens", "completion_tokens", "tokens_per_second", "timings_ms")},
            "prompt": PROMPT, "duration_ms": int((time.time() - t0) * 1000)}


# ---------------------------------------------------------------- pipeline tiga mode

def _usage(res: dict) -> dict:
    return {k: res[k] for k in ("model", "prompt_tokens", "completion_tokens", "tokens_per_second", "timings_ms")}


async def _ocr_llm_extract(ocr: dict, call: Callable) -> dict:
    s = get_settings()
    numbered = "\n".join(f"{i + 1:02d}| {ln['text']}" for i, ln in enumerate(ocr["lines"]))
    res = await call(messages=[{"role": "system", "content": OCR_PROMPT}, {"role": "user", "content": numbered}],
                     model=s.llm_model, format=RECEIPT_SCHEMA, think=False, temperature=0)
    raw = res["message"].get("content") or "{}"
    data = json.loads(raw)  # JSONDecodeError -> ditangani pemanggil sebagai validasi gagal
    return {"data": normalize(data), "raw": raw, "llm": _usage(res)}


async def extract_receipt(image_bytes: bytes, tr, llm_call_factory: Callable[[str, bool | None], Callable],
                          mode: str | None = None, rules_first: bool | None = None) -> dict:
    """Jalankan pipeline struk sesuai mode. `tr` = Tracer (step ocr/rules/validation/fallback ditulis di sini);
    `llm_call_factory(nama_step, think)` mengembalikan fungsi pemanggil LLM ber-trace.
    Mengembalikan {data, mode, jalur, fallback, validasi, llm_usage[], prompt, raw}."""
    s = get_settings()
    mode = mode or s.receipt_mode
    out: dict = {"mode": mode, "jalur": [], "fallback": None, "validasi": None, "llm_usage": [], "prompt": None, "raw": None}

    async def vision() -> dict:
        b64, meta = prepare_image(image_bytes)
        res = await extract(b64, llm_call=llm_call_factory("ekstrak_struk_vision", s.vision_think))
        out["jalur"].append("vision")
        out["llm_usage"].append(res["llm"])
        out.update(prompt=res["prompt"], raw=res["raw"], gambar=meta)
        return res["data"]

    if mode == "vision":
        out["data"] = await vision()
        out["data"]["sumber_ekstraksi"] = "vision"
        return out

    # --- OCR (CPU)
    t0 = time.time()
    tr.started("ocr", "ocr_struk", t0)
    try:
        ocr = await asyncio.to_thread(ocr_mod.run_ocr, image_bytes)
    except Exception as e:  # noqa: BLE001
        tr.add("ocr", "ocr_struk", {"engine": ocr_mod.ENGINE_NAME}, {"error": err(e)}, status="error", started_at=t0,
               duration_ms=int((time.time() - t0) * 1000))
        if mode == "ocr_llm" and s.receipt_fallback_vision:
            tr.add("fallback", "fallback_vision", {"alasan": [f"OCR gagal: {err(e)}"]}, {"mode_baru": "vision"}, status="warning")
            out["fallback"] = {"alasan": [f"OCR gagal: {err(e)}"]}
            out["data"] = await vision()
            out["data"]["sumber_ekstraksi"] = "vision_fallback"
            return out
        raise
    out["jalur"].append("ocr")
    tr.add("ocr", "ocr_struk", {"engine": ocr["engine"], "image_size": ocr["image_size"], "ocr_input_size": ocr["ocr_input_size"]},
           {k: ocr[k] for k in ("lines", "boxes", "n_boxes", "mean_conf", "min_conf", "skew_deg", "image_size", "coord_space",
                                 "timings_ms")},
           started_at=t0, duration_ms=ocr["timings_ms"]["total"])
    out["ocr"] = {"mean_conf": ocr["mean_conf"], "n_lines": len(ocr["lines"]), "ms": ocr["timings_ms"]["total"]}

    if mode == "ocr_only":
        t1 = time.time()
        data = receipt_rules.parse_lines(ocr["lines"])
        tr.add("rules", "parse_aturan", {"jumlah_baris": len(ocr["lines"])}, data, started_at=t1,
               duration_ms=int((time.time() - t1) * 1000))
        out["jalur"].append("rules")
        data = normalize(data)
        v = receipt_rules.validate(data, ocr, s.ocr_min_conf)
        tr.add("validation", "validasi_struk", {"sumber": "ocr_only"}, v, status="ok" if v["lolos"] else "warning")
        out["validasi"] = v
        # Tanpa LLM tidak ada "keyakinan" model: pakai confidence OCR, turunkan bila validasi gagal (agent lalu bertanya).
        data["keyakinan"] = round(ocr["mean_conf"] if v["lolos"] else min(0.5, ocr["mean_conf"]), 2)
        data["sumber_ekstraksi"] = "ocr_only"
        out["data"] = data
        return out

    # --- ocr_llm, opsi RECEIPT_RULES_FIRST: coba aturan dulu; bila lolos validasi, LLM tidak dipanggil (±1 dtk)
    if s.receipt_rules_first if rules_first is None else rules_first:
        t1 = time.time()
        rdata = normalize(receipt_rules.parse_lines(ocr["lines"]))
        tr.add("rules", "parse_aturan", {"jumlah_baris": len(ocr["lines"]), "rules_first": True}, rdata, started_at=t1,
               duration_ms=int((time.time() - t1) * 1000))
        rv = receipt_rules.validate(rdata, ocr, s.ocr_min_conf)
        tr.add("validation", "validasi_struk", {"sumber": "rules_first"}, rv, status="ok" if rv["lolos"] else "warning")
        out["jalur"].append("rules")
        if rv["lolos"]:
            out["validasi"] = rv
            rdata["keyakinan"] = round(ocr["mean_conf"], 2)
            rdata["sumber_ekstraksi"] = "ocr_rules"
            out["data"] = rdata
            return out

    # --- ocr_llm: LLM teks saja
    reasons: list[str] = []
    data = None
    try:
        r = await _ocr_llm_extract(ocr, llm_call_factory("ekstrak_struk_ocr_llm", False))
        out["llm_usage"].append(r["llm"])
        out.update(prompt=OCR_PROMPT, raw=r["raw"])
        data = r["data"]
        out["jalur"].append("llm_teks")
    except json.JSONDecodeError as e:
        reasons.append(f"output LLM bukan JSON: {e}")
    if data is not None:
        v = receipt_rules.validate(data, ocr, s.ocr_min_conf)
        out["validasi"] = v
        tr.add("validation", "validasi_struk", {"sumber": "ocr_llm"}, v, status="ok" if v["lolos"] else "warning")
        reasons += v["gagal"]
    if not reasons:
        data["sumber_ekstraksi"] = "ocr_llm"
        out["data"] = data
        return out
    if not s.receipt_fallback_vision:
        data = data or {"toko": "", "tanggal": "", "items": [], "total": 0, "kategori": "belanja"}
        data["keyakinan"] = min(0.5, float(data.get("keyakinan") or 0.5))
        data["sumber_ekstraksi"] = "ocr_llm"
        out["data"] = data
        return out
    tr.add("fallback", "fallback_vision", {"alasan": reasons, "hasil_ocr_llm": data}, {"mode_baru": "vision"}, status="warning")
    out["fallback"] = {"alasan": reasons, "hasil_ocr_llm": data}
    vdata = await vision()
    vdata["sumber_ekstraksi"] = "vision_fallback"
    out["data"] = vdata
    return out
