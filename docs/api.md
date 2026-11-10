# Kontrak API: AI Finance Agent Server (v0.1)

Untuk web app **sandbox**. Server: FastAPI di laptop pemilik proyek. Dokumentasi interaktif (OpenAPI) ada
di `http://<IP-SERVER>:8000/docs`, sedangkan skema mentahnya di `/openapi.json`.

## Dasar

| Hal | Nilai |
|---|---|
| Base URL | `http://<IP-LAN-SERVER>:8000` (atau IP Tailscale `100.x.y.z`) |
| Autentikasi | Header `X-API-Key: <key>`. Untuk `EventSource`/`<img>`/`<audio>` (tidak bisa set header) pakai query `?api_key=<key>` atau alias `?key=<key>` (berlaku di semua endpoint ber-auth) |
| CORS | Diizinkan dari origin di `CORS_ORIGINS` (default `*`) |
| Format | JSON (UTF-8). Upload memakai `multipart/form-data` |
| Uang | Rupiah, **integer** (`47700` = Rp47.700) |
| Tanggal transaksi | `YYYY-MM-DD` (zona Asia/Jakarta) |
| Timestamp run/job/step | Unix epoch detik (float), mis. `1794200000.12` |
| Rate limit | `RATE_LIMIT_PER_MINUTE` (default 30) job per menit per key. Lewat batas → `429` |

Pesan error (`detail`, `run.error`, step `error`/`fallback`) tidak memuat path absolut server; path disamarkan menjadi
`…/<nama-file>`. Contoh: `/v1/receipt` dengan file bukan gambar → `400 {"detail": "bukan gambar yang valid (UnidentifiedImageError); unggah JPG/PNG/WEBP"}`.

Kode error: `401` key salah atau tidak ada, `400` input tidak valid, `404` tidak ditemukan, `413` file terlalu besar,
`422` validasi body (format FastAPI: `{"detail": [...]}`), `429` rate limit, `502` IMAP gagal.
Badan error: `{"detail": "pesan"}`.

## Model eksekusi: job → run → steps

Setiap input (teks/struk/suara/email) menjadi **job** di antrean. GPU hanya satu, jadi job diproses **satu per satu (FIFO)**.
Setiap job punya satu **run** agent, dan setiap run berisi urutan **step** (trace).

```
POST /v1/chat ──► job (queued) ──► worker ──► run (running) ──► steps ... ──► run (done | awaiting_confirmation | ...)
                         │                              └── SSE /v1/runs/{run_id}/stream
                         └── GET /v1/jobs/{job_id} (posisi antrean)
```

Ada dua cara memakai endpoint input:
- **Asinkron (disarankan untuk sandbox):** `POST` langsung mengembalikan `job.id` dan `job.run_id`. Setelah itu buka SSE
  `/v1/runs/{run_id}/stream` untuk melihat langkah secara live.
- **Sinkron:** tambahkan `?wait=true` (opsional `&timeout=600`, default 600 detik). Respons baru dikirim setelah run selesai dan sudah berisi `run` lengkap.

Thinking model **aktif** (`LLM_THINK=true`), sehingga satu run teks butuh ±20–60 detik. Untuk UI, jalur asinkron + SSE lebih baik
karena reasoning bisa ditampilkan live lewat event `llm_delta`. Satu run dibatasi `AGENT_RUN_TIMEOUT_S` (default 600 detik);
bila lewat, run berakhir `error` dengan pesan `timeout: ...`.

### Respons endpoint input

```json
{
  "job": {
    "id": "job_1a2b3c4d5e6f", "input_type": "text", "status": "queued", "run_id": "run_9f8e7d6c5b4a",
    "conversation_id": "conv_0a1b2c3d4e5f", "queue_position": 1, "payload": {"text": "..."},
    "created_at": 1794200000.1, "started_at": null, "finished_at": null, "wait_ms": null, "run_ms": null, "error": null
  },
  "run": null,          // terisi (RunSummary) bila wait=true
  "timed_out": false
}
```

`job.status`: `queued` | `running` | `done` | `error`. `queue_position`: 0 = sedang diproses, 1 = berikutnya, dst.

### Status dan outcome run

`run.status` menyatakan **proses**, sedangkan `run.outcome` menyatakan **hasil** untuk UI.

