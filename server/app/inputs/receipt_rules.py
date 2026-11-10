"""Parser struk berbasis aturan/regex di atas baris OCR (mode `ocr_only`) + validasi hasil ekstraksi terhadap teks OCR.

Dipakai juga oleh mode `ocr_llm` untuk memvalidasi JSON dari LLM: apakah total & tanggal benar-benar ada di teks OCR,
apakah jumlah item (dikurangi diskon, ditambah pajak/layanan) cocok dengan total, dan apakah confidence OCR cukup.
"""
import re
from datetime import date

AMOUNT_RE = re.compile(r"(?<![\d.,])-?\(?(?:rp\.?\s*)?(\d{1,3}(?:[.,]\d{3})+)(?:,\d{2})?\)?(?![\d])", re.I)
PLAIN_RE = re.compile(r"(?<![\d.,:/-])(\d{3,7})(?![\d.,:/-])")
QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[xX×@]\s*(?:rp\.?\s*)?(\d{1,3}(?:[.,]\d{3})+|\d{3,7})")
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "mei": 5, "may": 5, "jun": 6, "jul": 7, "agu": 8, "agt": 8,
          "aug": 8, "sep": 9, "okt": 10, "oct": 10, "nov": 11, "des": 12, "dec": 12}
DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(20\d{2}|\d{2})")
DATE_YMD = re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
DATE_TXT = re.compile(r"(?<!\d)(\d{1,2})\s*(" + "|".join(MONTHS) + r")[a-z]*\.?\s*(20\d{2})", re.I)

TOTAL_KEYS = ["GRAND TOTAL", "GRANDTOTAL", "TOTAL BAYAR", "TOTALBAYAR", "TOTAL BELANJA", "TOTALBELANJA",
              "TOTAL HARGA", "JUMLAH BAYAR", "TOTAL", "JUMLAH"]
NOT_TOTAL = ["SUBTOTAL", "SUB TOTAL", "TOTAL ITEM", "TOTALITEM", "TOTAL QTY", "TOTAL DISKON", "TOTAL HEMAT",
             "TOTAL PPN", "TOTAL DISC", "JUMLAH ITEM", "JUMLAH QTY", "JUMLAH BARANG"]
DISCOUNT_KEYS = ["DISKON", "DISC", "POTONGAN", "HEMAT", "VOUCHER", "PROMO"]
TAX_KEYS = ["PPN", "PAJAK", "TAX", "SERVICE", "SERVIS", "PB1", "SVC"]
PAY_KEYS = ["TUNAI", "CASH", "KEMBALI", "CHANGE", "DEBIT", "KREDIT", "CREDIT", "QRIS", "BAYAR", "OVO", "GOPAY",
            "DANA", "SHOPEEPAY", "EDC", "KARTU"]
SKIP_KEYS = ["NPWP", "TELP", "TLP", "KASIR", "CASHIER", "NO.", "STRUK", "NOTA", "MEJA", "TABLE", "WAITER", "JL.",
             "JALAN", "TERIMA KASIH", "TERIMAKASIH", "DPP", "PEMBULATAN", "ITEM", "QTY", "MEMBER", "POIN", "SALDO"]
CATEGORY_HINTS = [
    ("makanan_minuman", ["RESTO", "RESTORAN", "CAFE", "KAFE", "KOPI", "COFFEE", "BAKSO", "WARUNG", "MAKAN", "RM.",
                         "BAKERY", "MIE ", "NASI", "SATE", "AYAM"]),
    ("transportasi", ["SPBU", "PERTAMINA", "BENSIN", "PERTALITE", "PERTAMAX", "SOLAR", "PARKIR", "TOL "]),
    ("kesehatan", ["APOTEK", "APOTIK", "FARMA", "OBAT", "KLINIK"]),
]


def to_int(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def amounts_in(text: str, plain: bool = False) -> list[int]:
    t = text.replace("O", "0") if re.search(r"\d[O]\d|\d[.,]\d*O", text) else text  # O→0 di dalam angka
    vals = [to_int(m.group(1)) for m in AMOUNT_RE.finditer(t)]
    if plain:  # angka tanpa pemisah ribuan (mis. "47700"); hanya untuk baris total & pencocokan validasi
        vals += [int(m.group(1)) for m in PLAIN_RE.finditer(t)]
    return vals


def dates_in(text: str) -> list[str]:
    out = []
    for m in DATE_YMD.finditer(text):
        out.append(_mk(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in DATE_DMY.finditer(text):
        y = int(m.group(3))
        out.append(_mk(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))))
    for m in DATE_TXT.finditer(text):
        out.append(_mk(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1))))
    return [d for d in out if d]


