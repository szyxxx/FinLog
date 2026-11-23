"""Tool yang bisa dipanggil agent (function calling Ollama) + implementasinya di SQLite."""
import re
from datetime import date, datetime, timedelta
from typing import Any

from . import db
from .config import get_settings
from .db import CATEGORY_NAMES

KINDS = ["pemasukan", "pengeluaran"]


def _fn(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


TOOL_SPECS = [
    _fn(
        "catat_transaksi",
        "Mencatat SATU transaksi baru (pemasukan atau pengeluaran) ke database. Panggil sekali per transaksi.",
        {
            "jenis": {"type": "string", "enum": KINDS},
            "jumlah": {"type": "integer", "description": "Nominal dalam rupiah, bilangan bulat. 30rb = 30000, 4,5 juta = 4500000"},
            "kategori": {"type": "string", "enum": CATEGORY_NAMES},
            "deskripsi": {"type": "string", "description": "Keterangan singkat"},
            "merchant": {"type": "string", "description": "Nama toko/pengirim/penerima bila ada"},
            "tanggal": {"type": "string", "description": "YYYY-MM-DD. Kosongkan bila hari ini"},
            "mata_uang_asli": {"type": "string", "description": "Isi HANYA bila transaksi dalam mata uang asing (mis. USD); "
                                                                  "jumlah tetap dalam rupiah hasil konversi_kurs"},
            "jumlah_asli": {"type": "number", "description": "Nominal dalam mata uang asing (bila ada)"},
            "kurs": {"type": "number", "description": "Kurs dari konversi_kurs (bila ada)"},
        },
        ["jenis", "jumlah", "kategori", "deskripsi"],
    ),
    _fn(
        "ubah_transaksi",
        "Mengubah transaksi yang sudah ada berdasarkan id. Isi hanya field yang berubah.",
        {
            "id": {"type": "integer"},
            "jenis": {"type": "string", "enum": KINDS},
            "jumlah": {"type": "integer"},
            "kategori": {"type": "string", "enum": CATEGORY_NAMES},
            "deskripsi": {"type": "string"},
            "merchant": {"type": "string"},
            "tanggal": {"type": "string", "description": "YYYY-MM-DD"},
        },
        ["id"],
    ),
    _fn(
        "hapus_transaksi",
        "Menghapus transaksi berdasarkan id. Bila id belum pasti, cari dulu dengan cari_transaksi.",
        {"id": {"type": "integer"}},
        ["id"],
    ),
    _fn(
        "cari_transaksi",
        "Mencari/memfilter transaksi. Semua parameter opsional.",
        {
            "kata_kunci": {"type": "string", "description": "Dicocokkan ke deskripsi/merchant"},
            "jenis": {"type": "string", "enum": KINDS},
            "kategori": {"type": "string", "enum": CATEGORY_NAMES},
            "periode": {"type": "string", "enum": ["hari_ini", "kemarin", "minggu_ini", "bulan_ini", "bulan_lalu", "tahun_ini", "semua"]},
            "tanggal_mulai": {"type": "string", "description": "YYYY-MM-DD"},
            "tanggal_akhir": {"type": "string", "description": "YYYY-MM-DD"},
            "batas": {"type": "integer", "description": "Maksimal hasil, default 20"},
        },
    ),
    _fn(
        "ringkasan",
        "Ringkasan total pemasukan, pengeluaran, saldo, dan total per kategori untuk suatu periode.",
        {
            "periode": {"type": "string", "enum": ["hari_ini", "kemarin", "minggu_ini", "bulan_ini", "bulan_lalu", "tahun_ini", "semua"]},
            "tanggal_mulai": {"type": "string", "description": "YYYY-MM-DD (menimpa periode)"},
            "tanggal_akhir": {"type": "string", "description": "YYYY-MM-DD"},
            "kategori": {"type": "string", "enum": CATEGORY_NAMES},
        },
    ),
    _fn(
        "minta_konfirmasi",
        "Bertanya balik ke pengguna bila data ambigu (nominal/jenis/tanggal tidak jelas, beberapa transaksi cocok, "
        "atau kepercayaan rendah). JANGAN mencatat dulu; tunggu jawaban pengguna.",
        {
            "pertanyaan": {"type": "string"},
            "opsi": {"type": "array", "items": {"type": "string"}, "description": "Pilihan jawaban (opsional)"},
            "draf_transaksi": {"type": "object", "description": "Transaksi yang akan dicatat bila dikonfirmasi (opsional)"},
        },
        ["pertanyaan"],
    ),
]

EXPENSE_CATS = [n for n, k, _ in db.CATEGORIES if k == "pengeluaran"]
PERIODE_LAPORAN = ["minggu_lalu", "minggu_ini", "bulan_lalu", "bulan_ini"]

# ---- Alat & layanan baru (perluasan) ----
TOOL_SPECS += [
    _fn(
        "konversi_kurs",
        "ALAT EKSTERNAL (internet): kurs mata uang asing ke rupiah. Panggil SEBELUM catat_transaksi bila nominal dalam "
        "mata uang asing (dolar, yen, SGD, ringgit, euro, ...). Juga untuk pertanyaan kurs.",
        {
            "jumlah": {"type": "number", "description": "Nominal dalam mata uang asal"},
            "dari": {"type": "string", "description": "Kode mata uang asal, mis. USD, JPY, SGD, MYR, EUR"},
            "ke": {"type": "string", "description": "Kode mata uang tujuan, default IDR"},
            "tanggal": {"type": "string", "description": "YYYY-MM-DD tanggal transaksi (kurs historis). Kosong = hari ini"},
        },
        ["jumlah", "dari"],
    ),
    _fn(
        "analisis_data",
        "Sub-agen analis: menulis & menjalankan kode Python atas SEMUA transaksi untuk pertanyaan yang tidak bisa dijawab "
        "ringkasan/cari_transaksi: tren antarbulan, perbandingan periode, rata-rata, persentase, prediksi, hari paling boros, "
        "dan GRAFIK. Tulis pertanyaannya selengkap mungkin.",
        {
            "pertanyaan": {"type": "string", "description": "Pertanyaan analisis lengkap, termasuk periode yang dimaksud"},
            "grafik": {"type": "boolean", "description": "true bila pengguna meminta grafik/visualisasi"},
        },
        ["pertanyaan"],
    ),
    _fn(
        "atur_anggaran",
        "Menetapkan/mengubah batas anggaran BULANAN untuk satu kategori pengeluaran, atau 'total' untuk semua pengeluaran.",
        {
            "kategori": {"type": "string", "enum": EXPENSE_CATS + ["total"]},
            "batas_bulanan": {"type": "integer", "description": "Batas per bulan dalam rupiah"},
            "ambang_persen": {"type": "integer", "description": "Beri peringatan saat terpakai sekian persen (default 80)"},
        },
        ["kategori", "batas_bulanan"],
    ),
    _fn(
        "cek_anggaran",
        "Status anggaran bulan ini: terpakai, sisa, persen, proyeksi akhir bulan, sisa per hari. Kosongkan kategori untuk semua.",
        {"kategori": {"type": "string", "enum": EXPENSE_CATS + ["total"]}},
    ),
    _fn(
        "tambah_tagihan",
        "Mendaftarkan tagihan/langganan RUTIN (listrik, internet, kos, Netflix, cicilan) agar diingatkan otomatis sebelum "
        "jatuh tempo. Ini BUKAN mencatat pembayaran.",
        {
            "nama": {"type": "string"},
            "jumlah": {"type": "integer", "description": "Nominal rupiah per periode"},
            "jatuh_tempo": {"type": "string", "description": "Tanggal tiap bulan (mis. '5') atau tanggal YYYY-MM-DD berikutnya"},
            "frekuensi": {"type": "string", "enum": ["bulanan", "mingguan", "tahunan"]},
            "kategori": {"type": "string", "enum": EXPENSE_CATS},
            "catat_otomatis": {"type": "boolean", "description": "true HANYA bila pengguna memintanya: saat jatuh tempo otomasi "
                                                                 "langsung mencatat pengeluarannya. Default false (hanya pengingat)"},
        },
        ["nama", "jumlah", "jatuh_tempo"],
    ),
    _fn(
        "daftar_tagihan",
        "Daftar tagihan rutin beserta jatuh tempo berikutnya dan sisa hari.",
        {"status": {"type": "string", "enum": ["aktif", "segera", "terlambat"],
                    "description": "aktif = semua; segera = jatuh tempo <= 7 hari; terlambat = lewat jatuh tempo"}},
    ),
    _fn(
        "bayar_tagihan",
        "Mencatat pembayaran tagihan rutin (membuat transaksi pengeluaran dan memajukan jatuh tempo). Cari id dengan daftar_tagihan.",
        {
            "id": {"type": "integer"},
            "tanggal": {"type": "string", "description": "YYYY-MM-DD, default hari ini"},
            "jumlah": {"type": "integer", "description": "Bila nominal berbeda dari biasanya"},
        },
        ["id"],
    ),
    _fn(
        "deteksi_langganan",
        "Menganalisis riwayat transaksi untuk menemukan pengeluaran berulang (langganan/tagihan) yang belum didaftarkan.",
        {},
    ),
    _fn(
        "fakta_laporan",
        "Mengambil semua angka untuk laporan keuangan suatu periode (total, perbandingan periode sebelumnya, kategori, "
        "merchant teratas, anggaran, tagihan). Panggil sebelum buat_laporan.",
        {"periode": {"type": "string", "enum": PERIODE_LAPORAN}},
        ["periode"],
    ),
    _fn(
        "buat_laporan",
        "Membuat laporan PDF (grafik + narasimu). Narasi 2-4 paragraf pendek bahasa Indonesia berisi temuan penting dan "
        "1-2 saran; SEMUA nominal Rp harus persis dari fakta_laporan. Sebut kategori dengan label-nya, bukan nama teknis.",
        {
            "periode": {"type": "string", "enum": PERIODE_LAPORAN},
            "narasi": {"type": "string"},
        },
        ["periode", "narasi"],
    ),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]


def tools_for(input_type: str) -> list[dict]:
    """Run otomasi tidak punya pengguna yang bisa ditanya: minta_konfirmasi tidak diberikan."""
    if input_type == "automation":
        return [t for t in TOOL_SPECS if t["function"]["name"] != "minta_konfirmasi"]
    return TOOL_SPECS


class ToolError(Exception):
    pass


# ---------------------------------------------------------------- helpers

def parse_amount(v: Any) -> int:
    """Terima int/float/string gaya Indonesia: '30rb', '4,5 juta', 'Rp 28.800'."""
    if isinstance(v, bool):
        raise ToolError("jumlah tidak valid")
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).lower().replace("rp", "").strip()
    mult = 1
    for suf, m in (("juta", 1_000_000), ("jt", 1_000_000), ("ribu", 1000), ("rb", 1000), ("k", 1000)):
        if s.endswith(suf):
            s, mult = s[: -len(suf)].strip(), m
            break
    s = s.replace(" ", "")
    if mult > 1:
        s = s.replace(",", ".")
        return int(round(float(s) * mult))
    s = re.sub(r"[.,](?=\d{3}(\D|$))", "", s)  # pemisah ribuan
    s = s.replace(",", ".")
    try:
        return int(round(float(s)))
    except ValueError as e:
        raise ToolError(f"jumlah tidak valid: {v!r}") from e


