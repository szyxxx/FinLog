"""Benchmark ekstraksi struk: tiga mode (ocr_only, ocr_llm, vision) pada struk sintetis + kunci jawaban.

    uv run python scripts/bench_receipt.py [--modes ocr_only,ocr_llm,vision] [--regen]

- Struk uji dibuat dengan PIL ke samples/bench/ (8 struk, nama toko FIKTIF) + ground_truth.json.
- Mengukur hanya tahap EKSTRAKSI struk (OCR/LLM/vision + validasi + fallback), bukan loop agent pencatat.
- Butuh Ollama aktif (mode ocr_llm & vision). Server FastAPI TIDAK perlu jalan. Trace ditulis ke data/bench_receipt.db.
- Hasil: data/bench_receipt.md (tabel) dan data/bench_receipt.json.
Catatan: struk sintetis (teks digital, huruf rapi) jauh lebih mudah daripada foto struk termal asli.
"""
import argparse
import asyncio
import difflib
import json
import os
import random
import re
import statistics
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = str(ROOT / "data" / "bench_receipt.db")  # jangan campur dengan database utama

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

BENCH = ROOT / "samples" / "bench"
FONTS = {
    "dejavu_mono": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "dejavu_mono_b": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "dejavu_sans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "dejavu_sans_b": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "liberation_mono": "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "liberation_mono_b": "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "noto_mono": "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
}


def rp(v: int) -> str:
    return f"{v:,}".replace(",", ".")


