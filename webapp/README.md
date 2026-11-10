# Kas Agent Sandbox (web app)

Client untuk AI server pencatat keuangan (`../server`). Menampilkan proses agent secara lengkap: timeline trace
live lewat SSE (reasoning yang mengalir, prompt/output LLM, tool call, guard, anti-dobel, outcome), struk dengan
kotak bbox OCR (gambar dari `GET /v1/runs/{id}/image`), voice note asli (`/audio`), alur konfirmasi, transaksi, riwayat run, antrean job, dan panel sistem (GPU/VRAM, token/detik).
Kontrak API: [`../docs/api.md`](../docs/api.md).

Teknologi: Vite + React + TypeScript. Tanpa library UI/chart (grafik SVG sendiri); ikon `lucide-react`, font Figtree
dan JetBrains Mono dibundel (tidak butuh internet saat demo).

## Menjalankan

Butuh Node 20+.

```bash
cd webapp
npm install          # sekali saja
npm run dev          # http://localhost:5180  (juga terbuka di IP LAN, mis. http://10.31.104.50:5180)
```

Build produksi (opsional): `npm run build` lalu `npm run preview` (port 5180), atau sajikan folder `dist/` dengan
server statis apa pun.

## Menghubungkan ke server

Web app meneruskan `/v1/*` dan `/health` ke server lewat proxy Vite (dev **dan** preview). Target proxy diatur env
`API_TARGET` (default `http://localhost:8000`), mis. `API_TARGET=http://localhost:8002 npm run dev`.

**Tidak perlu mengisi API key.** Saat `npm run dev` / `npm run preview` / `scripts/share.sh` start, proxy membaca key
**pertama** dari `API_KEYS` di `../server/.env` (atau env `API_KEY`) lalu menyisipkan header `X-API-Key` ke semua request
yang diteruskan (REST, SSE, `/image`, `/audio`, unggah). Key hanya ada di proses Node: tidak dibundel ke `dist/`,
tidak dikirim ke browser, tidak masuk URL/QR/log. Restart dev/preview setelah mengganti `API_KEYS`.
Konsekuensinya: **siapa pun yang bisa membuka web app bisa memakai API** (lihat bagian link publik).

Klik ikon gerigi (kanan atas) → isi:

| Isian | Nilai |
|---|---|
| URL server | Kosong = lewat web app ini (proxy, disarankan). Isi hanya bila server di mesin lain, mis. `http://<IP-LAN-SERVER>:8000` |
| API key | **Opsional.** Kosong = key server disisipkan proxy. Isi hanya bila memakai URL server lain (tanpa proxy) |
| Mode mock | **matikan** untuk memakai server sungguhan |

Tombol **Uji koneksi** memanggil `/health` dan `/v1/system`. Pengaturan disimpan di `localStorage` browser.

**Mode mock** (mati secara default; nyalakan di Pengaturan atau buka dengan `?mock=1`) memakai data contoh tanpa jaringan: trace nyata yang diekstrak dari
`server/data/finance.db` (dibuka read-only) ditambah beberapa skenario sintetis (fallback vision, error timeout).
Input yang dikirim di mode mock diputar ulang seperti run sungguhan, termasuk reasoning yang mengalir. Perbarui data
mock dengan `npm run fixtures`. URL `?mock=1` / `?mock=0` memaksa mode tanpa membuka pengaturan.

## Pindah ke laptop teman

Web app berjalan di laptop teman, AI server tetap di laptop pemilik.

1. Di laptop server, cari IP: `ip -4 addr show | grep inet` (mis. `10.31.104.83`).
2. Pastikan server bisa dijangkau dari laptop teman: `curl http://10.31.104.83:8000/health`.
3. Bila gagal, buka port di laptop server: `sudo ufw allow 8000/tcp` (cek dulu dengan `sudo ufw status`).
4. Di laptop teman: `npm install && npm run dev`, buka `http://localhost:5180`, lalu ganti **URL server** ke
   `http://10.31.104.83:8000` dan isi API key (di laptop teman tidak ada `server/.env`, jadi proxy tidak punya key).
   Alternatif tanpa isi apa pun: `API_TARGET=http://10.31.104.83:8000 API_KEY=<key> npm run dev`.

Catatan jaringan:
- **WiFi kampus** sering memakai *client isolation* (perangkat tidak bisa saling akses). Pakai **hotspot HP**
  (kedua laptop tersambung ke hotspot yang sama; IP server akan berubah, cek lagi dengan `ip -4 addr`), atau
  Tailscale (`http://100.x.y.z:8000`, lihat `server/README.md`).
- CORS server default `*`. Bila `CORS_ORIGINS` diatur, tambahkan origin web app, mis. `http://localhost:5180`.
- **Rekam suara di browser** hanya diizinkan di `https://` atau `http://localhost`. Jadi buka web app lewat
  `localhost` di laptop teman (bukan lewat IP LAN). Bila tidak bisa, unggah file audio saja.

## Fitur