| `run.status` | Arti |
|---|---|
| `queued` / `running` | Belum selesai (`outcome` = `null`) |
| `done` | Selesai. `final_answer` berisi jawaban agent |
| `awaiting_confirmation` | Agent bertanya balik (`final_answer` = pertanyaan). Jawab dengan `POST /v1/chat` memakai **`conversation_id` yang sama** |
| `done_with_warnings` | Selesai, tetapi ada tool tulis yang gagal dan tidak ada yang berhasil. Tampilkan `warnings` dari step `final` |
| `error` | Exception atau timeout. Lihat `run.error` dan step `error` |

| `run.outcome` | Status | Arti |
|---|---|---|
| `recorded` | `done` | Minimal satu transaksi baru tercatat |
| `modified` | `done` | Transaksi diubah/dihapus, tanpa transaksi baru |
| `answered` | `done` | Pertanyaan/chat dijawab tanpa mengubah data |
| `not_transaction` | `done` | Email/struk dibaca tetapi **bukan transaksi** (mis. email promo). Tidak ada yang dicatat |
| `duplicate` | `done` | **Anti-dobel:** email dengan Message-ID sama atau gambar struk dengan hash sama sudah pernah diproses. Ditolak tanpa memanggil LLM, dan `final_answer` menyebut run serta transaksi sebelumnya |
| `possible_duplicate` | `awaiting_confirmation` | Struk lain (file berbeda) dengan toko + tanggal + total yang sama sudah tercatat. Jawab "ya, catat" atau "tidak" lewat `conversation_id` |
| `needs_confirmation` | `awaiting_confirmation` | Agent minta konfirmasi (data ambigu) |
| `error` | `error` | Gagal |

## Endpoint