# Setiap struk: baris = str (kiri) | (kiri, kanan) | ("center", teks) | "---". gt = kunci jawaban.
RECEIPTS = [
    {
        "id": "r1_minimarket", "desc": "minimarket sederhana, font mono, bersih",
        "font": "dejavu_mono", "size": 20, "width": 460,
        "lines": [("center", "TOKO SUMBER REJEKI"), ("center", "Jl. Kenanga No. 5, Depok"), "07-11-2026 10:15  Ksr: ADI", "---",
                  "SUSU UHT COKLAT 250ML", ("  3 x 6.500", "19.500"), ("GULA PASIR 1KG", "17.900"), ("TELUR AYAM 1/2 KG", "15.000"),
                  "---", ("TOTAL", "52.400"), ("TUNAI", "60.000"), ("KEMBALI", "7.600")],
        "gt": {"toko": "TOKO SUMBER REJEKI", "tanggal": "2026-11-07", "total": 52400, "n_items": 3},
    },
    {
        "id": "r2_resto_pajak", "desc": "restoran, PPN 10% + service 5%, GRAND TOTAL, font proporsional",
        "font": "dejavu_sans", "size": 19, "width": 480,
        "lines": [("center", "RM PADANG SALERO BUNDO"), ("center", "Jl. Margonda Raya 101"), "Tgl: 06/11/2026  Meja: 07", "---",
                  ("Nasi Rendang", "28.000"), ("Ayam Pop", "25.000"), ("Gulai Tunjang", "30.000"), ("Es Teh Manis x2", "12.000"),
                  "---", ("SUBTOTAL", "95.000"), ("PPN 10%", "9.500"), ("SERVICE 5%", "4.750"), ("GRAND TOTAL", "109.250"),
                  ("DEBIT", "109.250")],
        "gt": {"toko": "RM PADANG SALERO BUNDO", "tanggal": "2026-11-06", "total": 109250, "n_items": 4},
    },
    {
        "id": "r3_swalayan_diskon", "desc": "swalayan, banyak item, diskon member, TOTAL BELANJA",
        "font": "liberation_mono", "size": 19, "width": 480,
        "lines": [("center", "PASAR SWALAYAN MAKMUR"), ("center", "Ruko Bintaro Blok C-3"), "2026-11-05 18:02  POS-02", "---",
                  ("BERAS PANDAN 5KG", "68.500"), "MINYAK GORENG 2L", ("  2 x 18.900", "37.800"), ("KECAP MANIS 520ML", "21.300"),
                  ("SABUN CUCI PIRING", "12.900"), ("TISU WAJAH 250S", "15.400"), "---", ("SUBTOTAL", "155.900"),
                  ("DISKON MEMBER", "-7.500"), ("TOTAL BELANJA", "148.400"), ("TUNAI", "150.000"), ("KEMBALI", "1.600")],
        "gt": {"toko": "PASAR SWALAYAN MAKMUR", "tanggal": "2026-11-05", "total": 148400, "n_items": 5},
    },
    {
        "id": "r4_kopi_qris", "desc": "kedai kopi, tanggal teks (09 Nov 2026), TOTAL BAYAR, QRIS",
        "font": "noto_mono", "size": 20, "width": 440,
        "lines": [("center", "KEDAI KOPI SENJA"), ("center", "Ciputat - Tangsel"), "09 Nov 2026 14:20", "Order #A-118", "---",
                  ("Kopi Susu Aren L", "28.000"), ("Croissant Butter", "26.000"), "---", ("TOTAL BAYAR", "54.000"),
                  ("QRIS", "54.000")],
        "gt": {"toko": "KEDAI KOPI SENJA", "tanggal": "2026-11-09", "total": 54000, "n_items": 2},
    },
    {
        "id": "r5_apotek_miring_buram", "desc": "apotek, miring 4 derajat + buram",
        "font": "dejavu_mono", "size": 20, "width": 460, "rotate": 4, "blur": 1.3,
        "lines": [("center", "APOTEK SEHAT SENTOSA"), ("center", "Jl. Cempaka 21, Bogor"), "03-11-2026 20:41", "---",
                  ("PARACETAMOL 500MG", "8.500"), ("VITAMIN C 1000 10S", "32.000"), ("MASKER MEDIS 5S", "10.000"),
                  "---", ("TOTAL", "50.500"), ("TUNAI", "100.000"), ("KEMBALI", "49.500")],
        "gt": {"toko": "APOTEK SEHAT SENTOSA", "tanggal": "2026-11-03", "total": 50500, "n_items": 3},
    },
    {
        "id": "r6_font_kecil", "desc": "toko bangunan, font kecil (11 px), 8 item, tahun 2 digit",
        "font": "dejavu_mono", "size": 11, "width": 280,
        "lines": [("center", "TOKO BANGUNAN MAJU"), ("center", "Jl. Raya Serpong KM 7"), "02/11/26 09:12", "---",
                  ("SEMEN 40KG", "62.000"), ("PASIR 1 KARUNG", "25.000"), ("PAKU 5CM 1KG", "18.000"), ("CAT TEMBOK 5KG", "95.000"),
                  ("KUAS 3 INCI", "12.000"), ("AMPLAS", "4.500"), ("LEM PIPA", "9.000"), ("SEKRUP 100PCS", "15.500"),
                  "---", ("TOTAL", "241.000"), ("TUNAI", "250.000"), ("KEMBALI", "9.000")],
        "gt": {"toko": "TOKO BANGUNAN MAJU", "tanggal": "2026-11-02", "total": 241000, "n_items": 8},
    },
    {
        "id": "r7_spbu", "desc": "SPBU, liter desimal, label berbeda (JUMLAH)",
        "font": "liberation_mono", "size": 19, "width": 440,
        "lines": [("center", "SPBU 31.154.02 CIPUTAT"), ("center", "Jl. Ir. H. Juanda"), "Waktu: 08/11/2026 07:30", "No. Trans: 558812", "---",
                  "Produk : PERTALITE", "Volume : 4,00 L", "Harga/L: Rp 10.000", "---", ("JUMLAH", "Rp 40.000"), ("CASH", "Rp 50.000"),
                  ("KEMBALI", "Rp 10.000")],
        "gt": {"toko": "SPBU 31.154.02 CIPUTAT", "tanggal": "2026-11-08", "total": 40000, "n_items": 1},
    },
    {
        "id": "r8_warung_noise_jpeg", "desc": "warung makan, noise berat + JPEG q25 + miring -3 derajat",
        "font": "dejavu_mono_b", "size": 20, "width": 460, "rotate": -3, "noise": 9000, "jpeg": 25,
        "lines": [("center", "WARUNG MAKAN BU SRI"), ("center", "Gg. Melati 3, Pamulang"), "01-11-2026 12:35", "---",
                  ("NASI PUTIH", "5.000"), ("AYAM GORENG", "15.000"), ("SAYUR ASEM", "6.000"), ("ES JERUK", "5.000"),
                  "---", ("TOTAL", "31.000"), ("TUNAI", "50.000"), ("KEMBALI", "19.000")],
        "gt": {"toko": "WARUNG MAKAN BU SRI", "tanggal": "2026-11-01", "total": 31000, "n_items": 4},
    },
]


