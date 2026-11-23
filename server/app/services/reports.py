"""Layanan baru: laporan keuangan PDF (grafik + narasi model lokal).

Alur: `compute_facts` menghitung semua angka dari database (deterministik) -> agent membaca fakta itu lewat tool lalu
menulis narasi -> `check_narrative` memastikan setiap nominal Rp di narasi memang ada di fakta (penjaga halusinasi)
-> `build_pdf` menggambar grafik (matplotlib) dan menyusun PDF (reportlab). Bila agent gagal menulis narasi yang
lolos pemeriksaan, laporan tetap dibuat dengan narasi templat (`narrative_source = template`).
"""
import re
from datetime import date, timedelta
from pathlib import Path

from .. import db
from ..config import get_settings
from . import planning

KATEGORI_LABEL = {
    "makanan_minuman": "Makanan & minuman", "transportasi": "Transportasi", "belanja": "Belanja", "tagihan": "Tagihan",
    "kesehatan": "Kesehatan", "pendidikan": "Pendidikan", "hiburan": "Hiburan", "transfer_keluar": "Transfer keluar",
    "lainnya_keluar": "Lainnya (keluar)", "gaji": "Gaji", "transfer_masuk": "Transfer masuk",
    "lainnya_masuk": "Lainnya (masuk)", "_total": "Total pengeluaran",
}


BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
         "November", "Desember"]


def _rp(v) -> str:
    v = int(round(v))
    return ("-" if v < 0 else "") + f"Rp{abs(v):,}".replace(",", ".")


def _pct(v) -> str:
    return f"{v}".replace(".", ",") + "%"


def _bulan(d: date) -> str:
    return f"{BULAN[d.month - 1]} {d.year}"


def resolve_period(periode: str | None, mulai: str | None = None, akhir: str | None = None) -> tuple[date, date, str, str]:
    """-> (awal, akhir, label, jenis). periode: minggu_lalu | bulan_lalu | minggu_ini | bulan_ini | YYYY-MM."""
    t = planning.today()
    p = (periode or "bulan_ini").strip().lower()
    if mulai and akhir:
        a, b = date.fromisoformat(mulai), date.fromisoformat(akhir)
        return a, b, f"{a.isoformat()} s/d {b.isoformat()}", "manual"
    if re.fullmatch(r"\d{4}-\d{2}", p):
        a = date.fromisoformat(p + "-01")
        b = planning.month_bounds(a)[1]
        return a, min(b, t), _bulan(a), "bulanan"
    if p == "minggu_lalu":
        a = t - timedelta(days=t.weekday() + 7)
        return a, a + timedelta(days=6), f"Minggu {a.isoformat()} s/d {(a + timedelta(days=6)).isoformat()}", "mingguan"
    if p == "minggu_ini":
        a = t - timedelta(days=t.weekday())
        return a, t, f"Minggu ini ({a.isoformat()} s/d {t.isoformat()})", "mingguan"
    if p == "bulan_lalu":
        b = t.replace(day=1) - timedelta(days=1)
        a = b.replace(day=1)
        return a, b, _bulan(a), "bulanan"
    if p == "bulan_ini":
        a = t.replace(day=1)
        return a, t, f"{_bulan(a)} (s/d {t.isoformat()})", "bulanan"
    raise ValueError("periode laporan: minggu_lalu, minggu_ini, bulan_lalu, bulan_ini, atau YYYY-MM")


def _totals(a: date, b: date) -> dict:
    rows = db.query("SELECT kind, category, COUNT(*) n, SUM(amount) s FROM transactions WHERE deleted=0 AND date>=? "
                    "AND date<=? GROUP BY kind, category ORDER BY s DESC", (a.isoformat(), b.isoformat()))
    inc = sum(r["s"] for r in rows if r["kind"] == "pemasukan")
    exp = sum(r["s"] for r in rows if r["kind"] == "pengeluaran")
    return {"pemasukan": inc, "pengeluaran": exp, "saldo": inc - exp, "jumlah_transaksi": sum(r["n"] for r in rows),
            "per_kategori": [{"kategori": r["category"], "label": KATEGORI_LABEL.get(r["category"], r["category"]),
                              "jenis": r["kind"], "total": r["s"], "jumlah": r["n"],
                              "porsi_persen": round(r["s"] / exp * 100, 1) if exp and r["kind"] == "pengeluaran" else None}
                             for r in rows]}


