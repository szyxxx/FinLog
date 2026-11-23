"""Layanan baru: anggaran per kategori, tagihan rutin, deteksi langganan, dan notifikasi.

Semua perhitungan angka deterministik (SQL + Python). Model AI hanya memutuskan KAPAN memanggilnya dan
menyampaikan hasilnya, jadi angka di jawaban selalu berasal dari database, bukan karangan model.
"""
import calendar
import re
import statistics
from collections import defaultdict
from datetime import date, timedelta

from .. import db
from ..db import CATEGORY_NAMES
from ..trace import publish

EXPENSE_CATS = [n for n, k, _ in db.CATEGORIES if k == "pengeluaran"]
TOTAL = "_total"


class PlanError(Exception):
    pass


def _rp(v) -> str:
    v = int(round(v))
    return ("-" if v < 0 else "") + f"Rp{abs(v):,}".replace(",", ".")


def today() -> date:
    return db.now_local().date()


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int, day: int | None = None) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    day = day or d.day
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


# ------------------------------------------------------------------ notifikasi

def notify(kind: str, title: str, body: str = "", level: str = "info", data: dict | None = None,
           dedup_key: str | None = None) -> dict | None:
    """Buat notifikasi (sekali per dedup_key). Dikirim live ke web app lewat SSE global (event `notification`)."""
    cur = db.execute("INSERT OR IGNORE INTO notifications(kind, level, title, body, data_json, dedup_key, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (kind, level, title, body, db.dumps(data) if data else None, dedup_key,
                                                 db.iso_now()))
    if not cur.rowcount:
        return None
    row = notification_row(db.query_one("SELECT * FROM notifications WHERE id=?", (cur.lastrowid,)))
    publish(None, "notification", row)
    return row


def notification_row(r: dict) -> dict:
    r = dict(r)
    r["data"] = db.loads(r.pop("data_json", None))
    r["read"] = bool(r["read"])
    return r


def list_notifications(unread_only: bool = False, limit: int = 50) -> dict:
    w = "WHERE read=0" if unread_only else ""
    rows = db.query(f"SELECT * FROM notifications {w} ORDER BY id DESC LIMIT ?", (limit,))
    unread = db.query_one("SELECT COUNT(*) n FROM notifications WHERE read=0")["n"]
    return {"unread": unread, "notifications": [notification_row(r) for r in rows]}


def mark_read(ids: list[int] | None) -> int:
    if ids:
        q = ",".join("?" * len(ids))
        return db.execute(f"UPDATE notifications SET read=1 WHERE id IN ({q})", ids).rowcount
    return db.execute("UPDATE notifications SET read=1 WHERE read=0").rowcount


# ------------------------------------------------------------------ anggaran

def set_budget(category: str, monthly_limit: int, alert_pct: int | None = None) -> dict:
    cat = (category or "").strip().lower()
    if cat in ("total", "semua", "keseluruhan", "_total", "all"):
        cat = TOTAL
    if cat != TOTAL and cat not in EXPENSE_CATS:
        raise PlanError(f"kategori anggaran harus kategori pengeluaran atau 'total'. Pilihan: {', '.join(EXPENSE_CATS)}")
    if monthly_limit <= 0:
        raise PlanError("batas anggaran harus > 0")
    pct = int(alert_pct or 80)
    if not 10 <= pct <= 100:
        raise PlanError("ambang peringatan harus 10-100 persen")
    now = db.iso_now()
    db.execute("INSERT INTO budgets(category, monthly_limit, alert_pct, created_at, updated_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(category) DO UPDATE SET monthly_limit=excluded.monthly_limit, alert_pct=excluded.alert_pct, "
               "updated_at=excluded.updated_at", (cat, monthly_limit, pct, now, now))
    return budget_status(cat)["anggaran"][0]


def delete_budget(category: str) -> bool:
    return bool(db.execute("DELETE FROM budgets WHERE category=?", (category,)).rowcount)


