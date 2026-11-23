"""Isi database dengan data demo realistis (±4 bulan) untuk mencoba layanan analisis, anggaran, tagihan, dan laporan.

    uv run python scripts/seed_demo.py            # tambah data demo (DB dicadangkan dulu ke data/finance.db.bak-*)
    uv run python scripts/seed_demo.py --hapus    # hapus semua data demo (source='demo'), data lain tidak tersentuh

Data dibuat deterministik (seed tetap) untuk profil mahasiswa: kiriman bulanan, honor asisten, kos, makan harian,
transportasi, beberapa langganan (Spotify, Netflix) yang sengaja TIDAK didaftarkan sebagai tagihan agar bisa ditemukan
oleh `deteksi_langganan`.
"""
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402

MAKAN = [("Warteg Bahari", 15000, 28000), ("Kantin kampus", 12000, 25000), ("Kopi Kenangan", 22000, 32000),
         ("Indomaret", 18000, 65000), ("GoFood ayam geprek", 28000, 45000), ("Bakso Pak Kumis", 18000, 25000),
         ("Mie Gacoan", 20000, 38000), ("Starbucks", 48000, 62000)]
TRANS = [("Gojek", 12000, 28000), ("Grab", 14000, 32000), ("Pertamina bensin", 25000, 50000), ("Parkir", 3000, 5000)]
HIBUR = [("CGV bioskop", 45000, 60000), ("Steam game", 60000, 150000), ("Timezone", 50000, 100000)]
BELANJA = [("Tokopedia", 80000, 350000), ("Shopee", 50000, 250000), ("Uniqlo", 199000, 399000)]


def rnd(rng, a, b, step=500):
    return int(rng.randint(a // step, b // step) * step)


def seed() -> None:
    s = get_settings()
    src = Path(s.db_path)
    if src.exists():
        # API backup SQLite: ikut menyalin isi file -wal (copyfile biasa bisa menghasilkan cadangan kosong/usang)
        bak = src.with_name(src.name + f".bak-{db.now_local().strftime('%Y%m%d-%H%M%S')}")
        with sqlite3.connect(src) as s_conn, sqlite3.connect(bak) as b_conn:
            s_conn.backup(b_conn)
        print("cadangan DB:", bak)
    db.init_db()
    if db.query_one("SELECT COUNT(*) n FROM transactions WHERE source='demo'")["n"]:
        print("data demo sudah ada; jalankan dengan --hapus dulu bila ingin membuat ulang")
        return
    rng = random.Random(20261123)
    today = db.now_local().date()
    start = date(today.year, today.month, 1) - timedelta(days=1)
    start = date(start.year, start.month, 1)
    start = (start - timedelta(days=1)).replace(day=1)
    start = (start - timedelta(days=1)).replace(day=1)  # awal 3 bulan sebelum bulan ini
    rows = []

    def add(d, kind, amount, cat, desc, merchant=None):
        rows.append((kind, amount, cat, desc, merchant, d.isoformat()))

    d = start
    while d <= today:
        if d.day == 1:
            add(d, "pemasukan", 3_500_000, "transfer_masuk", "Kiriman bulanan orang tua", "Transfer BCA")
            add(d, "pengeluaran", 1_250_000, "tagihan", "Bayar kos", "Kos Pak Darto")
        if d.day == 25:
            add(d, "pemasukan", 750_000, "gaji", "Honor asisten praktikum", "Fakultas")
        if d.day == 5:
            add(d, "pengeluaran", 350_000, "tagihan", "Internet IndiHome", "IndiHome")
        if d.day == 12:
            add(d, "pengeluaran", 54_990, "hiburan", "Langganan Spotify", "Spotify")
        if d.day == 20:
            add(d, "pengeluaran", 65_000, "hiburan", "Langganan Netflix", "Netflix")
        if d.day == 15:
            add(d, "pengeluaran", rnd(rng, 50_000, 100_000), "tagihan", "Pulsa & paket data", "Telkomsel")
        # makan: 2-3x sehari, akhir bulan sedikit lebih boros (biar ada pola untuk dianalisis)
        for _ in range(rng.choice([1, 2, 2])):
            m, a, b = rng.choice(MAKAN[:-1] if rng.random() < 0.9 else MAKAN)
            boost = 1.2 if d.day >= 20 else 1.0
            add(d, "pengeluaran", rnd(rng, int(a * boost), int(b * boost)), "makanan_minuman", m, m)
        if rng.random() < 0.5:
            m, a, b = rng.choice(TRANS)
            add(d, "pengeluaran", rnd(rng, a, b), "transportasi", m, m)
        if d.weekday() >= 5 and rng.random() < 0.2:
            m, a, b = rng.choice(HIBUR)
            add(d, "pengeluaran", rnd(rng, a, b, 1000), "hiburan", m, m)
        if rng.random() < 0.05:
            m, a, b = rng.choice(BELANJA)
            add(d, "pengeluaran", rnd(rng, a, b, 1000), "belanja", f"Belanja {m}", m)
        if rng.random() < 0.03:
            add(d, "pengeluaran", rnd(rng, 35_000, 120_000, 1000), "kesehatan", "Apotek K24", "Apotek K24")
        if d.day == 8 and d.month % 2 == 0:
            add(d, "pengeluaran", 150_000, "pendidikan", "Buku referensi kuliah", "Gramedia")
        d += timedelta(days=1)

    now = db.iso_now()
    for kind, amount, cat, desc, merchant, dt in rows:
        db.execute("INSERT INTO transactions(kind, amount, category, description, merchant, date, source, created_at, "
                   "updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (kind, amount, cat, desc, merchant, dt, "demo", now, now))
    print(f"{len(rows)} transaksi demo ({start.isoformat()} s/d {today.isoformat()})")

    for cat, lim in (("_total", 4_000_000), ("makanan_minuman", 1_600_000), ("transportasi", 450_000), ("hiburan", 300_000)):
        db.execute("INSERT OR IGNORE INTO budgets(category, monthly_limit, alert_pct, created_at, updated_at) "
                   "VALUES (?,?,?,?,?)", (cat, lim, 80, now, now))
    nxt_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    bills = [("Kos Pak Darto", 1_250_000, "tagihan", nxt_month.isoformat(), 1),
             ("Internet IndiHome", 350_000, "tagihan", (nxt_month.replace(day=5)).isoformat(), 0),
             ("UKT semester genap", 4_500_000, "pendidikan", (today + timedelta(days=2)).isoformat(), 0)]
    for name, amt, cat, due, auto in bills:
        if not db.query_one("SELECT 1 FROM bills WHERE name=? AND active=1", (name,)):
            db.execute("INSERT INTO bills(name, amount, category, merchant, frequency, next_due, auto_record, origin, "
                       "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (name, amt, cat, name, "tahunan" if "UKT" in name else "bulanan", due, auto, "demo", now, now))
    print("anggaran demo: total 4 jt, makan 1,6 jt, transport 450 rb, hiburan 300 rb; 3 tagihan rutin")


def hapus() -> None:
    db.init_db()
    n = db.execute("DELETE FROM transactions WHERE source='demo'").rowcount
    b = db.execute("DELETE FROM bills WHERE origin='demo'").rowcount
    print(f"dihapus: {n} transaksi demo, {b} tagihan demo (anggaran tidak dihapus; hapus lewat web app bila perlu)")


if __name__ == "__main__":
    hapus() if "--hapus" in sys.argv else seed()