def compute_facts(periode: str | None = None, mulai: str | None = None, akhir: str | None = None) -> dict:
    a, b, label, kind = resolve_period(periode, mulai, akhir)
    span = (b - a).days + 1
    if kind == "bulanan" and a.day == 1:  # bandingkan dengan bulan kalender sebelumnya (s/d tanggal yang sama)
        pb_full = a - timedelta(days=1)
        pa = pb_full.replace(day=1)
        pb = min(pb_full, pa + timedelta(days=span - 1))
    else:
        pa, pb = a - timedelta(days=span), a - timedelta(days=1)
    cur, prev = _totals(a, b), _totals(pa, pb)

    def chg(x, y):
        return round((x - y) / y * 100, 1) if y else None

    daily = db.query("SELECT date, SUM(amount) s FROM transactions WHERE deleted=0 AND kind='pengeluaran' AND date>=? "
                     "AND date<=? GROUP BY date ORDER BY date", (a.isoformat(), b.isoformat()))
    top_m = db.query("SELECT COALESCE(NULLIF(merchant,''), description, category) m, COUNT(*) n, SUM(amount) s FROM "
                     "transactions WHERE deleted=0 AND kind='pengeluaran' AND date>=? AND date<=? GROUP BY m ORDER BY s "
                     "DESC LIMIT 5", (a.isoformat(), b.isoformat()))
    big = db.query("SELECT id, date, amount, category, description, merchant FROM transactions WHERE deleted=0 AND "
                   "kind='pengeluaran' AND date>=? AND date<=? ORDER BY amount DESC LIMIT 5", (a.isoformat(), b.isoformat()))
    t = planning.today()
    budgets = planning.budget_status(on=min(b, t))["anggaran"] if kind == "bulanan" or (a.month == b.month) else []
    upcoming = [x for x in planning.list_bills()["tagihan"] if 0 <= (x["hari_lagi"] or 0) <= 14]
    overdue = [x for x in planning.list_bills()["tagihan"] if (x["hari_lagi"] or 0) < 0]
    days_with = len(daily)
    return {
        "periode": {"label": label, "jenis": kind, "mulai": a.isoformat(), "akhir": b.isoformat(), "jumlah_hari": span},
        "total": cur,
        "periode_sebelumnya": {"label": _bulan(pa) if kind == "bulanan" and a.day == 1 else
                               f"{pa.isoformat()} s/d {pb.isoformat()}", "mulai": pa.isoformat(), "akhir": pb.isoformat(), "pemasukan": prev["pemasukan"],
                               "pengeluaran": prev["pengeluaran"], "saldo": prev["saldo"]},
        "perubahan_persen": {"pemasukan": chg(cur["pemasukan"], prev["pemasukan"]),
                             "pengeluaran": chg(cur["pengeluaran"], prev["pengeluaran"])},
        "rata_rata_pengeluaran_per_hari": round(cur["pengeluaran"] / span) if span else 0,
        "hari_dengan_pengeluaran": days_with,
        "pengeluaran_harian": [{"tanggal": d["date"], "total": d["s"]} for d in daily],
        "merchant_teratas": [{"nama": m["m"], "jumlah": m["n"], "total": m["s"]} for m in top_m],
        "transaksi_terbesar": big,
        "anggaran": budgets,
        "anggaran_bulan": _bulan(min(b, t)),
        "tagihan_14_hari": [{"id": x["id"], "nama": x["name"], "jumlah": x["amount"], "jatuh_tempo": x["next_due"]}
                            for x in upcoming],
        "tagihan_terlambat": [{"id": x["id"], "nama": x["name"], "jumlah": x["amount"], "jatuh_tempo": x["next_due"]}
                              for x in overdue],
    }