### Sistem
| Method | Path | Auth | Keterangan |
|---|---|---|---|
| GET | `/health` | tidak | `{"status":"ok","time":"2026-11-09T16:16:53+07:00","queue_length":0}`: `time` adalah **string ISO 8601** dengan zona (Asia/Jakarta), bukan epoch |
| GET | `/v1/system` | ya | Konfigurasi server, lihat [bentuk lengkap](#v1system) |
| GET | `/v1/metrics?window=200` | ya | Metrik (lihat di bawah) |
| GET | `/v1/agent/graph` | ya | **Baru (aditif).** Mesin agent aktif + diagram graf LangGraph, lihat [Mesin agent](#mesin-agent-agent_engine) |

### Input (membuat job)
| Method | Path | Body | Keterangan |
|---|---|---|---|
| POST | `/v1/chat` | JSON `{"text": "...", "conversation_id": null}` | Pencatatan, pertanyaan, ubah/hapus, atau jawaban konfirmasi. `text` kosong atau hanya spasi/baris baru → `422`, tanpa membuat job |
| POST | `/v1/receipt` | multipart: `file` (jpg/png/webp ≤10 MB), `note?`, `conversation_id?` | Foto struk → ekstraksi (lihat `RECEIPT_MODE`) → JSON → transaksi |
| POST | `/v1/voice` | multipart: `file` (ogg/opus/m4a/mp3/wav/webm ≤25 MB), `conversation_id?` | Voice note → Whisper → agent |
| POST | `/v1/email` | multipart: `file` (.eml ≤5 MB), `conversation_id?` | **Nonaktif secara default** (`EMAIL_ENABLED=false`): `410` `{"detail":"Fitur email dinonaktifkan ..."}`, tanpa membuat job. Bila dinyalakan: email → parsing → agent |
| POST | `/v1/email/imap/fetch` | – | **Nonaktif secara default**: `410` seperti di atas. Bila dinyalakan: mengambil email dari IMAP (read-only), satu job per email. Email yang Message-ID-nya sudah pernah diproses dilewati. Respons `{"fetched", "jobs":[Job], "skipped_duplicates":[{"dedup_key","run_sebelumnya"}]}`. `400` bila IMAP belum dikonfigurasi |

Semua endpoint di atas menerima query `wait` (bool) dan `timeout` (detik, 1–900).

### Antrean
| Method | Path | Keterangan |
|---|---|---|
| GET | `/v1/jobs?status=&limit=50` | `{"queue_length", "current_job", "jobs":[Job]}` |
| GET | `/v1/jobs/{job_id}` | Satu `Job` (dengan `queue_position`, `wait_ms`, `run_ms`) |

### Trace
| Method | Path | Keterangan |
|---|---|---|
| GET | `/v1/runs?input_type=&status=&outcome=&conversation_id=&limit=50&offset=0` | **`{"runs": [RunListItem]}`** (objek, bukan array), urut terbaru dulu. `RunListItem` = field `RunSummary` tanpa `transactions`, `steps`, `dedup_key`: `id, job_id, conversation_id, input_type, input_text, status, outcome, final_answer, error, model, prompt_tokens, completion_tokens, llm_calls, tool_calls, created_at, started_at, finished_at, duration_ms, answered_by_run_id, answers_run_id, input_meta`. Di sini `input_meta` **ringkas** dan tanpa path server: `{"filename": "struk.png"}` (`null` untuk teks), dan untuk email `{"filename", "subject"}` (`subject` `null` selama email belum dibaca). `input_meta` lengkap ada di `GET /v1/runs/{id}` |
| GET | `/v1/runs/{run_id}?include_messages=false` | `RunSummary` + `steps[]` (+ `messages[]` riwayat percakapan bila diminta) |
| GET | `/v1/runs/{run_id}/image` | File **gambar struk asli** yang diunggah (bytes, `Content-Type` sesuai file). Koordinat bbox step `ocr` mengacu ke gambar ini. Run jawaban konfirmasi memakai gambar dari run asalnya. `404` bila run tidak punya gambar. Untuk `<img>`: `.../image?key=KEY` |
| GET | `/v1/runs/{run_id}/audio` | File **voice note asli** (bytes). Run jawaban konfirmasi memakai audio dari run asalnya. `404` bila tidak ada. Untuk `<audio>`: `.../audio?key=KEY` |
| GET | `/v1/runs/{run_id}/stream` | **SSE** per run (lihat di bawah) |
| GET | `/v1/events` | **SSE** global: semua event dari semua run dan job |

### Transaksi (CRUD langsung, tanpa agent)
| Method | Path | Keterangan |
|---|---|---|
| GET | `/v1/transactions?jenis=&kategori=&kata_kunci=&periode=&tanggal_mulai=&tanggal_akhir=&source=&limit=100` | `{"jumlah_hasil", "total_nominal", "transaksi":[Tx]}`. Bila tidak diisi, `periode` dianggap `semua` |
| GET | `/v1/transactions/{id}` | Satu `Tx` |
| POST | `/v1/transactions` | `{"jenis","jumlah","kategori","deskripsi?","merchant?","tanggal?"}` → `201 Tx` (source `manual`) |
| PATCH | `/v1/transactions/{id}` | Field yang diubah saja → `{"sebelum":Tx,"sesudah":Tx}`. Validasi ketat → `400`, data tidak berubah: `kategori` tak dikenal, atau kategori tidak cocok dengan jenis (mis. `gaji` pada `pengeluaran`). Mengganti `jenis` saja juga ditolak bila kategori lama tidak cocok; kirim `jenis` + `kategori` sekaligus. (Jalur agent tetap longgar: kategori tak dikenal dipetakan ke `lainnya_masuk`/`lainnya_keluar`) |
| DELETE | `/v1/transactions/{id}` | Soft delete → `{"dihapus":Tx}` |
| GET | `/v1/summary?periode=bulan_ini&tanggal_mulai=&tanggal_akhir=&kategori=` | Ringkasan (lihat di bawah) |
| GET | `/v1/categories` | `[{"name","kind","description"}]` |

Nilai `periode`: `hari_ini` `kemarin` `minggu_ini` `bulan_ini` `bulan_lalu` `tahun_ini` `semua`. Nilai lain → `400`
(sama seperti tanggal yang salah format), di `/v1/transactions` maupun `/v1/summary`.
`tanggal_mulai`/`tanggal_akhir` menimpa `periode`.

## Skema data

### Tx (transaksi)
```json
{
  "id": 4, "kind": "pengeluaran", "amount": 47700, "category": "belanja",
  "description": "Air mineral, roti, mie, sabun", "merchant": "SERBA ADA MART", "date": "2026-11-08",
  "source": "receipt", "items": [{"nama": "AIR MINERAL 600ML", "qty": 2, "harga_satuan": 3500, "subtotal": 7000}],
  "run_id": "run_19997aa1ca2e", "created_at": "2026-11-09T12:17:12+07:00", "updated_at": "2026-11-09T12:17:12+07:00"
}
```
`kind`: `pemasukan` | `pengeluaran`. `source`: `text` | `receipt` | `voice` | `email` | `manual`. `items` hanya ada untuk struk.
Kategori: `makanan_minuman`, `transportasi`, `belanja`, `tagihan`, `kesehatan`, `pendidikan`, `hiburan`, `transfer_keluar`,
`lainnya_keluar` (pengeluaran); `gaji`, `transfer_masuk`, `lainnya_masuk` (pemasukan).

### Summary
```json
{"periode": "bulan_ini (2026-11-01 s/d 2026-11-09)", "total_pemasukan": 5250000, "total_pengeluaran": 294700, "saldo": 4955300,
 "per_kategori": [{"jenis": "pengeluaran", "kategori": "belanja", "jumlah_transaksi": 2, "total": 224700}]}
```

### RunSummary
```json
{
  "id": "run_...", "job_id": "job_...", "conversation_id": "conv_...", "input_type": "receipt",
  "input_text": "teks yang akhirnya diberikan ke agent (mis. hasil transkripsi / JSON struk / isi email)",
  "input_meta": {"filename": "struk.png", "bytes": 91488, "image_path": "..."},
  "status": "done", "outcome": "recorded", "final_answer": "Transaksi pengeluaran Rp47.700 ...", "error": null,
  "model": "qwen3.5:9b", "prompt_tokens": 5458, "completion_tokens": 470, "llm_calls": 3, "tool_calls": 1,
  "created_at": 1794200000.1, "started_at": 1794200000.2, "finished_at": 1794200041.9, "duration_ms": 41884,
  "dedup_key": "receipt:sha256:...",   // kunci anti-dobel (null untuk teks/suara)
  "engine": "langgraph",               // aditif: mesin yang menjalankan run ini (langgraph | custom; null untuk run lama)
  "answered_by_run_id": null,          // hanya untuk run awaiting_confirmation: run berikutnya yang menjawabnya (null bila belum dijawab)
  "answers_run_id": null,              // run awaiting_confirmation sebelumnya yang dijawab oleh run ini (null bila bukan jawaban)
  "transactions": [Tx],          // transaksi yang dibuat run ini
  "steps": [Step]                // hanya di GET /v1/runs/{id}
}
```

### Step (unit trace)
```json
{
  "id": 17, "run_id": "run_...", "seq": 3, "type": "llm", "name": "agent_step_1", "status": "ok",
  "input": {...}, "output": {...},
  "started_at": 1794200017.0, "duration_ms": 8472,
  "prompt_tokens": 2226, "completion_tokens": 123, "tokens_per_second": 16.46,
  "node": "agent"   // aditif: node graf LangGraph yang menulis step ini (null untuk mesin custom / run lama)
}
```
`status`: `ok` | `error` | `warning`. Token hanya ada pada step `llm` (termasuk token thinking: Ollama tidak memisahkan
jumlah token thinking dan jawaban). Setiap panggilan LLM yang berpikir menghasilkan dua step berurutan: **`reasoning`**
(teks thinking) lalu **`llm`** (prompt, output, token), dengan `llm.output.reasoning_step_seq` menunjuk ke step reasoning-nya.

| `type` | `name` | `input` | `output` |
|---|---|---|---|
| `input` | jenis input (`text`/`receipt`/`voice`/`email`) | payload asli (teks, nama file, ukuran) | `{"conversation_id"}` |
| `preprocess` | `transkripsi_suara` | `{"file","engine"}` | `{"text","language","language_probability","audio_seconds","segments":[{start,end,text,avg_logprob}],"engine":{model,device,compute_type},"duration_ms"}` |
| `preprocess` | `ekstrak_struk` | `{"file","bytes","receipt_mode","fallback_vision"}` | `{"data":{toko,tanggal,items[],total,diskon,pajak_layanan,metode_bayar,kategori,keyakinan,catatan,cek_konsistensi,sumber_ekstraksi},"mode","jalur":["ocr","llm_teks"\|"rules"\|"vision"],"fallback":null\|{alasan,hasil_ocr_llm},"validasi_lolos","ocr":{mean_conf,n_lines,ms},"raw_output","prompt","llm":[usage]}`. Step ini ditutup **setelah** step-step di bawah (ocr, llm, validation, fallback); `sumber_ekstraksi` = `ocr_llm` / `ocr_rules` / `ocr_only` / `vision` / `vision_fallback` |
| `ocr` | `ocr_struk` | `{"engine","image_size","ocr_input_size"}` | `{"lines":[{"text","conf","bbox":[x0,y0,x1,y1],"box_index":[i]}],"boxes":[{"index","text","conf","quad":[[x,y]×4],"bbox":[x0,y0,x1,y1]}],"n_boxes","mean_conf","min_conf","skew_deg","image_size":[w,h],"coord_space","timings_ms":{total,det,cls,rec}}`. **Koordinat = piksel gambar asli yang diunggah** (setelah rotasi EXIF), jadi kotak bisa langsung digambar di atas gambar. `lines` = gabungan kotak per baris struk (nama item + harga) |
| `rules` | `parse_aturan` | `{"jumlah_baris","rules_first?"}` | JSON struk hasil aturan/regex (mode `ocr_only`, atau `ocr_llm` dengan `RECEIPT_RULES_FIRST=true`) |
| `validation` | `validasi_struk` | `{"sumber":"ocr_llm"\|"ocr_only"\|"rules_first"}` | `{"lolos","gagal":[nama cek],"checks":{"total_positif","total_ada_di_teks_ocr","tanggal_ada_di_teks_ocr","item_cocok_total","confidence_ocr"}}` (tiap cek berisi `ok` dan detail angkanya). `status` = `warning` bila tidak lolos |
| `fallback` | `fallback_vision` | `{"alasan":[cek yang gagal / error],"hasil_ocr_llm"}` | `{"mode_baru":"vision"}`: hasil OCR ditolak lalu struk dibaca ulang oleh model vision (step `llm` `ekstrak_struk_vision` menyusul) |
| `preprocess` | `baca_email` | `{"file","bytes","sumber":"upload"/"imap"}` | `{"from","to","subject","date","message_id","body_type","body","body_truncated","attachments"}` |
| `reasoning` | `agent_step_N` / `ekstrak_struk_vision` | `{"llm_step","think":true}` | `{"thinking": "teks reasoning lengkap", "chars", "time_to_first_token_ms"}`. `duration_ms` = lama fase berpikir |
| `llm` | `agent_step_N` / `ekstrak_struk_ocr_llm` / `ekstrak_struk_vision` | `{"model","messages":[{role,content,images?,tool_calls?,tool_name?}],"tools":[nama]\|null,"format","options":{think,temperature,num_ctx,num_predict}}`, berisi **prompt lengkap** (system + riwayat + user + hasil tool sebelumnya; gambar diganti placeholder) | `{"content","tool_calls":[{id,function:{name,arguments}}],"reasoning_step_seq","thinking_chars","done_reason","timings_ms":{wall,total,load,prompt_eval,eval,time_to_first_token,thinking}}` |
| `tool` | nama tool | argumen dari LLM | hasil tool (`{"ok":true,...}` atau `{"ok":false,"error","instruksi"}`) |
| `dedup` | `cek_duplikat` | `{"kunci"}` (`email:msgid:<id>`, `email:sha256:<hash>`, atau `receipt:sha256:<hash>`) | `{"duplikat","run_sebelumnya","transaksi_sebelumnya":[id]}` |
| `dedup` | `cek_struk_serupa` | `{"toko","tanggal","total"}` | `{"serupa","transaksi_serupa":Tx\|null}` |
| `context` | `lanjutan_konfirmasi` | `{"run_sebelumnya"}` | `{"sumber_transaksi","run_asal","tanggal_default","jumlah_item"}`: run ini menjawab konfirmasi, jadi transaksi mewarisi sumber, item, dan tanggal dari run asal |
| `guard` | `jawaban_kosong` | `{"content": ""}` | `{"tindakan":"jawaban dibuat otomatis dari hasil tool","jawaban"}`, `status: "warning"`: dengan thinking aktif model kadang mengakhiri giliran tanpa teks, sehingga `final_answer` dibuat dari hasil tool tulis (mis. "Transaksi #1 diubah: amount Rp30.000 -> Rp35.000.") |
| `guard` | `klaim_tanpa_tool` | `{"jawaban_model"}` | `{"tindakan","pesan"}`: model mengaku sudah mencatat tanpa memanggil tool, lalu diminta mengulang |
| `confirmation` | `minta_konfirmasi` / `pertanyaan_teks` / `konfirmasi_duplikat` | – | `{"pertanyaan","opsi?","draf_transaksi?"}` |
| `final` | status run | – | `{"answer","outcome","warnings":[]}` |
| `error` | nama exception | – | `{"error"}` |

Tools agent: `catat_transaksi`, `ubah_transaksi`, `hapus_transaksi`, `cari_transaksi`, `ringkasan`, `minta_konfirmasi`.
Skema JSON lengkapnya bisa diambil dari `GET /v1/system` → `tools`. Ekstraksi struk, transkripsi suara, dan pembacaan email
dijalankan sebagai langkah `preprocess` sebelum loop agent (bukan dipanggil oleh LLM).

## SSE (Server-Sent Events)

`GET /v1/runs/{run_id}/stream?api_key=KEY`. Server mengirim ulang step yang sudah ada, lalu step baru secara live,
dan stream ditutup setelah `run_finished`. Bila run sudah selesai, semua step langsung dikirim lalu disusul `run_finished`.

| event | data |
|---|---|
| `job_status` | `Job` (perubahan status/antrean) |
| `run_status` | `{"run_id","status":"running"}` |
| `step_started` | `{"run_id","type","name","started_at","node?"}`: langkah mulai (berguna untuk spinner). Tanpa `seq`, karena seq baru diberikan saat step disimpan |
| `llm_delta` | `{"run_id","step_name","kind":"thinking"\|"content","index","text","node?"}`: potongan reasoning/jawaban **live** selama LLM berjalan, dikirim per ±150 ms. Gabungkan `text` per `step_name`+`kind`. Potongan ini tidak disimpan; teks lengkapnya ada di step `reasoning`/`llm` |
| `step` | `Step` lengkap |

`node?` (aditif) hanya ada bila run dijalankan mesin LangGraph.
| `run_finished` | `RunSummary` (tanpa steps) |
| (comment `: ping`) | keep-alive setiap 10 detik |

**Yang tidak disimpan:** event `llm_delta` (dan `step_started`) hanya dikirim live. Klien yang tersambung belakangan tidak
menerimanya lagi. Saat replay, stream hanya mengirim `step` yang tersimpan, lalu `run_finished`. Teks reasoning dan jawaban
lengkap selalu ada di step `reasoning` dan `llm`.

`GET /v1/events?api_key=KEY` mengirim event yang sama dari semua run, ditambah `hello` di awal. Event ini dipakai untuk dashboard live.

```js
const BASE = "http://192.168.1.10:8000", KEY = "sk-...";
const { job } = await fetch(`${BASE}/v1/chat`, {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-API-Key": KEY },
  body: JSON.stringify({ text: "beli bensin 30rb", conversation_id: convId ?? null }),
}).then(r => r.json());

const es = new EventSource(`${BASE}/v1/runs/${job.run_id}/stream?api_key=${KEY}`);
es.addEventListener("step_started", e => showSpinner(JSON.parse(e.data)));
es.addEventListener("llm_delta", e => { const d = JSON.parse(e.data); appendLive(d.step_name, d.kind, d.text); });
es.addEventListener("step", e => renderStep(JSON.parse(e.data)));
es.addEventListener("run_finished", e => { renderResult(JSON.parse(e.data)); es.close(); });
```

Upload dari browser:
```js
const fd = new FormData(); fd.append("file", fileInput.files[0]);
await fetch(`${BASE}/v1/receipt`, { method: "POST", headers: { "X-API-Key": KEY }, body: fd });
```
Voice note dari mikrofon browser (`MediaRecorder`, biasanya `audio/webm`) bisa langsung dikirim ke `/v1/voice`.

## Metrik (`GET /v1/metrics`)
```json
{
  "window_runs": 12,
  "queue": {"length": 0, "current_job": null},
  "runs_by_status": {"done": 11, "awaiting_confirmation": 3, "awaiting_confirmation_answered": 2},  // *_answered = bukan status, lihat catatan
  "latency_by_input_type": {"text": {"runs": 5, "errors": 0, "latency_ms": {"avg": 9792, "p50": 8369, "p95": 16364, "max": 16364}, "queue_wait_ms_avg": 3300}, "receipt": {...}, "voice": {...}, "email": {...}},
  "llm": {"calls": 25, "tokens_per_second_avg": 16.3, "tokens_per_second_p50": 16.4, "llm_step_ms_avg": 6100, "prompt_tokens_total": 50000, "completion_tokens_total": 2500},
  "stt": {"model": "small", "device": "cuda", "compute_type": "int8_float16", "load_ms": 2076, "real_time_factor_avg": 0.08},
  "ollama_loaded_models": [{"name": "qwen3.5:9b", "size_vram_bytes": 5744749444, "context_length": 8192, "expires_at": "..."}],
  "gpu": {"available": true,
          "gpus": [{"name": "...RTX 5060...", "memory_used_mib": 7149, "memory_total_mib": 8151, "utilization_pct": 35, "temperature_c": 60, "power_w": 20}],
          "processes": [{"pid": 96507, "name": "/home/.../ollama/lib/ollama/llama-server", "used_memory_mib": 6548},
                        {"pid": 96367, "name": ".venv/bin/python", "used_memory_mib": 104}]}
}
```

`runs_by_status` berisi jumlah run per `status`, ditambah satu kunci turunan `awaiting_confirmation_answered`: run
`awaiting_confirmation` yang sudah dijawab (punya `answered_by_run_id`). Kunci `awaiting_confirmation` tetap menghitung semuanya,
jadi yang masih terbuka = `awaiting_confirmation - awaiting_confirmation_answered`.

`gpu.processes` = semua proses yang memakai GPU (dari `nvidia-smi --query-compute-apps`), termasuk aplikasi lain di laptop.
`used_memory_mib` berupa angka MiB, atau string mentah bila nvidia-smi tidak memberi angka (mis. `"[N/A]"`). Bila nvidia-smi
tidak ada: `{"available": false, "reason": "..."}` tanpa `gpus`/`processes`. Nilai numerik di `gpus[]` bisa `null` bila tidak terbaca.

## `/v1/system`

```json
{
  "agent_engine": "langgraph",        // aditif: AGENT_ENGINE (langgraph | custom)
  "llm_model": "qwen3.5:9b", "vision_model": "qwen3.5:9b",
  "think": true,                      // LLM_THINK: agent berpikir (step reasoning + llm_delta)
  "vision_think": false,              // VISION_THINK: thinking pada ekstraksi struk vision (null = ikut think)
  "receipt_mode": "ocr_llm",          // RECEIPT_MODE: ocr_llm | ocr_only | vision
  "receipt_fallback_vision": true, "receipt_rules_first": false,
  "model_capabilities": {"qwen3.5:9b": ["completion", "vision", "tools", "thinking"]},  // atau string "error: ..." bila Ollama tak terjangkau
  "whisper_model": "small", "whisper_device": "auto",
  "email_enabled": false,             // aditif: EMAIL_ENABLED. false = /v1/email & /v1/email/imap/fetch menjawab 410
  "imap_enabled": false,              // selalu false bila email_enabled false
  "tools": [ /* definisi tool agent (format function-calling Ollama/OpenAI: {"type":"function","function":{name,description,parameters}}) */ ],
  "categories": [{"name": "makanan_minuman", "kind": "pengeluaran", "description": "..."}]
}
```

## Ekstraksi struk (`RECEIPT_MODE` di `.env` server)

| Mode | Alur | Step di trace |
|---|---|---|
| `ocr_llm` (default) | OCR di CPU (PaddleOCR PP-OCRv4 via RapidOCR) → LLM **teks saja** (tanpa gambar, tanpa thinking) menyusun JSON → validasi terhadap teks OCR → bila gagal: fallback vision (`RECEIPT_FALLBACK_VISION=true`) | `ocr` → `llm` (`ekstrak_struk_ocr_llm`) → `validation` → [`fallback` → `llm` (`ekstrak_struk_vision`)] → `preprocess` |
| `ocr_only` | OCR + aturan/regex, tanpa LLM untuk ekstraksi. Bila validasi gagal, `keyakinan` diturunkan (≤0,5) sehingga agent meminta konfirmasi | `ocr` → `rules` → `validation` → `preprocess` |
| `vision` | Model vision membaca gambar langsung (jalur awal) | `llm` (`ekstrak_struk_vision`) → `preprocess` |

Opsi `RECEIPT_RULES_FIRST=true` (mode `ocr_llm`): parser aturan dicoba dulu. Bila lolos validasi, LLM tidak dipanggil
(`sumber_ekstraksi = "ocr_rules"`); bila tidak, alur berlanjut ke LLM teks lalu fallback vision.

Validasi (`ocr_llm`/`ocr_only`) lolos bila semua cek ini terpenuhi:
- Total > 0, dan angka total benar-benar muncul di teks OCR.
- Tanggal ada di teks OCR (atau kosong bila OCR memang tidak menemukan tanggal).
- Σ subtotal item − diskon + pajak/layanan ≈ total (toleransi 1% atau Rp100).
- Rata-rata confidence OCR ≥ `OCR_MIN_CONF` (0,80) dengan minimal 3 baris.

Agent pencatat tetap memakai LLM (dengan thinking) di semua mode. Mode ini hanya mengatur cara **membaca** struk.

## Anti-dobel
- **Email**: kunci = `Message-ID` (fallback: SHA-256 isi mentah). Berlaku untuk upload `.eml` maupun IMAP. Email yang sama →
  run `done` + `outcome: "duplicate"`, tidak dicatat ulang, dan LLM tidak dipanggil.
- **Struk**: (1) SHA-256 file gambar yang sama → `duplicate`, ditolak tanpa LLM. (2) File berbeda tetapi hasil ekstraksinya
  toko + tanggal + total sama dengan transaksi struk yang ada → `awaiting_confirmation` + `outcome: "possible_duplicate"`.
- Run yang berakhir `error` tidak dihitung, jadi file yang sama boleh dicoba lagi setelah gagal.

## Alur konfirmasi
1. `POST /v1/chat {"text":"tadi bayar 50"}` → run `awaiting_confirmation`, `final_answer` = "50 rupiah atau 50 ribu? Untuk apa?"
2. Sandbox menampilkan pertanyaannya. Bila step `confirmation` punya `opsi`, tampilkan sebagai tombol.
3. `POST /v1/chat {"text":"50 ribu, parkir", "conversation_id":"<conversation_id run sebelumnya>"}` → agent melanjutkan dengan riwayat percakapan, lalu mencatat transaksi.
4. Run pertanyaan **tetap** berstatus `awaiting_confirmation` (status tidak diubah), tetapi `answered_by_run_id` kini berisi id run
   jawaban, dan run jawaban punya `answers_run_id`. Pakai ini untuk menandai pertanyaan sebagai "sudah dijawab" di UI.

`conversation_id` juga bisa dipakai untuk chat multi-giliran biasa (mis. "hapus yang barusan").

Kontrak di atas sama untuk kedua mesin agent. Di mesin LangGraph, run pertanyaan menjeda graf dengan `interrupt()`
(state disimpan checkpointer SQLite), lalu run jawaban **melanjutkan thread graf yang sama**. Karena itu step `input` dan
`context/lanjutan_konfirmasi` run jawaban ditulis oleh node `tunggu_jawaban`, dan output `lanjutan_konfirmasi` punya field
aditif `"thread_graf": "dilanjutkan"`. Bila thread tidak ada (mis. pertanyaan dibuat mesin custom sebelum mesin diganti),
riwayat diambil dari database seperti biasa (node `konteks`).

## Mesin agent (`AGENT_ENGINE`)

Server punya dua mesin orkestrasi dengan kontrak API, trace, dan SSE yang sama:

| `AGENT_ENGINE` | Isi |
|---|---|
| `langgraph` | Graf LangGraph (`server/app/graph_agent.py`), model lewat `langchain-ollama` `ChatOllama` (tools + reasoning terpisah). Konfirmasi memakai `interrupt()` + checkpointer SQLite |
| `custom` | Loop agent awal (`server/app/agent.py`) |

Perbedaan yang terlihat klien hanya field aditif: `node` pada step (dan `node?` pada `step_started`/`llm_delta`),
`engine` pada RunSummary, `agent_engine` di `/v1/system`, dan endpoint berikut.

`GET /v1/agent/graph`:
```json
{
  "engine": "langgraph",                 // mesin aktif
  "langgraph_available": true,
  "mermaid": "graph TD; ...",            // diagram Mermaid graf (draw_mermaid, dibuat lokal)
  "nodes": ["masuk", "teks", "suara_stt", "struk_dedup", "struk_ocr", "struk_aturan",
            "struk_llm_teks", "struk_validasi", "struk_vision", "struk_hasil", "struk_serupa", "konteks", "agent",
            "tools", "guard", "selesai", "tunggu_jawaban"],
  "edges": [{"source": "agent", "target": "tools", "conditional": true}]
}
```
Diagram selalu dikembalikan (juga saat `engine` = `custom`), agar sandbox bisa menampilkannya.

| node | step yang ditulis |
|---|---|
| `masuk` | `input` |
| `teks` | – |
| `suara_stt` | `preprocess/transkripsi_suara` |
| `struk_dedup` | `dedup/cek_duplikat` |
| `struk_ocr`, `struk_aturan`, `struk_llm_teks`, `struk_validasi`, `struk_vision` | `ocr`, `rules`, `llm`/`reasoning` (`ekstrak_struk_*`), `validation`, `fallback` |
| `struk_hasil`, `struk_serupa` | `preprocess/ekstrak_struk`, `dedup/cek_struk_serupa` |
| `konteks` | `context/lanjutan_konfirmasi` (bila riwayat diambil dari database) |
| `agent` | `reasoning` + `llm` (`agent_step_N`) |
| `tools` | `tool`, `confirmation/minta_konfirmasi` |
| `guard` | `guard/klaim_tanpa_tool`, `guard/jawaban_kosong` |
| `selesai` | `confirmation/pertanyaan_teks`, `confirmation/konfirmasi_duplikat`, `final` |
| `tunggu_jawaban` | `input` + `context/lanjutan_konfirmasi` run jawaban (thread graf dilanjutkan) |

Step `error` (run gagal) ditulis di luar graf, jadi `node` bernilai `null`. Graf tidak punya node email (fitur email
dinonaktifkan); bila `EMAIL_ENABLED=true` dipakai lagi, run email dijalankan mesin custom (`engine: "custom"`).