def parse_date(v: Any) -> str:
    today = db.now_local().date()
    if not v:
        return today.isoformat()
    s = str(v).strip().lower()
    if s in ("hari ini", "hari_ini", "today"):
        return today.isoformat()
    if s == "kemarin":
        return (today - timedelta(days=1)).isoformat()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ToolError(f"tanggal tidak valid: {v!r} (pakai YYYY-MM-DD)")


def _category(kind: str, cat: str | None) -> str:
    if cat in CATEGORY_NAMES:
        return cat
    return "lainnya_masuk" if kind == "pemasukan" else "lainnya_keluar"


PERIODS = ["hari_ini", "kemarin", "minggu_ini", "bulan_ini", "bulan_lalu", "tahun_ini", "semua"]


def period_range(periode: str | None, mulai: str | None = None, akhir: str | None = None) -> tuple[str | None, str | None, str]:
    today = db.now_local().date()
    if mulai or akhir:
        a = parse_date(mulai) if mulai else None
        b = parse_date(akhir) if akhir else None
        return a, b, f"{a or '...'} s/d {b or '...'}"
    p = periode or "bulan_ini"
    if p not in PERIODS:
        raise ToolError(f"periode tidak valid: {periode!r}. Pilihan: {', '.join(PERIODS)}")
    if p == "hari_ini":
        a = b = today
    elif p == "kemarin":
        a = b = today - timedelta(days=1)
    elif p == "minggu_ini":
        a, b = today - timedelta(days=today.weekday()), today
    elif p == "bulan_ini":
        a, b = today.replace(day=1), today
    elif p == "bulan_lalu":
        last = today.replace(day=1) - timedelta(days=1)
        a, b = last.replace(day=1), last
    elif p == "tahun_ini":
        a, b = date(today.year, 1, 1), today
    else:
        return None, None, "semua"
    return a.isoformat(), b.isoformat(), f"{p} ({a.isoformat()} s/d {b.isoformat()})"


