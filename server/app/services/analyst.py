"""Sub-agen analis data: model lokal menulis kode Python (pandas/matplotlib), kode dijalankan di sandbox, dan bila
gagal, pesan error dikembalikan ke model untuk diperbaiki (loop tulis -> jalankan -> perbaiki).

Dipakai oleh tool `analisis_data` milik agent utama. Ini membuka pertanyaan yang tidak bisa dijawab tool tetap
(ringkasan/cari): tren antarbulan, perbandingan, rata-rata per hari, prediksi sederhana, dan grafik.

Pengaman berlapis:
1. Pemeriksaan AST sebelum dijalankan: hanya modul analisis yang boleh diimpor; nama/atribut berbahaya ditolak.
2. Proses anak terpisah (`sandbox_runner.py`, `python -I`) dengan rlimit memori/CPU/ukuran file, env minimal, direktori
   kerja sementara, jaringan diputus, builtins dibatasi, dan batas waktu.
3. Data yang diberikan hanya salinan CSV transaksi; kode tidak pernah menyentuh database asli.
"""
import ast
import asyncio
import csv
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

from .. import db
from ..config import get_settings
from ..util import redact

RUNNER = Path(__file__).with_name("sandbox_runner.py")
ALLOWED_IMPORTS = {"pandas", "numpy", "matplotlib", "math", "statistics", "datetime", "calendar", "collections",
                   "itertools", "json", "re"}
BANNED_NAMES = {"open", "exec", "eval", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr",
                "__import__", "input", "breakpoint", "help", "exit", "quit", "memoryview"}
BANNED_ATTRS = {"system", "popen", "remove", "unlink", "rmdir", "rmtree", "to_pickle", "read_pickle", "to_sql",
                "read_sql", "read_csv", "read_excel", "read_json", "read_html", "to_csv", "to_excel", "to_json",
                "to_html", "savefig", "subprocess", "environ", "spawn", "fork", "kill", "load", "fromfile", "tofile",
                "eval", "read_parquet", "to_parquet", "read_table", "read_fwf"}

SYSTEM = """Kamu adalah sub-agen analis data keuangan. Tulis SATU blok kode Python untuk menjawab pertanyaan.

Lingkungan yang sudah tersedia (jangan membaca file, jangan impor modul lain):
- `df`: pandas DataFrame semua transaksi, kolom:
  id (int), tanggal (datetime64), bulan (str 'YYYY-MM'), jenis ('pemasukan'|'pengeluaran'), jumlah (int, rupiah),
  kategori (str), deskripsi (str), merchant (str), sumber (str)
- `pd`, `np`, `plt` (matplotlib.pyplot) sudah diimpor.
- `keluar` (hanya pengeluaran), `masuk` (hanya pemasukan), `bulan_ini` ('{month}'), `hari_ini` (pd.Timestamp).
- `rupiah(x)` -> string 'Rp1.234.567'.
- FUNGSI BANTU (utamakan ini, sudah teruji):
  total_per(data, kolom) -> Series total per 'bulan'/'kategori'/'merchant', urut terbesar
  per_bulan(data) -> Series total per bulan, urut kronologis
  per_hari_minggu(data, cara='total'|'rata') -> Series per hari Senin..Minggu
  proyeksi_akhir_bulan(kategori=None) -> dict terpakai, rata_harian, proyeksi, hari_ke, jumlah_hari
  grafik_batang(series, judul) / grafik_garis(series, judul) -> gambar + simpan grafik
  simpan_grafik(judul) -> simpan grafik matplotlib buatan sendiri (bila tidak memakai grafik_*)
- Contoh:
    p = proyeksi_akhir_bulan()
    print(f"Proyeksi akhir bulan {rupiah(p['proyeksi'])} (terpakai {rupiah(p['terpakai'])})")
    h = per_hari_minggu(keluar[keluar.bulan == bulan_ini])
    print(f"Hari paling boros: {h.idxmax()} {rupiah(h.max())}")
    grafik_batang(h, 'Pengeluaran per hari dalam seminggu')
    hasil = {"proyeksi": p["proyeksi"], "hari_paling_boros": h.idxmax()}

Aturan:
- WAJIB memakai `keluar`/`masuk` (bukan `df` mentah) untuk menghitung pengeluaran/pemasukan, dan memakai fungsi
  bantu di atas bila cocok. Jangan menulis ulang tanggal hari ini; pakai `hari_ini` dan `bulan_ini`.
- Hari ini {today}. Gunakan hanya data di `df`; bila data kosong untuk periode yang ditanya, cetak itu apa adanya.
- `print()` jawaban ringkas dalam bahasa Indonesia BESERTA angka kunci (pakai rupiah()).
- Simpan angka penting di variabel `hasil` (dict sederhana: str/int/float/list).
- Buat grafik hanya bila diminta atau jelas membantu (tren, perbandingan, komposisi), maksimal 2 grafik,
  judul dan label sumbu berbahasa Indonesia. Nominal sumbu-y dalam rupiah (boleh dibagi 1000 dengan label 'ribu Rp').
- Untuk prediksi, gunakan metode sederhana yang dijelaskan (rata-rata harian, tren linear np.polyfit) dan sebutkan
  bahwa itu perkiraan.
- Tips: bulan ini = df[df.bulan == '{month}']; hari dalam minggu = df.tanggal.dt.day_of_week (0=Senin ... 6=Minggu),
  nama hari pakai list ['Senin','Selasa','Rabu','Kamis','Jumat','Sabtu','Minggu']. Saat menggambar, pastikan sumbu-x
  dan sumbu-y berasal dari Series yang SAMA (x = s.index, y = s.values).
- Jangan memakai open(), eval(), exec(), getattr(), os, sys, subprocess, requests, atau membaca/menulis file.

- Kode RINGKAS: maksimal sekitar 40 baris, tanpa fungsi bertingkat, tanpa komentar panjang, tanpa pengecekan berlebihan.
  Pertanyaan majemuk: jawab tiap bagian dengan beberapa baris sederhana.

Balas HANYA dengan blok ```python ... ``` tanpa penjelasan lain."""