def _mk(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _has(up: str, keys: list[str]) -> bool:
    return any(k in up for k in keys)


def _total_rank(up: str) -> int | None:
    if _has(up, NOT_TOTAL):
        return None
    for i, k in enumerate(TOTAL_KEYS):
        if k in up:
            return i
    return None


def parse_lines(lines: list[dict]) -> dict:
    """Ekstraksi struk dari baris OCR tanpa LLM. Hasil memakai skema yang sama dengan jalur vision/LLM."""
    texts = [ln["text"] for ln in lines]
    ups = [t.upper() for t in texts]
    full = "\n".join(texts)
    notes = []

    # tanggal: tanggal pertama yang terbaca
    ds = dates_in(full)
    tanggal = ds[0] if ds else ""

    # total: baris berkata kunci TOTAL dengan prioritas tertinggi; bila sama, yang terakhir
    best, total_idx = None, None
    for i, up in enumerate(ups):
        r = _total_rank(up)
        if r is not None and (best is None or r <= best):
            best, total_idx = r, i
    total = 0
    if total_idx is not None:
        vals = amounts_in(texts[total_idx], plain=True)
        if not vals and total_idx + 1 < len(texts):
            vals = amounts_in(texts[total_idx + 1], plain=True)
        total = vals[-1] if vals else 0
    if not total:
        cands = [v for t, up in zip(texts, ups) if not _has(up, PAY_KEYS) for v in amounts_in(t)]
        total = max(cands) if cands else 0
        notes.append("total tidak ditemukan lewat kata kunci; pakai nominal terbesar")

    # toko: baris awal pertama yang berisi huruf dan bukan alamat/NPWP/tanggal
    toko = ""
    for t, up in zip(texts[:4], ups[:4]):
        letters = re.sub(r"[^A-Za-z]", "", t)
        if len(letters) >= 3 and not _has(up, ["JL.", "JL ", "JALAN", "NPWP", "TELP", "KASIR", "NO."]) and not dates_in(t):
            toko = t.strip()
            break

    # item & penyesuaian (diskon/pajak) di area sebelum baris total / subtotal
    end = len(texts)
    for i, up in enumerate(ups):
        if _total_rank(up) is not None or "SUBTOTAL" in up or "SUB TOTAL" in up:
            end = i
            break
    header_end = 0
    for i, (t, up) in enumerate(zip(texts[:end], ups[:end])):
        if i < 6 and (t.strip() == toko or _has(up, SKIP_KEYS) or dates_in(t)):
            header_end = i + 1
    items, pending = [], None
    diskon = pajak = 0
    for i in range(len(texts)):
        t, up = texts[i], ups[i]
        vals = amounts_in(t)
        if _has(up, DISCOUNT_KEYS) and vals:
            diskon += abs(vals[-1]); continue
        if _has(up, TAX_KEYS) and vals and not _has(up, ["TERMASUK", "INCL", "DPP"]):
            pajak += vals[-1]; continue
        if i < header_end or i >= end:
            continue
        if re.fullmatch(r"[-=_*.\s]+", t) or _has(up, PAY_KEYS) or _has(up, SKIP_KEYS):
            continue
        q = QTY_RE.search(t)
        name = re.sub(r"\s+", " ", AMOUNT_RE.sub(" ", QTY_RE.sub(" ", t))).strip(" -:")
        has_name = len(re.sub(r"[^A-Za-z]", "", name)) >= 2
        if q and not has_name and pending:
            sub = vals[-1] if vals else 0
            qty = float(q.group(1).replace(",", "."))
            unit = to_int(q.group(2))
            items.append({"nama": pending, "qty": qty if qty != int(qty) else int(qty), "harga_satuan": unit,
                          "subtotal": sub or int(round(qty * unit))})
            pending = None
        elif vals and has_name:
            it = {"nama": name, "subtotal": vals[-1]}
            if q:
                qty = float(q.group(1).replace(",", "."))
                it.update(qty=qty if qty != int(qty) else int(qty), harga_satuan=to_int(q.group(2)))
            else:
                it.update(qty=1, harga_satuan=vals[-1])
            items.append(it)
            pending = None
        elif has_name and not vals:
            pending = name

    up_full = full.upper()
    head = " ".join(ups[:3]) + " "  # kategori dari nama/kepala toko, bukan dari nama item
    kategori = next((c for c, keys in CATEGORY_HINTS if _has(head, keys)), "belanja")
    metode = next((k for k in ["QRIS", "DEBIT", "KREDIT", "CREDIT", "OVO", "GOPAY", "DANA", "SHOPEEPAY", "TUNAI", "CASH"]
                   if k in up_full), "")
    return {"toko": toko, "tanggal": tanggal, "items": items, "total": total, "diskon": diskon,
            "pajak_layanan": pajak, "metode_bayar": metode, "kategori": kategori,
            "keyakinan": 0.7, "catatan": "; ".join(notes)}


def validate(data: dict, ocr: dict, min_conf: float) -> dict:
    """Cek hasil ekstraksi terhadap teks OCR. Mengembalikan {lolos, checks, alasan}."""
    texts = [ln["text"] for ln in ocr.get("lines", [])]
    full = "\n".join(texts)
    ocr_amounts = set()
    for t in texts:
        ocr_amounts.update(amounts_in(t, plain=True))
    ocr_dates = sorted(set(dates_in(full)))
    total = int(data.get("total") or 0)
    items = data.get("items") or []
    s_items = sum(int(it.get("subtotal") or 0) for it in items)
    diskon, pajak = int(data.get("diskon") or 0), int(data.get("pajak_layanan") or 0)
    expected = s_items - diskon + pajak
    tol = max(100, int(total * 0.01))
    tgl = data.get("tanggal") or ""
    checks = {
        "total_positif": {"ok": total > 0, "total": total},
        "total_ada_di_teks_ocr": {"ok": total in ocr_amounts, "total": total},
        "tanggal_ada_di_teks_ocr": {"ok": (tgl in ocr_dates) if tgl else not ocr_dates, "tanggal": tgl,
                                    "tanggal_terbaca_ocr": ocr_dates},
        "item_cocok_total": {"ok": bool(items) and abs(expected - total) <= tol, "jumlah_item": len(items),
                             "jumlah_subtotal": s_items, "diskon": diskon, "pajak_layanan": pajak,
                             "subtotal_minus_diskon_plus_pajak": expected, "total": total, "toleransi": tol},
        "confidence_ocr": {"ok": ocr.get("mean_conf", 0) >= min_conf and len(texts) >= 3,
                           "mean_conf": ocr.get("mean_conf"), "min_conf": ocr.get("min_conf"), "ambang": min_conf,
                           "jumlah_baris": len(texts)},
    }
    gagal = [k for k, v in checks.items() if not v["ok"]]
    return {"lolos": not gagal, "gagal": gagal, "checks": checks}