def _where(filters: dict) -> tuple[str, list]:
    w, p = ["deleted=0"], []
    if filters.get("jenis") in KINDS:
        w.append("kind=?"); p.append(filters["jenis"])
    if filters.get("kategori") in CATEGORY_NAMES:
        w.append("category=?"); p.append(filters["kategori"])
    if filters.get("kata_kunci"):
        # tidak peka spasi/huruf besar: OCR sering menghilangkan spasi ("SERBAADAMART" vs "SERBA ADA MART")
        w.append("(description LIKE ? OR merchant LIKE ? OR REPLACE(UPPER(COALESCE(merchant,'')||' '||"
                 "COALESCE(description,'')),' ','') LIKE ?)")
        k = f"%{filters['kata_kunci']}%"
        p += [k, k, "%" + filters["kata_kunci"].upper().replace(" ", "") + "%"]
    a, b, _ = period_range(filters.get("periode") or ("semua" if not (filters.get("tanggal_mulai") or filters.get("tanggal_akhir")) else None),
                           filters.get("tanggal_mulai"), filters.get("tanggal_akhir"))
    if a:
        w.append("date>=?"); p.append(a)
    if b:
        w.append("date<=?"); p.append(b)
    return " AND ".join(w), p


# ---------------------------------------------------------------- implementations