| Halaman | Isi |
|---|---|
| **Sandbox** | Input chat / foto struk (pratinjau) / rekam suara (MediaRecorder) atau unggah audio. Run terpilih: KPI (durasi, panggilan LLM, tool call, token), jawaban akhir, transaksi yang dibuat, kotak konfirmasi (opsi sebagai tombol, jawaban dikirim dengan `conversation_id` yang sama), panel struk (gambar + bbox OCR per baris/kotak berwarna menurut confidence, hasil ekstraksi, cek validasi), dan timeline trace (Gantt mini per step; klik untuk membuka prompt lengkap, output, reasoning, waktu & token, argumen + hasil tool, JSON mentah). Selama run berjalan, reasoning/jawaban mengalir live dari event `llm_delta` (lewat link publik Quick Tunnel: polling, lihat di bawah) |
| **Transaksi** | Saldo, pemasukan, pengeluaran per periode; batang per kategori; pengeluaran harian; tabel dengan filter, tambah manual, ubah (PATCH hanya field yang berubah), hapus (soft delete); klik sumber untuk membuka trace run asal |
| **Riwayat** | Antrean job (job yang sedang diproses, posisi antrean, waktu tunggu) dan daftar run dengan filter jenis input / status / outcome |
| **Sistem** | Health, VRAM, tok/detik, utilisasi GPU, panjang antrean (dengan grafik kecil yang terisi tiap polling), latensi per jenis input, model & kapabilitas, Whisper, model yang dimuat Ollama, proses GPU, daftar tools agent |

## Skrip

| Perintah | Fungsi |
|---|---|
| `npm run dev` | Dev server (port 5180) |
| `npm run build` | Cek tipe + build ke `dist/` |
| `npm run fixtures` | Ekstrak ulang data mock dari database server (read-only) |
| `npm run screenshots` | Screenshot Playwright 1440 px mode mock ke `screenshots/` (dev server harus hidup) |
| `npm run e2e:live` | Uji end-to-end **mode live** lewat UI (teks, struk, suara, konfirmasi, ubah/hapus transaksi, SSE) + screenshot `screenshots/live-*`. Butuh server + dev server hidup; memakai LLM sungguhan (±10 menit). API key dibaca dari `../server/.env` |
| `scripts/share.sh <KEY_TAMU>` | Link publik https + QR untuk teman sekelas (lihat bagian berikut) |

## Bagikan ke teman sekelas (link publik + QR)

Semuanya tetap jalan di **satu laptop** (web app + AI server). Cloudflare *Quick Tunnel* memberi link
`https://<acak>.trycloudflare.com` tanpa akun; web app meneruskan request API ke server lokal, jadi tamu cukup
membuka satu link (atau memindai QR).

**Sekali saja:** `cloudflared` sudah terpasang di `~/.local/bin/cloudflared` (rilis resmi, checksum SHA-256 dicek).
Memasang ulang tanpa sudo:

```bash
curl -fL -o ~/.local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared && cloudflared --version
```

**Saat sesi:**

```bash
cd server && ./run.sh          # terminal 1: AI server (port 8000)
cd webapp && scripts/share.sh  # terminal 2: build, preview :5190, tunnel, link + QR
```

Skrip mencetak link (`https://…trycloudflare.com/`, tanpa key) dan QR di terminal, serta menyimpan `share/qr.png`
(untuk slide) dan `share/link.txt`. Tamu langsung bisa memakai tanpa mengisi apa pun: proxy menyisipkan key server.
Opsi: `API_TARGET` (default `http://localhost:8000`), `API_KEY` (bila tidak memakai key pertama di `server/.env`),
`PORT` (default 5190), `SKIP_BUILD=1`. **Ctrl+C** mematikan tunnel dan preview; link langsung mati. Link baru = URL
baru setiap kali. Tamu melihat banner "Data bersama untuk demo - jangan masukkan data keuangan asli."

Hal yang perlu diketahui:
- **GPU satu, antrean FIFO.** Request diproses bergantian: ±15–50 detik per request (struk lebih lama), dan antrean
  terlihat di halaman Riwayat. Bila 10 orang mengirim bersamaan, yang terakhir menunggu beberapa menit.
- **Link = akses penuh ke API.** Siapa pun yang memegang link (atau QR) bisa mencatat, mengubah, dan menghapus
  transaksi. Bagikan hanya ke kelas, dan matikan `share.sh` setelah sesi.
- **Batas 30 job/menit per key.** Semua tamu memakai key server yang sama, jadi batas ini dibagi bersama (lewat batas: 429).
  Agar tamu tidak memakai key utama: tambahkan key kedua di `API_KEYS` dan jalankan `API_KEY=<key-tamu> scripts/share.sh`.
- **Reasoning tidak mengalir live lewat link publik.** Cloudflare: "Quick Tunnels do not support Server-Sent Events
  (SSE)". Web app otomatis beralih ke polling (label "live via polling"): step muncul setelah selesai, reasoning
  lengkap tetap bisa dibuka per step. Dari laptop server/LAN, SSE dan reasoning live tetap jalan.
- **Buku kas bersama.** Tidak ada akun per orang; semua tamu melihat dan bisa mengubah/menghapus transaksi yang sama.
- **Reset database setelah sesi** (hentikan server dulu): `rm -f server/data/finance.db*` lalu `./run.sh`
  (lihat `server/README.md`; hapus juga `server/data/uploads/` bila foto/suara tamu tidak perlu disimpan).
- **Laptop jangan sleep** dan tetap tersambung internet selama sesi (colokkan charger, matikan suspend otomatis).
  Quick Tunnel tanpa jaminan uptime dan maks. 200 request bersamaan.
- **Mencabut akses tamu:** tekan Ctrl+C di `share.sh` (link mati; link berikutnya berbeda). Bila memakai key tamu
  terpisah, hapus juga dari `API_KEYS` lalu restart server (key itu langsung ditolak 401).

## Struktur

```
src/api/types.ts       tipe sesuai docs/api.md + interface ApiClient
src/api/http.ts        klien live: fetch + X-API-Key, EventSource dengan ?api_key=
src/mock/              klien mock (antrean FIFO + SSE tiruan) dan fixture
src/lib/useRunTrace.ts gabungan GET /v1/runs/{id} + SSE per run
src/components/        RunView (trace), StepDetail, ReceiptViewer (bbox), InputPanel
src/pages/             Sandbox, Transaksi, Riwayat, Sistem
```
