"""Uji layanan baru (perluasan) terhadap server yang SUDAH berjalan.

    uv run python scripts/smoke_services.py            # cek cepat tanpa LLM (endpoint, sandbox, penjaga angka, kurs)
    uv run python scripts/smoke_services.py --llm      # + skenario agent dengan model lokal (beberapa menit)

Prasyarat data: `uv run python scripts/seed_demo.py` (data demo ±4 bulan). Skenario --llm MENULIS data
(transaksi kurs asing, anggaran, tagihan); jalankan `seed_demo.py --hapus` + seed ulang untuk kembali bersih.
Laporan ditulis ke data/smoke_services_report.json.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def read_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("API_KEYS="):
            return line.split("=", 1)[1].split(",")[0].strip()
    raise SystemExit("API key tidak ditemukan di .env")


def offline_checks() -> None:
    """Uji unit tanpa server: validator kode, sandbox, penjaga angka narasi."""
    from app.services import analyst, reports

    print("\n== Sandbox analis & penjaga angka (tanpa server) ==")
    bad = analyst.validate_code("import os\nopen('/etc/passwd')\nx.__class__")
    check("validator menolak os/open/dunder", len(bad) == 3, "; ".join(bad))
    check("pemeriksa logika: df mentah tanpa pisah jenis",
          bool(analyst.logic_warnings("t = df[df.bulan == bulan_ini]['jumlah'].sum()", "total pengeluaran")))
    w = Path(tempfile.mkdtemp())
    analyst.export_csv(w / "data.csv")
    r = asyncio.run(analyst.run_sandbox("import socket\nsocket.socket()", w))
    check("sandbox memblokir modul jaringan", not r["ok"] and "tidak diizinkan" in r["stderr"], r["stderr"][-120:])
    r = asyncio.run(analyst.run_sandbox("p = proyeksi_akhir_bulan()\nprint(rupiah(p['proyeksi']))\n"
                                        "grafik_batang(per_bulan(keluar), 'Per bulan')", w))
    check("sandbox menjalankan fungsi bantu + grafik", r["ok"] and len(r.get("grafik") or []) == 1, r["stdout"].strip())
    f = reports.compute_facts("bulan_lalu")
    exp = f["total"]["pengeluaran"]
    ok_text = f"Pengeluaran Rp{exp:,}".replace(",", ".") + f" atau sekitar Rp{exp / 1e6:.1f} juta".replace(".", ",", 1)
    check("penjaga angka menerima angka dari fakta", reports.check_narrative(ok_text, f) == [], ok_text)
    check("penjaga angka menolak angka karangan", reports.check_narrative("Hemat Rp987.654 bulan ini", f) == ["Rp987.654"])


def endpoint_checks(c: httpx.Client) -> None:
    print("\n== Endpoint layanan baru ==")
    b = c.get("/v1/budgets").json()
    check("GET /v1/budgets", "anggaran" in b, f"{len(b['anggaran'])} anggaran, bulan {b['bulan']}")
    r = c.put("/v1/budgets/kesehatan", json={"batas_bulanan": 250000, "ambang_persen": 75})
    check("PUT /v1/budgets/kesehatan", r.status_code == 200 and r.json()["batas"] == 250000)
    check("PUT anggaran kategori salah ditolak", c.put("/v1/budgets/gaji", json={"batas_bulanan": 1}).status_code == 400)
    check("DELETE /v1/budgets/kesehatan", c.delete("/v1/budgets/kesehatan").status_code == 200)
    r = c.post("/v1/bills", json={"nama": "Uji smoke gym", "jumlah": 150000, "jatuh_tempo": "15"})
    check("POST /v1/bills", r.status_code == 201, r.text[:120])
    bill = r.json()
    check("POST tagihan duplikat ditolak", c.post("/v1/bills", json={"nama": "Uji smoke gym", "jumlah": 1, "jatuh_tempo": "1"}).status_code == 400)
    r = c.post(f"/v1/bills/{bill['id']}/pay", json={})
    paid = r.json()
    check("POST /v1/bills/{id}/pay mencatat transaksi + memajukan jatuh tempo",
          r.status_code == 200 and paid["tagihan"]["next_due"] > bill["next_due"],
          f"{bill['next_due']} -> {paid['tagihan']['next_due']}, tx #{paid['transaksi']['id']}")
    c.delete(f"/v1/transactions/{paid['transaksi']['id']}")
    check("DELETE /v1/bills/{id} menonaktifkan", c.delete(f"/v1/bills/{bill['id']}").json()["active"] is False)
    d = c.get("/v1/subscriptions/detect").json()
    check("GET /v1/subscriptions/detect", "kandidat" in d, ", ".join(k["nama"] for k in d["kandidat"]))
    a = c.get("/v1/automations").json()
    check("GET /v1/automations", len(a["tasks"]) == 4, ", ".join(f"{t['name']}={t['status_terakhir']}" for t in a["tasks"]))
    r = c.post("/v1/automations/harian/run").json()
    check("POST /v1/automations/harian/run", r.get("status") == "ok", json.dumps({k: r.get(k) for k in ("pengingat", "terlambat")}))
    n = c.get("/v1/notifications").json()
    check("GET /v1/notifications", "unread" in n, f"{n['unread']} belum dibaca dari {len(n['notifications'])}")
    t0 = time.time()
    fx = c.get("/v1/fx", params={"dari": "USD", "jumlah": 5})
    check("GET /v1/fx (alat eksternal kurs)", fx.status_code == 200,
          f"{fx.json().get('hasil')} IDR via {fx.json().get('provider')} ({int((time.time() - t0) * 1000)} ms)" if fx.status_code == 200 else fx.text)
    reps = c.get("/v1/reports").json()["reports"]
    if reps:
        pdf = c.get(reps[0]["url"])
        check("GET /v1/reports/{id}/pdf", pdf.status_code == 200 and pdf.content[:4] == b"%PDF", f"{len(pdf.content)} byte")


def chat(c: httpx.Client, text: str, conv: str | None = None) -> dict:
    t0 = time.time()
    r = c.post("/v1/chat", params={"wait": "true", "timeout": 600}, json={"text": text, "conversation_id": conv}).json()
    run = c.get(f"/v1/runs/{r['job']['run_id']}").json()
    run["_ms"] = int((time.time() - t0) * 1000)
    run["_tools"] = [s["name"] for s in run["steps"] if s["type"] == "tool" and s["status"] == "ok"]
    print(f"    {text!r} -> {run['outcome']} {run['_ms']} ms tools={run['_tools']}\n      {(run['final_answer'] or '')[:160]!r}")
    return run


def llm_checks(c: httpx.Client) -> None:
    print("\n== Skenario agent (model lokal) ==")
    r = chat(c, "beli kopi di bandara Changi 6,5 SGD")
    tx = (r.get("transactions") or [{}])[0]
    check("kurs asing: konversi_kurs lalu catat_transaksi", r["_tools"][:2] == ["konversi_kurs", "catat_transaksi"]
          and tx.get("orig_currency") == "SGD", f"Rp{tx.get('amount')} kurs {tx.get('fx_rate')}")
    r = chat(c, "bandingkan pengeluaran makan per bulan, buatkan grafik")
    st = next((s for s in r["steps"] if s["name"] == "analisis_data" and s["status"] == "ok"), None)
    check("analisis: sub-agen menulis kode + grafik", bool(st and st["output"].get("grafik")),
          f"percobaan {st['output']['percobaan']}" if st else "gagal")
    r = chat(c, "atur anggaran hiburan 400rb sebulan")
    check("anggaran: atur_anggaran", "atur_anggaran" in r["_tools"])
    r = chat(c, "ada pengeluaran rutin yang belum aku daftarkan?")
    check("langganan: deteksi_langganan", "deteksi_langganan" in r["_tools"])
    rep = c.post("/v1/reports", params={"wait": "true", "timeout": 600}, json={"periode": "bulan_lalu"}).json()
    run = c.get(f"/v1/runs/{rep['job']['run_id']}").json()
    names = [s["name"] for s in run["steps"] if s["type"] in ("tool", "guard")]
    check("laporan: fakta_laporan -> buat_laporan (PDF)", run["outcome"] == "report", " -> ".join(names))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    offline_checks()
    c = httpx.Client(base_url=a.base, headers={"X-API-Key": read_key()}, timeout=900)
    endpoint_checks(c)
    if a.llm:
        llm_checks(c)
    ok = sum(r["ok"] for r in results)
    print(f"\n{ok}/{len(results)} lolos")
    (ROOT / "data" / "smoke_services_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
