"""Penjadwal otomasi di dalam server: agen bekerja tanpa disuruh.

| Nama              | Jadwal (TIMEZONE)                  | Isi                                                                 |
|-------------------|------------------------------------|---------------------------------------------------------------------|
| harian            | tiap hari jam AUTOMATION_DAILY_HOUR | pengingat tagihan H-n / jatuh tempo / terlambat, catat otomatis      |
|                   |                                    | tagihan `catat_otomatis`, cek anggaran (proyeksi lewat batas)        |
| deteksi_langganan | tiap Senin                         | cari pengeluaran berulang yang belum didaftarkan -> notifikasi       |
| laporan_mingguan  | tiap Senin                         | job agent: fakta_laporan -> narasi model lokal -> buat_laporan (PDF) |
| laporan_bulanan   | tiap tanggal 1                     | sama, periode bulan lalu                                             |

Tugas deterministik (harian, deteksi) langsung dijalankan; tugas laporan dimasukkan ke antrean job yang sama dengan
request pengguna (satu GPU), sehingga trace-nya tampil di web app seperti run biasa (input_type `automation`).
Slot terakhir disimpan di tabel `automations`, jadi tugas yang terlewat saat server mati dikejar saat server hidup lagi,
dan tidak pernah dijalankan dua kali untuk slot yang sama.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .. import db
from ..config import get_settings
from . import planning

log = logging.getLogger("automation")


def _rp(v) -> str:
    v = int(round(v))
    return ("-" if v < 0 else "") + f"Rp{abs(v):,}".replace(",", ".")


TASKS = {
    "harian": {"label": "Cek tagihan & anggaran harian", "jadwal": "Setiap hari jam {h:02d}:00", "llm": False},
    "deteksi_langganan": {"label": "Deteksi langganan baru", "jadwal": "Setiap Senin jam {h:02d}:00", "llm": False},
    "laporan_mingguan": {"label": "Laporan mingguan (PDF)", "jadwal": "Setiap Senin jam {h:02d}:00, periode minggu lalu",
                         "llm": True},
    "laporan_bulanan": {"label": "Laporan bulanan (PDF)", "jadwal": "Setiap tanggal 1 jam {h:02d}:00, periode bulan lalu",
                        "llm": True},
}


def _enabled(name: str) -> bool:
    s = get_settings()
    if not s.automation_enabled:
        return False
    if name == "laporan_mingguan":
        return s.report_weekly_enabled
    if name == "laporan_bulanan":
        return s.report_monthly_enabled
    return True


def _slot(name: str, now: datetime) -> str | None:
    """Slot jadwal yang SUDAH jatuh tempo pada waktu `now` (None = belum waktunya)."""
    h = get_settings().automation_daily_hour
    d = now.date()
    if name == "harian":
        return d.isoformat() if now.hour >= h else None
    if name in ("laporan_mingguan", "deteksi_langganan"):
        monday = d - timedelta(days=d.weekday())
        if d.weekday() == 0 and now.hour < h:
            return None
        y, w, _ = monday.isocalendar()
        return f"{y}-W{w:02d}"
    if name == "laporan_bulanan":
        if d.day == 1 and now.hour < h:
            return None
        return d.strftime("%Y-%m")
    return None


def _next_run(name: str, now: datetime) -> str:
    h = get_settings().automation_daily_hour
    base = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if name == "harian":
        nxt = base if now < base else base + timedelta(days=1)
    elif name in ("laporan_mingguan", "deteksi_langganan"):
        days = (7 - now.weekday()) % 7
        nxt = base + timedelta(days=days)
        if nxt <= now:
            nxt += timedelta(days=7)
    else:
        first = base.replace(day=1)
        nxt = first if now < first else (first + timedelta(days=32)).replace(day=1)
    return nxt.isoformat(timespec="minutes")


def _record(name: str, slot: str | None, status: str, detail: dict) -> None:
    db.execute("INSERT INTO automations(name, last_slot, last_run_at, last_status, last_detail_json) VALUES (?,?,?,?,?) "
               "ON CONFLICT(name) DO UPDATE SET last_slot=COALESCE(excluded.last_slot, automations.last_slot), "
               "last_run_at=excluded.last_run_at, last_status=excluded.last_status, last_detail_json=excluded.last_detail_json",
               (name, slot, db.iso_now(), status, db.dumps(detail)))


def list_automations() -> list[dict]:
    now = db.now_local()
    h = get_settings().automation_daily_hour
    rows = {r["name"]: r for r in db.query("SELECT * FROM automations")}
    out = []
    for name, t in TASKS.items():
        r = rows.get(name) or {}
        out.append({"name": name, "label": t["label"], "jadwal": t["jadwal"].format(h=h), "pakai_llm": t["llm"],
                    "aktif": _enabled(name), "slot_terakhir": r.get("last_slot"), "terakhir_jalan": r.get("last_run_at"),
                    "status_terakhir": r.get("last_status"), "detail_terakhir": db.loads(r.get("last_detail_json")),
                    "jadwal_berikutnya": _next_run(name, now)})
    return out


# ------------------------------------------------------------------ tugas deterministik

def task_harian() -> dict:
    s = get_settings()
    t = planning.today()
    reminders, auto_paid, overdue = [], [], []
    for b in planning.list_bills()["tagihan"]:
        left = b["hari_lagi"]
        if left is None:
            continue
        if left <= 0 and b["auto_record"]:
            res = planning.pay_bill(b["id"], b["next_due"], source="otomasi")
            auto_paid.append({"tagihan": b["name"], "transaksi_id": res["transaksi"]["id"], "jumlah": b["amount"]})
            planning.notify("tagihan", f"{b['name']} dicatat otomatis",
                            f"Pembayaran {b['name']} {_rp(b['amount'])} (jatuh tempo {b['next_due']}) dicatat otomatis. "
                            f"Jatuh tempo berikutnya {res['tagihan']['next_due']}.", "info",
                            {"bill_id": b["id"], "transaksi_id": res["transaksi"]["id"]},
                            dedup_key=f"bill-auto:{b['id']}:{b['next_due']}")
            planning.check_budget_after_write(res["transaksi"])
        elif left < 0:
            overdue.append(b["name"])
            planning.notify("tagihan", f"{b['name']} terlambat {-left} hari",
                            f"{b['name']} {_rp(b['amount'])} jatuh tempo {b['next_due']} dan belum dicatat dibayar.", "danger",
                            {"bill_id": b["id"]}, dedup_key=f"bill-late:{b['id']}:{b['next_due']}:{t.isoformat()}")
        elif left == 0 or left <= s.bill_remind_days:
            when = "hari ini" if left == 0 else f"{left} hari lagi"
            reminders.append(b["name"])
            planning.notify("tagihan", f"{b['name']} jatuh tempo {when}",
                            f"{b['name']} {_rp(b['amount'])} jatuh tempo {b['next_due']}. Balas 'sudah bayar {b['name']}' di "
                            "chat untuk mencatat.", "warning" if left == 0 else "info", {"bill_id": b["id"]},
                            dedup_key=f"bill-due:{b['id']}:{b['next_due']}:{'h0' if left == 0 else 'hn'}")
    budget_alerts = []
    for b in planning.budget_status()["anggaran"]:
        if b["status"] == "aman":
            continue
        label = "total pengeluaran" if b["kategori"] == planning.TOTAL else b["kategori"]
        if b["status"] == "diproyeksikan_lewat":
            msg = (f"Dengan laju sekarang, {label} diperkirakan {_rp(b['proyeksi_akhir_bulan'])} di akhir bulan, melewati "
                   f"anggaran {_rp(b['batas'])}. Sisa aman {_rp(b['sisa_per_hari'])}/hari.")
            level = "warning"
        else:
            msg = f"Anggaran {label}: {b['persen']}% terpakai ({_rp(b['terpakai'])} dari {_rp(b['batas'])})."
            level = "danger" if b["status"] == "terlampaui" else "warning"
        if planning.notify("anggaran", f"Anggaran {label}: {b['status'].replace('_', ' ')}", msg, level, {"anggaran": b},
                           dedup_key=f"budget:{b['kategori']}:{t.strftime('%Y-%m')}:{b['status']}"):
            budget_alerts.append(label)
    return {"pengingat": reminders, "terlambat": overdue, "dicatat_otomatis": auto_paid, "peringatan_anggaran": budget_alerts}


def task_deteksi() -> dict:
    res = planning.detect_recurring()
    new = 0
    for c in res["kandidat"]:
        if c["keyakinan"] < 0.6:
            continue
        if planning.notify("langganan", f"Terdeteksi pengeluaran rutin: {c['nama']}",
                           f"{c['nama']} sekitar {_rp(c['nominal_tipikal'])} ({c['frekuensi']}, {c['kemunculan']}x). "
                           f"Perkiraan berikutnya {c['perkiraan_berikutnya']}. Daftarkan sebagai tagihan agar diingatkan?",
                           "info", {"kandidat": c}, dedup_key=f"recurring:{c['kunci']}"):
            new += 1
    return {"kandidat": res["jumlah"], "notifikasi_baru": new}


def submit_report(kind: str, periode: str | None = None) -> dict:
    from ..jobqueue import job_queue  # impor lokal: hindari impor melingkar
    periode = periode or ("minggu_lalu" if kind == "mingguan" else "bulan_lalu")
    instr = (f"Buat laporan keuangan {kind} untuk periode {periode}. Langkah: panggil fakta_laporan(periode='{periode}'), "
             "lalu tulis narasi 2-4 paragraf pendek (temuan utama, perbandingan dengan periode sebelumnya, kategori terbesar, "
             "status anggaran dan tagihan bila ada, 1-2 saran hemat yang konkret), lalu panggil buat_laporan dengan "
             "periode yang sama dan narasi itu. Semua nominal Rp WAJIB persis dari fakta_laporan. Setelah berhasil, "
             "jawab 1 kalimat.")
    job = job_queue.submit("automation", {"task": "laporan", "report_kind": kind, "periode": periode, "instruksi": instr},
                           None)
    return {"job_id": job["id"], "run_id": job["run_id"], "periode": periode}


async def run_task(name: str, slot: str | None = None, manual: bool = False, periode: str | None = None) -> dict:
    if name not in TASKS:
        raise KeyError(name)
    try:
        if name == "harian":
            detail = await asyncio.to_thread(task_harian)
        elif name == "deteksi_langganan":
            detail = await asyncio.to_thread(task_deteksi)
        elif name == "laporan_mingguan":
            detail = submit_report("mingguan", periode)
        else:
            detail = submit_report("bulanan", periode)
        detail["manual"] = manual
        _record(name, None if manual else slot, "ok", detail)
        return {"name": name, "status": "ok", **detail}
    except Exception as e:  # noqa: BLE001
        log.exception("otomasi %s gagal", name)
        _record(name, None if manual else slot, "error", {"error": repr(e)[:300], "manual": manual})
        return {"name": name, "status": "error", "error": repr(e)[:300]}


class Scheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if get_settings().automation_enabled:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def tick(self) -> list[dict]:
        now = db.now_local()
        done = {r["name"]: r["last_slot"] for r in db.query("SELECT name, last_slot FROM automations")}
        ran = []
        for name in TASKS:
            if not _enabled(name):
                continue
            slot = _slot(name, now)
            if slot and done.get(name) != slot:
                ran.append(await run_task(name, slot))
        return ran

    async def _loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick penjadwal gagal")
            await asyncio.sleep(get_settings().automation_tick_s)


scheduler = Scheduler()
