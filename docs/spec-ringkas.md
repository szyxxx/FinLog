# Spec ringkas: AI agent pencatat keuangan (proyek matkul Infrastruktur & Platform Cerdas)

Tim 2 orang. Laptop pemilik proyek = local AI server. Teman = web app "sandbox" (client) yang
menampilkan detail proses AI agent secara komprehensif (tiap langkah, tool call, prompt/output,
waktu, token), selain fitur pencatatan itu sendiri.

## Use case
Agent pencatat pemasukan & pengeluaran pribadi. Input bisa dari:
1. Teks / chat ("beli bensin 30rb", "gajian 4,5 juta").
2. Foto / scan struk belanja (model vision mengekstrak toko, tanggal, item, total, kategori).
3. Voice note (speech-to-text lalu diproses seperti teks).
4. Email (mis. notifikasi transfer bank, e-wallet, tagihan, invoice belanja online) - dibaca lewat
   IMAP atau upload file .eml, lalu diekstrak menjadi transaksi.
Agent juga menjawab pertanyaan ("pengeluaran makan bulan ini berapa?"), membuat ringkasan/laporan,
dan meminta konfirmasi bila data ambigu atau kepercayaannya rendah.

## Hardware server (laptop)
RTX 5060 Laptop 8 GB VRAM, RAM 30 GB, 16 thread, Linux. Ollama terpasang dengan model:
qwen3.5:9b (LLM, kemungkinan multimodal/vision - perlu diverifikasi) dan bge-m3 (embedding).
Python 3.14 + uv, Node tersedia. Docker belum terpasang.

## Arsitektur
Web app sandbox (teman) --HTTP REST + SSE (API key)--> AI server (FastAPI) di laptop:
- API gateway: autentikasi API key, rate limit sederhana, antrean job (GPU satu, request diproses berurutan).
- Agent: LLM + tools (catat_transaksi, ubah/hapus transaksi, cari/filter transaksi, ringkasan per
  kategori/periode, ekstrak_struk, transkripsi_suara, baca_email, minta_konfirmasi).
- Ollama: LLM + vision + embedding. Speech-to-text: faster-whisper (GPU) di server.
- Database: SQLite (transaksi, kategori, run agent, langkah/trace).
- Trace: setiap run agent mencatat langkah-langkahnya (input, reasoning/rencana bila ada, tool call
  + argumen + hasil, prompt & output LLM, durasi, jumlah token) dan bisa dibaca/di-stream oleh sandbox.
- Monitoring: endpoint metrik (latency, token/detik, panjang antrean, pemakaian GPU/VRAM).
- Jaringan: LAN yang sama; cadangan Tailscale bila WiFi kampus memblokir koneksi antar perangkat.
