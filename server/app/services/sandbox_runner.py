"""Proses anak sandbox: menjalankan kode analisis buatan model.

Dipanggil sebagai `python -I sandbox_runner.py <workdir>`. File di workdir:
  code.py   kode buatan model (sudah lolos pemeriksaan AST di analyst.py)
  data.csv  transaksi (salinan baca saja, bukan database asli)
Keluaran: stdout (hasil print), chart_*.png, dan result.json (variabel `hasil` bila ada).

Lapisan pengaman di proses ini (selain pemeriksaan AST): batas memori/CPU/ukuran file (rlimit), jaringan diputus
(socket diganti), builtins berbahaya dihapus, dan proses berjalan di direktori kerja sementara dengan env minimal.
"""
import json
import os
import resource
import sys


def _limits(mem_mb: int, cpu_s: int) -> None:
    resource.setrlimit(resource.RLIMIT_AS, (mem_mb * 1024 * 1024, mem_mb * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024, 20 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def main() -> None:
    work = sys.argv[1]
    mem_mb = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    cpu_s = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    os.chdir(work)
    import warnings
    warnings.filterwarnings("ignore")
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MPLCONFIGDIR"] = work

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    _limits(mem_mb, cpu_s)

    import socket

    def _no_net(*a, **k):
        raise PermissionError("akses jaringan tidak diizinkan di sandbox analisis")
    socket.socket = _no_net  # type: ignore[assignment]
    socket.create_connection = _no_net  # type: ignore[assignment]

    df = pd.read_csv("data.csv", parse_dates=["tanggal"])
    df["bulan"] = df["tanggal"].dt.strftime("%Y-%m")
    charts: list[dict] = []

    plt.rcParams.update({"figure.figsize": (8, 4.2), "figure.dpi": 110, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3})

    def simpan_grafik(judul: str = "Grafik") -> str:
        """Simpan figure matplotlib aktif sebagai PNG (dipanggil oleh kode model)."""
        name = f"chart_{len(charts) + 1}.png"
        fig = plt.gcf()
        if not fig.axes:
            raise ValueError("belum ada grafik yang digambar sebelum simpan_grafik()")
        if not fig.axes[0].get_title():
            fig.axes[0].set_title(judul)
        fig.tight_layout()
        fig.savefig(name)
        plt.close(fig)
        charts.append({"file": name, "judul": str(judul)[:120]})
        return name

    def rupiah(v) -> str:
        v = int(round(float(v)))
        return ("-" if v < 0 else "") + "Rp" + f"{abs(v):,}".replace(",", ".")

    # ---- fungsi bantu domain: model kecil cukup merangkai fungsi ini, tidak perlu menulis logika pandas rumit
    hari_ini = pd.Timestamp(sys.argv[4]) if len(sys.argv) > 4 else pd.Timestamp.today().normalize()
    bulan_ini = hari_ini.strftime("%Y-%m")
    keluar = df[df["jenis"] == "pengeluaran"]
    masuk = df[df["jenis"] == "pemasukan"]
    HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

    def total_per(data, kolom):
        """Total `jumlah` per nilai kolom (mis. 'bulan', 'kategori', 'merchant'), urut terbesar."""
        if isinstance(kolom, (list, tuple)):  # toleran: model kecil kadang ikut menulis 'jumlah' di daftar kolom
            kolom = [k for k in kolom if k != "jumlah"] or "kategori"
            if len(kolom) == 1:
                kolom = kolom[0]
        return data.groupby(kolom)["jumlah"].sum().sort_values(ascending=False)

    def per_bulan(data):
        """Total `jumlah` per bulan, urut kronologis."""
        return data.groupby("bulan")["jumlah"].sum().sort_index()

    def per_hari_minggu(data, cara="total"):
        """Total (atau rata-rata per tanggal bila cara='rata') per hari Senin..Minggu, index = nama hari."""
        harian = data.groupby(data["tanggal"].dt.normalize())["jumlah"].sum()
        g = harian.groupby(harian.index.day_of_week)
        s = g.mean() if cara == "rata" else g.sum()
        s = s.reindex(range(7), fill_value=0)
        s.index = HARI
        return s

    def proyeksi_akhir_bulan(kategori=None):
        """Proyeksi pengeluaran bulan ini sampai akhir bulan (rata-rata harian x jumlah hari)."""
        d = keluar[keluar["bulan"] == bulan_ini]
        if kategori:
            d = d[d["kategori"] == kategori]
        n_hari = hari_ini.days_in_month
        terpakai = int(d["jumlah"].sum())
        rata = terpakai / max(hari_ini.day, 1)
        return {"terpakai": terpakai, "hari_ke": int(hari_ini.day), "jumlah_hari": int(n_hari),
                "rata_harian": int(round(rata)), "proyeksi": int(round(rata * n_hari))}

    def grafik_batang(series, judul, ylabel="ribu Rp"):
        """Gambar Series sebagai grafik batang (nilai dibagi 1000) lalu simpan."""
        plt.figure()
        ax = plt.gca()
        ax.bar([str(i) for i in series.index], series.values / 1000, color="#4F46E5")
        ax.set_ylabel(ylabel)
        ax.set_title(judul)
        plt.xticks(rotation=30, ha="right")
        return simpan_grafik(judul)

    def grafik_garis(series, judul, ylabel="ribu Rp"):
        """Gambar Series sebagai grafik garis (nilai dibagi 1000) lalu simpan."""
        plt.figure()
        ax = plt.gca()
        ax.plot([str(i) for i in series.index], series.values / 1000, marker="o", color="#0EA5E9")
        ax.set_ylabel(ylabel)
        ax.set_title(judul)
        plt.xticks(rotation=30, ha="right")
        return simpan_grafik(judul)

    import builtins
    safe_builtins = {k: getattr(builtins, k)
                     for k in ("abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "format", "int",
                               "isinstance", "len", "list", "map", "max", "min", "print", "range", "reversed", "round",
                               "set", "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "KeyError",
                               "TypeError", "ZeroDivisionError", "__import__")}
    real_import = safe_builtins["__import__"]
    allowed = {"pandas", "numpy", "matplotlib", "matplotlib.pyplot", "math", "statistics", "datetime", "calendar",
               "collections", "itertools", "json", "re"}

    def guarded_import(name, *a, **k):
        if name.split(".")[0] not in {m.split(".")[0] for m in allowed}:
            raise ImportError(f"modul {name!r} tidak diizinkan di sandbox")
        return real_import(name, *a, **k)
    safe_builtins["__import__"] = guarded_import

    g = {"__builtins__": safe_builtins, "df": df, "pd": pd, "np": np, "plt": plt, "simpan_grafik": simpan_grafik,
         "rupiah": rupiah, "__name__": "__analisis__", "keluar": keluar, "masuk": masuk, "hari_ini": hari_ini,
         "bulan_ini": bulan_ini, "HARI": HARI, "total_per": total_per, "per_bulan": per_bulan,
         "per_hari_minggu": per_hari_minggu, "proyeksi_akhir_bulan": proyeksi_akhir_bulan,
         "grafik_batang": grafik_batang, "grafik_garis": grafik_garis}
    code = open("code.py", encoding="utf-8").read()
    exec(compile(code, "kode_analisis.py", "exec"), g)  # noqa: S102 - kode sudah divalidasi AST, berjalan di sandbox
    if plt.get_fignums() and not charts:
        simpan_grafik("Grafik")
    hasil = g.get("hasil")
    try:
        json.dumps(hasil, default=str)
    except (TypeError, ValueError):
        hasil = str(hasil)
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump({"hasil": hasil, "grafik": charts}, f, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main()