KIND_SYNONYMS = {
    "masuk": "pemasukan", "income": "pemasukan", "kredit": "pemasukan", "uang_masuk": "pemasukan",
    "keluar": "pengeluaran", "expense": "pengeluaran", "debit": "pengeluaran", "uang_keluar": "pengeluaran",
}
_CAT_KIND = {n: k for n, k, _ in db.CATEGORIES}


def normalize_kind(kind: Any, category: Any = None) -> str:
    """LLM kadang mengisi jenis dengan nama kategori (mis. 'transfer_masuk'). Petakan ke pemasukan/pengeluaran."""
    k = str(kind or "").strip().lower().replace(" ", "_")
    if k in KINDS:
        return k
    if k in KIND_SYNONYMS:
        return KIND_SYNONYMS[k]
    if k in _CAT_KIND:
        return _CAT_KIND[k]
    if category in _CAT_KIND:
        return _CAT_KIND[category]
    raise ToolError("jenis harus 'pemasukan' atau 'pengeluaran'")


def create_transaction(args: dict, source: str = "manual", run_id: str | None = None, items: Any = None) -> dict:
    tx = _create_transaction(args, source, run_id, items)
    fx = {}
    if args.get("mata_uang_asli") and str(args["mata_uang_asli"]).upper() not in ("IDR", "RP", "RUPIAH"):
        try:
            fx = {"orig_currency": str(args["mata_uang_asli"]).upper()[:3],
                  "orig_amount": float(args["jumlah_asli"]) if args.get("jumlah_asli") not in (None, "") else None,
                  "fx_rate": float(args["kurs"]) if args.get("kurs") not in (None, "") else None}
        except (TypeError, ValueError):
            fx = {"orig_currency": str(args["mata_uang_asli"]).upper()[:3]}
        db.execute("UPDATE transactions SET orig_currency=?, orig_amount=?, fx_rate=? WHERE id=?",
                   (fx.get("orig_currency"), fx.get("orig_amount"), fx.get("fx_rate"), tx["id"]))
        tx = get_transaction(tx["id"])
    return tx