def budget_status(category: str | None = None, on: date | None = None) -> dict:
    """Status anggaran bulan berjalan: terpakai, sisa, persen, proyeksi akhir bulan (linear per hari)."""
    d = on or today()
    a, b = month_bounds(d)
    days_total, days_gone = b.day, d.day
    spent = {r["category"]: r["s"] for r in db.query(
        "SELECT category, SUM(amount) s FROM transactions WHERE deleted=0 AND kind='pengeluaran' AND date>=? AND date<=? "
        "GROUP BY category", (a.isoformat(), d.isoformat()))}
    spent[TOTAL] = sum(spent.values())
    rows = db.query("SELECT * FROM budgets ORDER BY CASE WHEN category='_total' THEN 0 ELSE 1 END, category")
    if category:
        rows = [r for r in rows if r["category"] == category]
    out = []
    for r in rows:
        used = int(spent.get(r["category"], 0))
        lim = r["monthly_limit"]
        pct = round(used / lim * 100, 1) if lim else 0
        proj = round(used / max(days_gone, 1) * days_total)
        if pct >= 100:
            st = "terlampaui"
        elif pct >= r["alert_pct"]:
            st = "waspada"
        elif proj > lim:
            st = "diproyeksikan_lewat"
        else:
            st = "aman"
        out.append({"kategori": r["category"], "batas": lim, "terpakai": used, "sisa": lim - used, "persen": pct,
                    "ambang_persen": r["alert_pct"], "proyeksi_akhir_bulan": proj, "status": st,
                    "sisa_per_hari": max(0, round((lim - used) / max(days_total - days_gone + 1, 1)))})
    return {"bulan": a.strftime("%Y-%m"), "hari_ke": days_gone, "jumlah_hari": days_total, "anggaran": out,
            "kategori_tanpa_anggaran": [c for c in EXPENSE_CATS if c not in {r["category"] for r in rows} and spent.get(c)]}


def check_budget_after_write(tx: dict) -> list[str]:
    """Dipanggil setelah transaksi pengeluaran dicatat/diubah: buat notifikasi bila melewati ambang, kembalikan
    peringatan singkat untuk disampaikan agent ke pengguna."""
    if tx.get("kind") != "pengeluaran":
        return []
    try:
        d = date.fromisoformat(tx["date"])
    except (TypeError, ValueError):
        return []
    if (d.year, d.month) != (today().year, today().month):
        return []
    warns = []
    for b in budget_status(on=today())["anggaran"]:
        if b["kategori"] not in (tx.get("category"), TOTAL) or b["status"] not in ("waspada", "terlampaui"):
            continue
        label = "total pengeluaran" if b["kategori"] == TOTAL else b["kategori"]
        if b["status"] == "terlampaui":
            msg = f"Anggaran {label} bulan ini TERLAMPAUI: {_rp(b['terpakai'])} dari {_rp(b['batas'])} ({b['persen']}%)."
            level = "danger"
        else:
            msg = (f"Anggaran {label} sudah {b['persen']}% ({_rp(b['terpakai'])} dari {_rp(b['batas'])}), "
                   f"sisa {_rp(b['sisa'])}.")
            level = "warning"
        warns.append(msg)
        notify("anggaran", f"Anggaran {label}: {b['status']}", msg, level, {"anggaran": b, "transaksi_id": tx.get("id")},
               dedup_key=f"budget:{b['kategori']}:{d.strftime('%Y-%m')}:{b['status']}")
    return warns


# ------------------------------------------------------------------ tagihan rutin

FREQS = ("mingguan", "bulanan", "tahunan")


def _bill_row(r: dict) -> dict:
    r = dict(r)
    r["auto_record"], r["active"] = bool(r["auto_record"]), bool(r["active"])
    try:
        r["hari_lagi"] = (date.fromisoformat(r["next_due"]) - today()).days
    except ValueError:
        r["hari_lagi"] = None
    return r


def _parse_due(v, freq: str) -> date:
    t = today()
    if v in (None, ""):
        raise PlanError("jatuh tempo wajib diisi (tanggal YYYY-MM-DD atau tanggal 1-31 tiap bulan)")
    s = str(v).strip().lower().removeprefix("tanggal").strip()
    if re.fullmatch(r"\d{1,2}", s):
        day = int(s)
        if not 1 <= day <= 31:
            raise PlanError("tanggal jatuh tempo harus 1-31")
        cand = date(t.year, t.month, min(day, calendar.monthrange(t.year, t.month)[1]))
        return cand if cand >= t else add_months(cand, 1, day)
    from ..tools import ToolError, parse_date  # impor lokal: hindari impor melingkar
    try:
        return date.fromisoformat(parse_date(s))
    except ToolError as e:
        raise PlanError(str(e)) from e