class AnalysisError(Exception):
    pass


def _today() -> str:
    return db.now_local().date().isoformat()


def export_csv(path: Path) -> dict:
    rows = db.query("SELECT id, date, kind, amount, category, description, merchant, source FROM transactions "
                    "WHERE deleted=0 ORDER BY date, id")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "tanggal", "jenis", "jumlah", "kategori", "deskripsi", "merchant", "sumber"])
        for r in rows:
            w.writerow([r["id"], r["date"], r["kind"], r["amount"], r["category"], r["description"] or "",
                        r["merchant"] or "", r["source"]])
    months = sorted({r["date"][:7] for r in rows})
    return {"jumlah_baris": len(rows), "tanggal_awal": rows[0]["date"] if rows else None,
            "tanggal_akhir": rows[-1]["date"] if rows else None, "bulan": months,
            "kategori": sorted({r["category"] for r in rows})}


def validate_code(code: str) -> list[str]:
    """Pemeriksaan statis. Kembalikan daftar pelanggaran (kosong = aman dijalankan)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        src = code.splitlines()
        line = src[e.lineno - 1].strip() if e.lineno and 0 < e.lineno <= len(src) else ""
        cut = " (kode kemungkinan terpotong karena terlalu panjang; tulis versi yang jauh lebih pendek)" \
            if e.lineno and e.lineno >= len(src) - 1 else ""
        return [f"SyntaxError baris {e.lineno}: {e.msg}: {line[:120]}{cut}"]
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    bad.append(f"impor {a.name!r} tidak diizinkan")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS:
                bad.append(f"impor dari {node.module!r} tidak diizinkan")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            bad.append(f"nama {node.id!r} tidak diizinkan")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BANNED_ATTRS:
                bad.append(f"atribut {node.attr!r} tidak diizinkan")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await)):
            bad.append(f"{type(node).__name__} tidak diizinkan")
    return sorted(set(bad))


def logic_warnings(code: str, question: str) -> list[str]:
    """Pemeriksa logika sederhana untuk kesalahan yang sering dibuat model kecil dan TIDAK memicu error:
    menjumlahkan `jumlah` dari df tanpa memisahkan pemasukan/pengeluaran, dan tanggal hari ini yang ditulis manual."""
    out = []
    q = question.lower()
    sums_df = re.search(r"\bdf\b[^\n]*\[?['\"]?jumlah['\"]?\]?\s*\.(sum|mean)\(|\bdf\b[^\n]*\.jumlah\.(sum|mean)\(", code) \
        or re.search(r"\bdata_\w+\s*\[\s*['\"]jumlah['\"]\s*\]\s*\.(sum|mean)\(", code)
    separates = re.search(r"pengeluaran|pemasukan|\bkeluar\b|\bmasuk\b|jenis", code)
    if sums_df and not separates and ("pengeluaran" in q or "boros" in q or "belanja" in q or "pemasukan" in q):
        out.append("Kode menjumlahkan kolom jumlah tanpa memisahkan pemasukan dan pengeluaran. Pakai `keluar` "
                   "(hanya pengeluaran) atau `masuk`, bukan `df` mentah.")
    if re.search(r"^\s*(hari_ini|bulan_ini|today)\s*=", code, re.M):
        out.append("Jangan menulis tanggal hari ini secara manual; pakai variabel `hari_ini` dan `bulan_ini`.")
    return out


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    if m:
        return m.group(1).strip()
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
    return t.strip("`").strip()


async def run_sandbox(code: str, work: Path) -> dict:
    s = get_settings()
    (work / "code.py").write_text(code, encoding="utf-8")
    t0 = time.time()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(RUNNER), str(work), str(s.analysis_mem_mb), str(int(s.analysis_timeout_s)), _today(),
        cwd=str(work), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "HOME": str(work), "MPLBACKEND": "Agg", "LANG": "C.UTF-8",
             "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2"})
    try:
        out, errb = await asyncio.wait_for(proc.communicate(), s.analysis_timeout_s + 5)
        timed_out = False
    except asyncio.TimeoutError:
        proc.kill()
        out, errb = await proc.communicate()
        timed_out = True
    stdout, stderr = out.decode("utf-8", "replace"), errb.decode("utf-8", "replace")
    res = {"exit_code": proc.returncode, "timeout": timed_out, "durasi_ms": int((time.time() - t0) * 1000),
           "stdout": stdout[-4000:], "stderr": _short_tb(stderr, code)}
    rj = work / "result.json"
    if rj.exists() and proc.returncode == 0:
        res.update(json.loads(rj.read_text(encoding="utf-8")))
    res["ok"] = proc.returncode == 0 and not timed_out
    return res


def _short_tb(stderr: str, code: str = "") -> str:
    """Ringkas traceback agar berguna bagi model: baris kode model yang gagal + pesan exception terakhir.
    Frame internal (pandas, sandbox) dan path server dibuang."""
    if not stderr:
        return ""
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    exc = next((ln.strip() for ln in reversed(lines) if not ln.startswith(" ") and ":" in ln
                and not ln.startswith(("Traceback", "The above", "During handling"))), lines[-1].strip())
    nums = [int(m.group(1)) for m in re.finditer(r'File "kode_analisis.py", line (\d+)', stderr)]
    out = []
    if nums and code:
        src = code.splitlines()
        n = nums[-1]
        if 0 < n <= len(src):
            out.append(f"Baris {n} kode kamu: {src[n - 1].strip()}")
    out.append(exc)
    return redact("\n".join(out))[-1500:]


def _save_charts(run_id: str | None, work: Path, charts: list[dict]) -> list[dict]:
    s = get_settings()
    dest = Path(s.artifacts_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for c in charts or []:
        src = work / c["file"]
        if not src.exists():
            continue
        aid = db.new_id("art")
        path = dest / f"{aid}.png"
        shutil.copyfile(src, path)
        db.execute("INSERT INTO artifacts(id, run_id, kind, title, path, created_at) VALUES (?,?,?,?,?,?)",
                   (aid, run_id, "chart", c.get("judul"), str(path), db.iso_now()))
        out.append({"id": aid, "judul": c.get("judul"), "url": f"/v1/artifacts/{aid}"})
    return out


async def analyze(question: str, tracer, run_id: str | None, want_chart: bool | None = None) -> dict:
    """Jalankan sub-agen analis. `tracer` mencatat setiap percobaan (step llm + step sandbox)."""
    from ..agent import llm_step  # impor lokal: hindari impor melingkar

    s = get_settings()
    work = Path(tempfile.mkdtemp(prefix="analisis_"))
    try:
        profile = export_csv(work / "data.csv")
        sample = (work / "data.csv").read_text(encoding="utf-8").splitlines()[:6]
        user = (f"Pertanyaan pengguna: {question}\n"
                + ("Pengguna meminta grafik.\n" if want_chart else "")
                + f"Profil data: {json.dumps(profile, ensure_ascii=False)}\n"
                + "Contoh baris CSV (sebelum dimuat ke df):\n" + "\n".join(sample))
        messages = [{"role": "system", "content": SYSTEM.replace("{today}", _today()).replace("{month}", _today()[:7])}, {"role": "user", "content": user}]
        attempts = []
        for i in range(1, s.analysis_max_attempts + 1):
            res = await llm_step(tracer, f"analis_tulis_kode_{i}", messages, think=s.analysis_think, temperature=0.2)
            text = res["message"].get("content") or ""
            code = extract_code(text)
            messages.append({"role": "assistant", "content": text})
            violations = validate_code(code)
            if not violations and i < s.analysis_max_attempts:
                violations = logic_warnings(code, question)
            t0 = time.time()
            tracer.started("sandbox", "jalankan_kode", t0)
            if violations:
                run = {"ok": False, "ditolak_validator": violations, "stderr": "; ".join(violations)}
                tracer.add("sandbox", "jalankan_kode", {"percobaan": i, "kode": code},
                           {"ok": False, "ditolak_validator": violations}, status="warning", started_at=t0,
                           duration_ms=int((time.time() - t0) * 1000))
            else:
                run = await run_sandbox(code, work)
                tracer.add("sandbox", "jalankan_kode", {"percobaan": i, "kode": code},
                           {k: run.get(k) for k in ("ok", "exit_code", "timeout", "stdout", "stderr", "hasil", "grafik",
                                                    "durasi_ms")},
                           status="ok" if run["ok"] else "warning", started_at=t0, duration_ms=run.get("durasi_ms"))
            attempts.append({"percobaan": i, "ok": run["ok"], "error": None if run["ok"] else run.get("stderr")})
            if run["ok"] and (run.get("stdout") or "").strip():
                charts = _save_charts(run_id, work, run.get("grafik") or [])
                return {"ok": True, "jawaban_analis": run["stdout"].strip()[-2500:], "hasil": run.get("hasil"),
                        "grafik": charts, "kode": code, "percobaan": i, "riwayat_percobaan": attempts,
                        "data": {"jumlah_baris": profile["jumlah_baris"], "bulan": profile["bulan"]},
                        "catatan": "Angka di atas dihitung oleh kode dari database, bukan perkiraan model. "
                                   "Grafik sudah tampil otomatis di aplikasi; sebutkan saja bahwa grafik tersedia."}
            if run["ok"]:
                feedback = "Kode berjalan tetapi tidak mencetak apa pun. Tambahkan print() jawaban beserta angkanya."
            elif run.get("ditolak_validator"):
                feedback = ("Kode ditolak pemeriksa: " + "; ".join(run["ditolak_validator"])
                            + ". Tulis ulang kode lengkap (df/keluar/masuk sudah tersedia, jangan membaca/menulis file, "
                            "utamakan fungsi bantu).")
            elif run.get("timeout"):
                feedback = "Kode melebihi batas waktu. Sederhanakan (hindari loop besar)."
            else:
                feedback = (f"Kode error:\n{run.get('stderr')}\nPerbaiki penyebabnya (periksa panjang/indeks data, kolom, "
                            "dan tipe) lalu kirim ulang kode LENGKAP. Utamakan kode yang sederhana.")
            messages.append({"role": "user", "content": feedback})
        raise AnalysisError(f"analisis gagal setelah {s.analysis_max_attempts} percobaan karena kode yang ditulis error "
                            f"(data TERSEDIA: {profile['jumlah_baris']} transaksi, jadi bukan karena data kosong). "
                            "Pecah pertanyaan menjadi bagian lebih sederhana dan panggil analisis_data lagi, atau pakai "
                            "ringkasan/cek_anggaran. Error terakhir: " + (attempts[-1].get("error") or "tanpa keluaran")[:300])
    finally:
        shutil.rmtree(work, ignore_errors=True)