def render(spec: dict) -> Path:
    f = ImageFont.truetype(FONTS[spec["font"]], spec["size"])
    lh = int(spec["size"] * 1.6)
    w = spec["width"]
    h = 30 + lh * len(spec["lines"]) + 20
    img = Image.new("RGB", (w, h), (252, 250, 244))
    d = ImageDraw.Draw(img)
    rnd = random.Random(zlib.crc32(spec["id"].encode()))
    for _ in range(spec.get("noise", 800)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        c = rnd.randrange(150 if spec.get("noise") else 215, 245)
        d.point((x, y), fill=(c, c, c))
    pad = 14
    for i, ln in enumerate(spec["lines"]):
        y = 15 + i * lh
        if ln == "---":
            d.text((pad, y), "-" * 60, fill=(40, 40, 40), font=f)
        elif isinstance(ln, tuple) and ln[0] == "center":
            tw = d.textlength(ln[1], font=f)
            d.text(((w - tw) / 2, y), ln[1], fill=(25, 25, 25), font=f)
        elif isinstance(ln, tuple):
            d.text((pad, y), ln[0], fill=(25, 25, 25), font=f)
            tw = d.textlength(ln[1], font=f)
            d.text((w - pad - tw, y), ln[1], fill=(25, 25, 25), font=f)
        else:
            d.text((pad, y), ln, fill=(25, 25, 25), font=f)
    if spec.get("rotate"):
        img = img.rotate(spec["rotate"], expand=True, fillcolor=(190, 190, 190), resample=Image.BICUBIC)
    if spec.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(spec["blur"]))
    ext = "jpg" if spec.get("jpeg") else "png"
    p = BENCH / f"{spec['id']}.{ext}"
    if ext == "jpg":
        img.save(p, "JPEG", quality=spec["jpeg"])
    else:
        img.save(p)
    return p


def make_set(regen: bool) -> dict:
    BENCH.mkdir(parents=True, exist_ok=True)
    gt_path = BENCH / "ground_truth.json"
    if gt_path.exists() and not regen:
        return json.loads(gt_path.read_text())
    gt = {}
    for spec in RECEIPTS:
        p = render(spec)
        gt[spec["id"]] = {"file": p.name, "desc": spec["desc"], **spec["gt"]}
    gt_path.write_text(json.dumps(gt, indent=1, ensure_ascii=False))
    return gt


def norm_name(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper().replace("O", "0"))


def name_ok(pred: str, gt: str) -> bool:
    a, b = norm_name(pred), norm_name(gt)
    return bool(a) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.85)


async def run_mode(mode: str, gt: dict) -> list[dict]:
    from app import db
    from app.agent import llm_step
    from app.inputs import receipt
    from app.trace import Tracer

    db.init_db()
    rows = []
    for rid, g in gt.items():
        data = (BENCH / g["file"]).read_bytes()
        tr = Tracer(f"bench_{mode}_{rid}_{int(time.time())}")

        def factory(name, think, tr=tr):
            return lambda **kw: llm_step(tr, name, **{"think": think, **kw})

        t0 = time.time()
        err = None
        try:
            if mode == "ocr_llm_rules_first":
                res = await receipt.extract_receipt(data, tr, factory, mode="ocr_llm", rules_first=True)
            else:
                res = await receipt.extract_receipt(data, tr, factory, mode=mode, rules_first=False)
            d = res["data"]
        except Exception as e:  # noqa: BLE001
            res, d, err = {}, {}, repr(e)
        ms = int((time.time() - t0) * 1000)
        row = {
            "id": rid, "mode": mode, "ms": ms, "error": err,
            "pred": {k: d.get(k) for k in ("toko", "tanggal", "total")} | {"n_items": len(d.get("items") or [])},
            "sumber": d.get("sumber_ekstraksi"), "fallback": bool(res.get("fallback")),
            "fallback_alasan": (res.get("fallback") or {}).get("alasan"),
            "validasi_lolos": (res.get("validasi") or {}).get("lolos"),
            "ocr_ms": (res.get("ocr") or {}).get("ms"),
        }
        row["ok"] = {
            "total": d.get("total") == g["total"],
            "tanggal": d.get("tanggal") == g["tanggal"],
            "toko": name_ok(d.get("toko"), g["toko"]),
            "n_items": len(d.get("items") or []) == g["n_items"],
        }
        rows.append(row)
        flag = " ".join(f"{k}={'Y' if v else 'n'}" for k, v in row["ok"].items())
        print(f"  [{mode}] {rid:24s} {ms:6d} ms  {flag}  sumber={row['sumber']} fb={row['fallback']}"
              + (f"  ERR {err}" if err else "") + f"  pred={row['pred']}", flush=True)
    return rows


async def warmup() -> None:
    from app import llm
    from app.inputs import ocr

    ocr.warmup()
    await llm.chat([{"role": "user", "content": "ok"}], think=False)  # muat model ke GPU sebelum diukur