def add_bill(name: str, amount: int, next_due, category: str = "tagihan", frequency: str = "bulanan",
             auto_record: bool = False, merchant: str | None = None, origin: str = "manual") -> dict:
    if not (name or "").strip():
        raise PlanError("nama tagihan wajib diisi")
    if amount <= 0:
        raise PlanError("nominal tagihan harus > 0")
    freq = (frequency or "bulanan").strip().lower()
    if freq not in FREQS:
        raise PlanError(f"frekuensi harus salah satu dari {', '.join(FREQS)}")
    cat = category if category in EXPENSE_CATS else "tagihan"
    due = _parse_due(next_due, freq)
    dup = db.query_one("SELECT * FROM bills WHERE active=1 AND LOWER(name)=LOWER(?)", (name.strip(),))
    if dup:
        raise PlanError(f"tagihan aktif bernama {name!r} sudah ada (id={dup['id']}). Ubah atau hapus dulu.")
    now = db.iso_now()
    cur = db.execute("INSERT INTO bills(name, amount, category, merchant, frequency, next_due, auto_record, origin, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (name.strip(), int(amount), cat, merchant, freq, due.isoformat(), int(bool(auto_record)), origin, now, now))
    return get_bill(cur.lastrowid)


def get_bill(bill_id: int) -> dict:
    r = db.query_one("SELECT * FROM bills WHERE id=?", (int(bill_id),))
    if not r:
        raise PlanError(f"tagihan id={bill_id} tidak ditemukan")
    return _bill_row(r)


def list_bills(status: str = "aktif", within_days: int | None = None) -> dict:
    rows = [_bill_row(r) for r in db.query("SELECT * FROM bills WHERE active=1 ORDER BY next_due, id")]
    if status == "terlambat":
        rows = [r for r in rows if (r["hari_lagi"] or 0) < 0]
    elif status == "segera":
        lim = within_days if within_days is not None else 7
        rows = [r for r in rows if (r["hari_lagi"] or 0) <= lim]
    total_month = 0
    t = today()
    a, b = month_bounds(t)
    for r in db.query("SELECT * FROM bills WHERE active=1"):
        if r["frequency"] == "bulanan":
            total_month += r["amount"]
        elif r["frequency"] == "mingguan":
            total_month += r["amount"] * 4
        elif a.isoformat() <= r["next_due"] <= b.isoformat():
            total_month += r["amount"]
    return {"tagihan": rows, "jumlah": len(rows), "perkiraan_total_per_bulan": total_month}


def _advance(d: date, freq: str, anchor_day: int) -> date:
    if freq == "mingguan":
        return d + timedelta(days=7)
    if freq == "tahunan":
        return add_months(d, 12, anchor_day)
    return add_months(d, 1, anchor_day)


def pay_bill(bill_id: int, paid_on: str | None = None, amount: int | None = None, source: str = "tagihan",
             run_id: str | None = None) -> dict:
    """Catat pembayaran tagihan sebagai transaksi pengeluaran lalu majukan jatuh tempo berikutnya."""
    from .. import tools  # impor lokal: hindari impor melingkar
    bill = get_bill(bill_id)
    if not bill["active"]:
        raise PlanError(f"tagihan id={bill_id} sudah nonaktif")
    due = date.fromisoformat(bill["next_due"])
    tx = tools.create_transaction({"jenis": "pengeluaran", "jumlah": amount or bill["amount"], "kategori": bill["category"],
                                   "deskripsi": f"Bayar {bill['name']}", "merchant": bill.get("merchant") or bill["name"],
                                   "tanggal": paid_on or today().isoformat()}, source=source, run_id=run_id)
    db.execute("UPDATE transactions SET bill_id=? WHERE id=?", (bill["id"], tx["id"]))
    # satu pembayaran = satu periode; tagihan yang telat beberapa periode tetap terlihat terlambat
    nxt = _advance(due, bill["frequency"], due.day)
    db.execute("UPDATE bills SET next_due=?, last_paid=?, updated_at=? WHERE id=?",
               (nxt.isoformat(), tx["date"], db.iso_now(), bill["id"]))
    return {"transaksi": {**tx, "bill_id": bill["id"]}, "tagihan": get_bill(bill["id"])}


def update_bill(bill_id: int, fields: dict) -> dict:
    bill = get_bill(bill_id)
    sets, p = [], []
    for k, col in (("nama", "name"), ("jumlah", "amount"), ("kategori", "category"), ("frekuensi", "frequency"),
                   ("catat_otomatis", "auto_record"), ("aktif", "active"), ("jatuh_tempo", "next_due")):
        if k not in fields or fields[k] is None:
            continue
        v = fields[k]
        if k == "jumlah":
            v = int(v)
        elif k == "frekuensi" and v not in FREQS:
            raise PlanError(f"frekuensi harus salah satu dari {', '.join(FREQS)}")
        elif k == "kategori" and v not in EXPENSE_CATS:
            raise PlanError("kategori tagihan harus kategori pengeluaran")
        elif k in ("catat_otomatis", "aktif"):
            v = int(bool(v))
        elif k == "jatuh_tempo":
            v = _parse_due(v, fields.get("frekuensi") or bill["frequency"]).isoformat()
        sets.append(f"{col}=?"); p.append(v)
    if not sets:
        raise PlanError("tidak ada field yang diubah")
    sets.append("updated_at=?"); p.append(db.iso_now())
    db.execute(f"UPDATE bills SET {', '.join(sets)} WHERE id=?", (*p, bill["id"]))
    return get_bill(bill["id"])


# ------------------------------------------------------------------ deteksi langganan / pengeluaran rutin

def _key(t: dict) -> str:
    base = t.get("merchant") or t.get("description") or ""
    base = re.sub(r"\b(bayar|langganan|tagihan|bulan|ini|untuk)\b", " ", base.lower())
    return re.sub(r"[^a-z0-9]", "", base)[:24]


def detect_recurring(months: int = 6, min_occurrences: int = 2) -> dict:
    """Cari pengeluaran berulang (nominal mirip, jarak ±mingguan/bulanan) yang belum terdaftar sebagai tagihan."""
    t = today()
    start = add_months(t.replace(day=1), -months + 1).isoformat()
    txs = db.query("SELECT * FROM transactions WHERE deleted=0 AND kind='pengeluaran' AND date>=? ORDER BY date",
                   (start,))
    groups: dict[str, list[dict]] = defaultdict(list)
    for x in txs:
        k = _key(x)
        if len(k) >= 3:
            groups[k].append(x)
    bills = db.query("SELECT name, merchant FROM bills WHERE active=1")
    known = {_key({"merchant": b["merchant"] or b["name"]}) for b in bills} | {_key({"merchant": b["name"]}) for b in bills}
    known.discard("")
    out = []
    for k, xs in groups.items():
        if len(xs) < min_occurrences or any(k in kk or kk in k for kk in known):
            continue
        amounts = [x["amount"] for x in xs]
        med = statistics.median(amounts)
        if med <= 0 or any(abs(a - med) / med > 0.15 for a in amounts):
            continue
        ds = [date.fromisoformat(x["date"]) for x in xs]
        gaps = [(b - a).days for a, b in zip(ds, ds[1:])]
        if not gaps:
            continue
        g = statistics.median(gaps)
        if 25 <= g <= 35:
            freq, nxt = "bulanan", add_months(ds[-1], 1)
        elif 6 <= g <= 8:
            freq, nxt = "mingguan", ds[-1] + timedelta(days=7)
        else:
            continue
        spread = max(gaps) - min(gaps)
        conf = round(max(0.4, min(0.98, 0.6 + 0.1 * (len(xs) - 2) - 0.02 * spread)), 2)
        out.append({"nama": xs[-1].get("merchant") or xs[-1].get("description"), "kunci": k, "frekuensi": freq,
                    "nominal_tipikal": int(med), "kategori": xs[-1]["category"], "kemunculan": len(xs),
                    "tanggal": [x["date"] for x in xs], "perkiraan_berikutnya": nxt.isoformat(), "keyakinan": conf,
                    "transaksi_id": [x["id"] for x in xs]})
    out.sort(key=lambda r: (-r["keyakinan"], -r["nominal_tipikal"]))
    return {"rentang_mulai": start, "kandidat": out, "jumlah": len(out)}