def _numbers(obj, acc: set[int]) -> set[int]:
    if isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        acc.add(int(round(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numbers(v, acc)
    return acc


RP_RE = re.compile(r"Rp\s?-?([\d.]+(?:,\d+)?)\s*(juta|jt|ribu|rb)?", re.I)


def check_narrative(text: str, facts: dict) -> list[str]:
    """Setiap nominal Rp di narasi harus ada di fakta (toleransi pembulatan 1%, atau selisih/jumlah dua angka fakta).
    Mengembalikan daftar nominal yang tidak bisa dibuktikan."""
    known = sorted(n for n in _numbers(facts, set()) if n >= 100)
    derived = set(known)
    # selisih/jumlah hanya antar angka utama (total & periode sebelumnya), bukan semua pasangan
    t, pv = facts.get("total", {}), facts.get("periode_sebelumnya", {})
    main = [t.get("pemasukan", 0), t.get("pengeluaran", 0), t.get("saldo", 0), pv.get("pemasukan", 0),
            pv.get("pengeluaran", 0), pv.get("saldo", 0)]
    main += [b.get("batas", 0) for b in facts.get("anggaran", [])] + [b.get("terpakai", 0) for b in facts.get("anggaran", [])]
    for x in main:
        for y in main:
            derived.add(abs(x - y))
    bad = []
    for m in RP_RE.finditer(text or ""):
        raw, suf = m.group(1), (m.group(2) or "").lower()
        num = raw.rstrip(".")
        if suf in ("juta", "jt", "ribu", "rb"):
            # "Rp4,5 juta" atau "Rp4.5 juta" (titik desimal gaya Inggris) -> 4,5; "Rp1.250 ribu" -> 1250
            dec = re.fullmatch(r"(\d+)[.,](\d{1,2})", num)
            base = float(f"{dec.group(1)}.{dec.group(2)}") if dec else float(num.replace(".", "").replace(",", "."))
            v = base * (1_000_000 if suf in ("juta", "jt") else 1000)
        else:
            v = float(num.replace(".", "").replace(",", "."))
        tol = max(1.0, v * (0.051 if suf else 0.003))  # "Rp1,2 juta" adalah pembulatan
        if not any(abs(abs(v) - abs(k)) <= tol for k in derived):
            bad.append(m.group(0).strip())
    return bad


def template_narrative(f: dict) -> str:
    t, p = f["total"], f["perubahan_persen"]
    s = [f"Selama {f['periode']['label']}, pemasukan tercatat {_rp(t['pemasukan'])} dan pengeluaran {_rp(t['pengeluaran'])}, "
         f"sehingga saldo periode ini {_rp(t['saldo'])} dari {t['jumlah_transaksi']} transaksi."]
    if p["pengeluaran"] is not None:
        arah = "naik" if p["pengeluaran"] > 0 else "turun"
        s.append(f"Pengeluaran {arah} {_pct(abs(p['pengeluaran']))} dibanding periode sebelumnya "
                 f"({_rp(f['periode_sebelumnya']['pengeluaran'])}).")
    cats = [c for c in t["per_kategori"] if c["jenis"] == "pengeluaran"]
    if cats:
        c = cats[0]
        s.append(f"Pos terbesar adalah {KATEGORI_LABEL.get(c['kategori'], c['kategori'])} sebesar {_rp(c['total'])} "
                 f"({_pct(c['porsi_persen'])} dari pengeluaran).")
    over = [b for b in f["anggaran"] if b["status"] in ("waspada", "terlampaui", "diproyeksikan_lewat")]
    for b in over[:3]:
        s.append(f"Anggaran {KATEGORI_LABEL.get(b['kategori'], b['kategori'])} berstatus {b['status'].replace('_', ' ')} "
                 f"({_pct(b['persen'])} dari {_rp(b['batas'])}).")
    if f["tagihan_14_hari"]:
        s.append("Tagihan dalam 14 hari ke depan: " + ", ".join(f"{x['nama']} {_rp(x['jumlah'])} ({x['jatuh_tempo']})"
                                                              for x in f["tagihan_14_hari"][:4]) + ".")
    return " ".join(s)


# ------------------------------------------------------------------ grafik + PDF

def _charts(f: dict, work: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = []
    palette = ["#4F46E5", "#0EA5E9", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899", "#14B8A6", "#64748B"]
    cats = [c for c in f["total"]["per_kategori"] if c["jenis"] == "pengeluaran"]
    if cats:
        fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
        labels = [KATEGORI_LABEL.get(c["kategori"], c["kategori"]) for c in cats][::-1]
        vals = [c["total"] / 1000 for c in cats][::-1]
        ax.barh(labels, vals, color=palette[0])
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v:,.0f}".replace(",", "."), va="center", fontsize=8)
        ax.set_xlabel("ribu Rp"); ax.set_title("Pengeluaran per kategori", fontsize=11, loc="left")
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); p = work / "kategori.png"; fig.savefig(p); plt.close(fig); out.append(p)
    if f["pengeluaran_harian"]:
        a, b = date.fromisoformat(f["periode"]["mulai"]), date.fromisoformat(f["periode"]["akhir"])
        m = {d["tanggal"]: d["total"] for d in f["pengeluaran_harian"]}
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        fig, ax = plt.subplots(figsize=(6.4, 2.6), dpi=150)
        ax.bar([d.day if len(days) > 7 else d.strftime("%a %d") for d in days], [m.get(d.isoformat(), 0) / 1000 for d in days],
               color=palette[1])
        avg = f["rata_rata_pengeluaran_per_hari"] / 1000
        ax.axhline(avg, color=palette[4], lw=1, ls="--", label=f"rata-rata {avg:,.0f} rb/hari".replace(",", "."))
        ax.legend(fontsize=7, frameon=False); ax.set_ylabel("ribu Rp")
        ax.set_title("Pengeluaran harian", fontsize=11, loc="left"); ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="x", labelsize=6 if len(days) > 7 else 8)
        fig.tight_layout(); p = work / "harian.png"; fig.savefig(p); plt.close(fig); out.append(p)
    if f["anggaran"]:
        fig, ax = plt.subplots(figsize=(6.4, 0.5 + 0.42 * len(f["anggaran"])), dpi=150)
        labels = [KATEGORI_LABEL.get(x["kategori"], x["kategori"]) for x in f["anggaran"]][::-1]
        pct = [min(x["persen"], 150) for x in f["anggaran"]][::-1]
        cols = [palette[4] if p >= 100 else palette[3] if p >= x["ambang_persen"] else palette[2]
                for p, x in zip(pct, f["anggaran"][::-1])]
        ax.barh(labels, [100] * len(pct), color="#E5E7EB"); ax.barh(labels, pct, color=cols)
        for i, (p, x) in enumerate(zip(pct, f["anggaran"][::-1])):
            ax.text(min(p, 100) + 1, i, f"{x['persen']}%", va="center", fontsize=8)
        ax.set_xlim(0, 160); ax.set_xlabel("% anggaran terpakai")
        ax.set_title(f"Pemakaian anggaran ({f['anggaran_bulan']})", fontsize=11, loc="left"); ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); p = work / "anggaran.png"; fig.savefig(p); plt.close(fig); out.append(p)
    return out


def build_pdf(f: dict, narrative: str, path: Path, work: Path, narrative_source: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=18, alignment=0, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=9, textColor=colors.HexColor("#6B7280"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("b", parent=ss["Normal"], fontSize=10, leading=14)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#6B7280"))
    t, p = f["total"], f["perubahan_persen"]

    def pc(v):
        return "-" if v is None else f"{'+' if v > 0 else ''}{_pct(v)}"

    def table(rows, widths, head=True):
        tb = Table(rows, colWidths=widths, hAlign="LEFT")
        st = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4),
              ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E5E7EB")), ("ALIGN", (-1, 0), (-1, -1), "RIGHT")]
        if head:
            st += [("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F3F4F6"))]
        tb.setStyle(TableStyle(st))
        return tb

    story = [Paragraph("Laporan Keuangan", h1),
             Paragraph(f"{f['periode']['label']} &middot; {f['periode']['mulai']} s/d {f['periode']['akhir']} &middot; dibuat "
                       f"{db.now_local().strftime('%Y-%m-%d %H:%M')}", sub), Spacer(1, 10)]
    kpi = [["Pemasukan", "Pengeluaran", "Saldo", "Transaksi"],
           [_rp(t["pemasukan"]), _rp(t["pengeluaran"]), _rp(t["saldo"]), str(t["jumlah_transaksi"])],
           [f"vs sebelumnya {pc(p['pemasukan'])}", f"vs sebelumnya {pc(p['pengeluaran'])}",
            f"rata-rata {_rp(f['rata_rata_pengeluaran_per_hari'])}/hari", ""]]
    kt = Table(kpi, colWidths=[4.3 * cm] * 4, hAlign="LEFT")
    kt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, 0), 8), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6B7280")),
                            ("FONTSIZE", (0, 1), (-1, 1), 13), ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 2), (-1, 2), 7.5), ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#6B7280")),
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAFAFA"))]))
    story += [kt, Paragraph("Ringkasan", h2)]
    for para in [x.strip() for x in narrative.split("\n") if x.strip()]:
        story.append(Paragraph(para.replace("&", "&amp;").replace("<", "&lt;"), body))
    story.append(Paragraph("Narasi ditulis model AI lokal dan setiap nominalnya sudah dicocokkan dengan database."
                           if narrative_source == "agent" else
                           "Narasi dibuat dari templat (model tidak menghasilkan narasi yang lolos pemeriksaan angka).", small))
    for img in _charts(f, work):
        from PIL import Image as PILImage
        with PILImage.open(img) as im:
            w, h = im.size
        story += [Spacer(1, 8), Image(str(img), width=16 * cm, height=16 * cm * h / w)]
    cats = [c for c in t["per_kategori"] if c["jenis"] == "pengeluaran"]
    if cats:
        story += [KeepTogether([Paragraph("Rincian pengeluaran", h2),
                  table([["Kategori", "Transaksi", "Porsi", "Total"]] +
                        [[KATEGORI_LABEL.get(c["kategori"], c["kategori"]), str(c["jumlah"]), f"{c['porsi_persen']}%",
                          _rp(c["total"])] for c in cats], [7 * cm, 2.5 * cm, 2.5 * cm, 5 * cm])])]
    if f["transaksi_terbesar"]:
        story += [KeepTogether([Paragraph("Transaksi terbesar", h2),
                  table([["Tanggal", "Keterangan", "Kategori", "Nominal"]] +
                        [[x["date"], (x.get("merchant") or x.get("description") or "-")[:40],
                          KATEGORI_LABEL.get(x["category"], x["category"]), _rp(x["amount"])] for x in f["transaksi_terbesar"]],
                        [2.6 * cm, 7 * cm, 3.6 * cm, 3.8 * cm])])]
    bills = f["tagihan_terlambat"] + f["tagihan_14_hari"]
    if bills:
        story += [KeepTogether([Paragraph("Tagihan yang perlu diperhatikan", h2),
                  table([["Tagihan", "Jatuh tempo", "Status", "Nominal"]] +
                        [[x["nama"], x["jatuh_tempo"], "terlambat" if x in f["tagihan_terlambat"] else "segera",
                          _rp(x["jumlah"])] for x in bills], [7 * cm, 3 * cm, 3 * cm, 4 * cm])])]
    story += [Spacer(1, 14), Paragraph("Dibuat otomatis oleh agen AI lokal (Qwen 3.5 9B lewat Ollama). Semua angka dihitung dari "
                                       "database SQLite di laptop server; tidak ada data yang dikirim ke layanan cloud.", small)]
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.6 * cm,
                            bottomMargin=1.6 * cm, title=f"Laporan Keuangan {f['periode']['label']}")
    doc.build(story)


