# FinLog

Agen AI pencatat pemasukan dan pengeluaran yang berjalan sepenuhnya di satu laptop. Catat transaksi lewat
ketikan, rekaman suara, atau foto struk; agen memahami isinya, memakai alat untuk mencatat, dan bertanya balik
kalau datanya kurang jelas. Tidak ada data keuangan yang dikirim ke layanan cloud.

Proyek mata kuliah Infrastruktur dan Platform Cerdas oleh Teuku Laksamana Andrian Rahman dan Axel David.

## Isi repo

| Folder | Isi |
|---|---|
| [`server/`](server/) | AI server: FastAPI, agen LangGraph, model Qwen 3.5 9B lewat Ollama, Whisper untuk suara, OCR untuk struk, SQLite |
| [`webapp/`](webapp/) | Web app sandbox (React + Vite): kirim teks/suara/foto dan lihat setiap langkah kerja agen secara live |
| [`docs/`](docs/) | Dokumen use case, SRS cara kerja sistem, narasi, slide, dan kontrak API |

## Cara kerja singkat

1. Pengguna mengirim teks, rekaman suara, atau foto struk dari web app.
2. Server memeriksa permintaan dan memasukkannya ke antrean (satu GPU, diproses bergiliran).
3. Agen LangGraph menyiapkan masukan: suara diubah jadi teks oleh Whisper; struk dibaca OCR, dicek dengan aturan
   dan lima pemeriksaan angka, dengan model AI sebagai cadangan.
4. Model AI berpikir lalu memakai alat: catat, ubah, hapus, cari, ringkasan, atau tanya balik ke pengguna.
5. Hasil disimpan di SQLite, dan setiap langkah tampil live di web app.

Penjelasan lengkap untuk pemula ada di [`docs/srs-cara-kerja.pdf`](docs/srs-cara-kerja.pdf).

## Menjalankan

Prasyarat: GPU NVIDIA (dicoba di RTX 5060 8 GB), [Ollama](https://ollama.com) dengan model `qwen3.5:9b`,
[`uv`](https://docs.astral.sh/uv/), `ffmpeg`, dan Node 20+.

```bash
ollama pull qwen3.5:9b

# Terminal 1: server
cd server
uv sync
cp .env.example .env      # lalu ganti API_KEYS dengan key acak
./run.sh                  # http://localhost:8000 (dokumentasi API: /docs)

# Terminal 2: web app
cd webapp
npm install
npm run dev               # buka http://localhost:5180
```

Web app membaca API key dari `server/.env` dan menyisipkannya otomatis, jadi tidak perlu diisi di browser.
Untuk membagikan ke orang lain lewat link https publik, lihat bagian "Bagikan ke teman sekelas" di
[`webapp/README.md`](webapp/README.md).

Detail konfigurasi (mode struk, mesin agen, reset database) ada di [`server/README.md`](server/README.md).
