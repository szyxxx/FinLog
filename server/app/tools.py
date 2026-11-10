"""Tool yang bisa dipanggil agent (function calling Ollama) + implementasinya di SQLite."""
import re
from datetime import date, datetime, timedelta
from typing import Any

from . import db
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

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]


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
    if name == "catat_transaksi":
        if not args.get("tanggal") and ctx.get("default_date"):
            args = {**args, "tanggal": ctx["default_date"]}
        tx = create_transaction(args, source=ctx.get("source", "manual"), run_id=ctx.get("run_id"), items=ctx.get("items"))
        return {"ok": True, "transaksi": tx}
    if name == "ubah_transaksi":
        return {"ok": True, **update_transaction(args)}
    if name == "hapus_transaksi":
        return {"ok": True, **delete_transaction(args.get("id"))}
    if name == "cari_transaksi":
        return {"ok": True, **search_transactions(args)}
    if name == "ringkasan":
        return {"ok": True, **summary(args)}
    if name == "minta_konfirmasi":
        return {"ok": True, "menunggu_jawaban_pengguna": True}
    raise ToolError(f"tool tidak dikenal: {name}")