def create_report(periode: str | None, narrative: str | None, run_id: str | None = None, kind: str | None = None,
                  mulai: str | None = None, akhir: str | None = None, narrative_source: str = "agent") -> dict:
    import shutil
    import tempfile

    f = compute_facts(periode, mulai, akhir)
    text = (narrative or "").strip()
    if not text:
        text, narrative_source = template_narrative(f), "template"
    rid = db.new_id("rep")
    d = Path(get_settings().reports_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"laporan_{f['periode']['mulai']}_{f['periode']['akhir']}_{rid[-6:]}.pdf"
    work = Path(tempfile.mkdtemp(prefix="laporan_"))
    try:
        build_pdf(f, text, path, work, narrative_source)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    db.execute("INSERT INTO reports(id, kind, period_label, date_from, date_to, pdf_path, facts_json, narrative, "
               "narrative_source, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (rid, kind or f["periode"]["jenis"], f["periode"]["label"], f["periode"]["mulai"], f["periode"]["akhir"],
                str(path), db.dumps(f), text, narrative_source, run_id, db.iso_now()))
    planning.notify("laporan", f"Laporan {f['periode']['label']} siap",
                    f"Pengeluaran {_rp(f['total']['pengeluaran'])}, pemasukan {_rp(f['total']['pemasukan'])}.", "info",
                    {"report_id": rid, "url": f"/v1/reports/{rid}/pdf"}, dedup_key=f"report:{rid}")
    return {"id": rid, "url": f"/v1/reports/{rid}/pdf", "periode": f["periode"], "narasi_sumber": narrative_source,
            "halaman_pdf": path.name}


def report_row(r: dict) -> dict:
    r = dict(r)
    r.pop("pdf_path", None)
    facts = db.loads(r.pop("facts_json", None)) or {}
    r["total"] = {k: (facts.get("total") or {}).get(k) for k in ("pemasukan", "pengeluaran", "saldo", "jumlah_transaksi")}
    r["url"] = f"/v1/reports/{r['id']}/pdf"
    return r