def _create_transaction(args: dict, source: str = "manual", run_id: str | None = None, items: Any = None) -> dict:
    jenis_raw = str(args.get("jenis") or "").strip().lower()
    kind = normalize_kind(jenis_raw, args.get("kategori"))
    if not args.get("kategori") and jenis_raw in _CAT_KIND:
        args = {**args, "kategori": jenis_raw}
    amount = parse_amount(args.get("jumlah"))
    if amount <= 0:
        raise ToolError("jumlah harus > 0")
    now = db.iso_now()
    cur = db.execute(
        """INSERT INTO transactions(kind, amount, category, description, merchant, date, source, items_json, run_id,
           created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (kind, amount, _category(kind, args.get("kategori")), args.get("deskripsi"), args.get("merchant"),
         parse_date(args.get("tanggal")), source, db.dumps(items) if items else None, run_id, now, now),
    )
    return get_transaction(cur.lastrowid)


def get_transaction(tx_id: int) -> dict:
    row = db.query_one("SELECT * FROM transactions WHERE id=? AND deleted=0", (tx_id,))
    if not row:
        raise ToolError(f"transaksi id={tx_id} tidak ditemukan")
    return db.tx_row(row)


def check_category_strict(kind: str, cat: str | None) -> None:
    """Validasi ketat untuk REST (PATCH): kategori harus dikenal dan cocok dengan jenis. Jalur agent tetap longgar."""
    if cat is None:
        return
    if cat not in CATEGORY_NAMES:
        raise ToolError(f"kategori tidak dikenal: {cat!r}. Pilihan: {', '.join(CATEGORY_NAMES)}")
    if _CAT_KIND[cat] != kind:
        valid = [n for n in CATEGORY_NAMES if _CAT_KIND[n] == kind]
        raise ToolError(f"kategori {cat!r} adalah kategori {_CAT_KIND[cat]}, tidak cocok untuk jenis {kind!r}. "
                        f"Pilihan untuk {kind}: {', '.join(valid)}")


def update_transaction(args: dict, strict: bool = False) -> dict:
    """strict=True (REST): kategori tak dikenal / tak cocok jenis ditolak, bukan dipetakan diam-diam ke lainnya_*."""
    tx_id = int(args["id"])
    cur = get_transaction(tx_id)
    if strict:
        if args.get("jenis") not in (None, "") and args["jenis"] not in KINDS:
            raise ToolError("jenis harus 'pemasukan' atau 'pengeluaran'")
        new_kind = args.get("jenis") or cur["kind"]
        new_cat = args.get("kategori") or cur["category"]
        if args.get("kategori") not in (None, "") or args.get("jenis") not in (None, ""):
            check_category_strict(new_kind, new_cat)
    sets, p = [], []
    mapping = {"jenis": "kind", "jumlah": "amount", "kategori": "category", "deskripsi": "description",
               "merchant": "merchant", "tanggal": "date"}
    for k, col in mapping.items():
        if args.get(k) in (None, ""):
            continue
        v = args[k]
        if k == "jumlah":
            v = parse_amount(v)
        elif k == "tanggal":
            v = parse_date(v)
        elif k == "jenis":
            v = normalize_kind(v, args.get("kategori"))
        elif k == "kategori":
            v = _category(args.get("jenis") or cur["kind"], v)
        sets.append(f"{col}=?"); p.append(v)
    if not sets:
        raise ToolError("tidak ada field yang diubah")
    sets.append("updated_at=?"); p.append(db.iso_now())
    db.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE id=?", (*p, tx_id))
    return {"sebelum": cur, "sesudah": get_transaction(tx_id)}


def delete_transaction(tx_id: int) -> dict:
    cur = get_transaction(int(tx_id))
    db.execute("UPDATE transactions SET deleted=1, updated_at=? WHERE id=?", (db.iso_now(), int(tx_id)))
    return {"dihapus": cur}


def search_transactions(args: dict) -> dict:
    w, p = _where(args)
    limit = max(1, min(int(args.get("batas") or 20), 200))
    rows = db.query(f"SELECT * FROM transactions WHERE {w} ORDER BY date DESC, id DESC LIMIT ?", (*p, limit))
    total = db.query_one(f"SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM transactions WHERE {w}", p)
    return {"jumlah_hasil": total["n"], "total_nominal": total["s"], "transaksi": [db.tx_row(r) for r in rows]}


def summary(args: dict) -> dict:
    a, b, label = period_range(args.get("periode"), args.get("tanggal_mulai"), args.get("tanggal_akhir"))
    w, p = ["deleted=0"], []
    if a:
        w.append("date>=?"); p.append(a)
    if b:
        w.append("date<=?"); p.append(b)
    if args.get("kategori") in CATEGORY_NAMES:
        w.append("category=?"); p.append(args["kategori"])
    ws = " AND ".join(w)
    rows = db.query(f"SELECT kind, category, COUNT(*) n, SUM(amount) total FROM transactions WHERE {ws} "
                    f"GROUP BY kind, category ORDER BY total DESC", p)
    inc = sum(r["total"] for r in rows if r["kind"] == "pemasukan")
    exp = sum(r["total"] for r in rows if r["kind"] == "pengeluaran")
    return {
        "periode": label,
        "total_pemasukan": inc,
        "total_pengeluaran": exp,
        "saldo": inc - exp,
        "per_kategori": [{"jenis": r["kind"], "kategori": r["category"], "jumlah_transaksi": r["n"], "total": r["total"]} for r in rows],
    }


def execute_tool(name: str, args: dict, ctx: dict) -> dict:
    """Jalankan tool. ctx: run_id, source, items (opsional dari struk)."""
    from .services import planning

    if name == "catat_transaksi":
        if not args.get("tanggal") and ctx.get("default_date"):
            args = {**args, "tanggal": ctx["default_date"]}
        tx = create_transaction(args, source=ctx.get("source", "manual"), run_id=ctx.get("run_id"), items=ctx.get("items"))
        out = {"ok": True, "transaksi": tx}
        warns = planning.check_budget_after_write(tx)
        if warns:
            out["peringatan_anggaran"] = warns
            out["instruksi"] = "Sampaikan peringatan anggaran ini ke pengguna dalam jawabanmu."
        return out
    if name == "ubah_transaksi":
        res = update_transaction(args)
        warns = planning.check_budget_after_write(res["sesudah"])
        return {"ok": True, **res, **({"peringatan_anggaran": warns} if warns else {})}
    if name == "hapus_transaksi":
        return {"ok": True, **delete_transaction(args.get("id"))}
    if name == "cari_transaksi":
        return {"ok": True, **search_transactions(args)}
    if name == "ringkasan":
        return {"ok": True, **summary(args)}
    if name == "minta_konfirmasi":
        return {"ok": True, "menunggu_jawaban_pengguna": True}
    try:
        return _execute_planning(name, args, ctx, planning)
    except planning.PlanError as e:
        raise ToolError(str(e)) from e


def _execute_planning(name: str, args: dict, ctx: dict, planning) -> dict:
    if name == "atur_anggaran":
        b = planning.set_budget(args.get("kategori"), parse_amount(args.get("batas_bulanan")), args.get("ambang_persen"))
        return {"ok": True, "anggaran": b}
    if name == "cek_anggaran":
        cat = args.get("kategori")
        cat = planning.TOTAL if cat in ("total", "semua") else cat
        st = planning.budget_status(cat or None)
        if not st["anggaran"]:
            st["catatan"] = "Belum ada anggaran yang diatur" + (f" untuk {cat}" if cat else "") + ". Tawarkan atur_anggaran."
        return {"ok": True, **st}
    if name == "tambah_tagihan":
        b = planning.add_bill(args.get("nama"), parse_amount(args.get("jumlah")), args.get("jatuh_tempo"),
                              args.get("kategori") or "tagihan", args.get("frekuensi") or "bulanan",
                              bool(args.get("catat_otomatis")))
        days = get_settings().bill_remind_days
        return {"ok": True, "tagihan": b, "catatan": f"Pengingat otomatis dikirim H-{days} dan saat jatuh tempo."}
    if name == "daftar_tagihan":
        return {"ok": True, **planning.list_bills(args.get("status") or "aktif")}
    if name == "bayar_tagihan":
        if args.get("id") in (None, ""):
            raise ToolError("id tagihan wajib; cari dulu dengan daftar_tagihan")
        res = planning.pay_bill(int(args["id"]), parse_date(args.get("tanggal")) if args.get("tanggal") else None,
                                parse_amount(args["jumlah"]) if args.get("jumlah") else None,
                                source=ctx.get("source", "text"), run_id=ctx.get("run_id"))
        warns = planning.check_budget_after_write(res["transaksi"])
        return {"ok": True, **res, **({"peringatan_anggaran": warns} if warns else {})}
    if name == "deteksi_langganan":
        return {"ok": True, **planning.detect_recurring()}
    if name == "fakta_laporan":
        from .services import reports
        try:
            f = reports.compute_facts(args.get("periode"))
        except ValueError as e:
            raise ToolError(str(e)) from e
        f.pop("pengeluaran_harian", None)  # besar dan tidak perlu untuk narasi (tetap dipakai grafik PDF)
        return {"ok": True, **f}
    raise ToolError(f"tool tidak dikenal: {name}")


ASYNC_TOOLS = {"konversi_kurs", "analisis_data", "buat_laporan"}


async def execute_tool_async(name: str, args: dict, ctx: dict, tracer=None) -> dict:
    """Eksekusi tool untuk kedua mesin. Tool lama tetap sinkron; tool baru yang butuh internet, sub-agen, atau
    pembuatan PDF berjalan async (tracer dipakai sub-agen analis untuk mencatat langkahnya)."""
    import asyncio

    if name not in ASYNC_TOOLS:
        return execute_tool(name, args, ctx)
    if name == "konversi_kurs":
        from .services import fx
        try:
            amt = float(args.get("jumlah"))
        except (TypeError, ValueError):
            amt = float(parse_amount(args.get("jumlah")))
        on = parse_date(args["tanggal"]) if args.get("tanggal") else None
        try:
            r = await fx.convert(amt, args.get("dari"), args.get("ke") or "IDR", on)
        except fx.FxError as e:
            raise ToolError(f"kurs gagal: {e}") from e
        return {"ok": True, **r, "instruksi": "Bila pengguna MELAPORKAN transaksi (bukan sekadar bertanya kurs), LANGSUNG panggil "
                "catat_transaksi sekarang tanpa bertanya balik: jumlah = hasil (rupiah), isi mata_uang_asli, jumlah_asli, "
                "kurs. Tanggal yang tidak disebut = hari ini."}
    if name == "analisis_data":
        from .services import analyst
        if not (args.get("pertanyaan") or "").strip():
            raise ToolError("pertanyaan analisis wajib diisi")
        try:
            r = await analyst.analyze(args["pertanyaan"], tracer, ctx.get("run_id"), bool(args.get("grafik")))
        except analyst.AnalysisError as e:
            raise ToolError(str(e)) from e
        ctx["artifacts"] = [*ctx.get("artifacts", []), *[g["id"] for g in r.get("grafik", [])]]
        return r
    if name == "buat_laporan":
        from .services import reports
        narr = (args.get("narasi") or "").strip()
        if len(narr) < 80:
            raise ToolError("narasi terlalu pendek; tulis 2-4 paragraf dari fakta_laporan")
        try:
            facts = reports.compute_facts(args.get("periode"))
        except ValueError as e:
            raise ToolError(str(e)) from e
        bad = reports.check_narrative(narr, facts)
        if bad:
            ctx["report_rejections"] = ctx.get("report_rejections", 0) + 1
            raise ToolError("narasi memuat nominal yang TIDAK ada di fakta_laporan: " + ", ".join(bad[:6])
                            + ". Perbaiki narasi: pakai hanya angka dari fakta_laporan, lalu panggil buat_laporan lagi.")
        r = await asyncio.to_thread(reports.create_report, args.get("periode"), narr, ctx.get("run_id"),
                                    ctx.get("report_kind"))
        ctx["report_id"] = r["id"]
        return {"ok": True, "laporan": r, "catatan": "PDF sudah tersedia di aplikasi (menu Otomasi > Laporan)."}
    raise ToolError(f"tool tidak dikenal: {name}")