def summarize(rows: list[dict], gt: dict) -> str:
    modes = sorted({r["mode"] for r in rows}, key=["ocr_only", "ocr_llm", "ocr_llm_rules_first", "vision"].index)
    n = len(gt)
    out = ["# Benchmark ekstraksi struk (RECEIPT_MODE)", "",
           f"Tanggal: {time.strftime('%Y-%m-%d %H:%M')} · {n} struk sintetis (`samples/bench/`, kunci jawaban di "
           "`ground_truth.json`) · hanya tahap ekstraksi (tanpa loop agent) · OCR di CPU, LLM/vision `qwen3.5:9b` di GPU "
           "tanpa thinking. `ocr_llm_rules_first` = mode `ocr_llm` dengan `RECEIPT_RULES_FIRST=true`.", "",
           "> **Catatan:** struk sintetis (teks digital yang dirender rapi, termasuk yang dibuat miring/buram/ber-noise) "
           "jauh lebih mudah daripada foto struk termal asli (kertas lecek, tinta pudar, cahaya, perspektif). "
           "Angka di bawah adalah batas atas, bukan perkiraan akurasi di dunia nyata.", "",
           "## Ringkasan", "",
           "| Mode | Total benar | Tanggal benar | Nama toko benar | Jumlah item benar | Latensi rata-rata | Latensi maks | Fallback ke vision |",
           "|---|---|---|---|---|---|---|---|"]
    for m in modes:
        rs = [r for r in rows if r["mode"] == m]
        c = {k: sum(r["ok"][k] for r in rs) for k in ("total", "tanggal", "toko", "n_items")}
        lat = [r["ms"] for r in rs]
        fb = sum(r["fallback"] for r in rs)
        out.append(f"| `{m}` | {c['total']}/{n} | {c['tanggal']}/{n} | {c['toko']}/{n} | {c['n_items']}/{n} | "
                   f"{statistics.mean(lat) / 1000:.1f} dtk | {max(lat) / 1000:.1f} dtk | "
                   f"{f'{fb}/{n}' if m.startswith('ocr_llm') else '-'} |")
    out += ["", "## Per struk", "", "| Struk | Kondisi | Mode | Total | Tanggal | Toko | Item | Latensi | Keterangan |",
            "|---|---|---|---|---|---|---|---|---|"]
    yn = lambda b: "✓" if b else "✗"  # noqa: E731
    for rid, g in gt.items():
        for m in modes:
            r = next(x for x in rows if x["id"] == rid and x["mode"] == m)
            p = r["pred"]
            note = []
            if r["fallback"]:
                note.append("fallback vision: " + ", ".join(r["fallback_alasan"] or []))
            if r["error"]:
                note.append("error " + r["error"][:60])
            if not r["ok"]["total"]:
                note.append(f"total {p['total']} (kunci {g['total']})")
            if not r["ok"]["tanggal"]:
                note.append(f"tanggal {p['tanggal']!r} (kunci {g['tanggal']})")
            if not r["ok"]["toko"]:
                note.append(f"toko {p['toko']!r}")
            if not r["ok"]["n_items"]:
                note.append(f"{p['n_items']} item (kunci {g['n_items']})")
            out.append(f"| {rid} | {g['desc']} | `{m}` | {yn(r['ok']['total'])} | {yn(r['ok']['tanggal'])} | "
                       f"{yn(r['ok']['toko'])} | {yn(r['ok']['n_items'])} | {r['ms'] / 1000:.1f} dtk | {'; '.join(note)} |")
    out += ["", "Nama toko dianggap benar bila sama setelah normalisasi (huruf besar, tanpa spasi/tanda baca) atau "
            "kemiripan ≥ 0,85. Latensi = OCR + LLM/vision + validasi (+ fallback) pada laptop server, model sudah dimuat."]
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="ocr_only,ocr_llm,ocr_llm_rules_first,vision")
    ap.add_argument("--regen", action="store_true")
    a = ap.parse_args()
    gt = make_set(a.regen)
    print(f"{len(gt)} struk uji di {BENCH}")
    rows = []

    async def go():
        await warmup()
        for m in a.modes.split(","):
            rows.extend(await run_mode(m, gt))

    asyncio.run(go())
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "bench_receipt.json").write_text(json.dumps({"ground_truth": gt, "rows": rows}, indent=1, ensure_ascii=False))
    md = summarize(rows, gt)
    (ROOT / "data" / "bench_receipt.md").write_text(md)
    print("\n" + md)


if __name__ == "__main__":
    main()
