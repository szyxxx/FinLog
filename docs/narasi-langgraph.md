# Skrip Narasi: Cara Kerja Agen LangGraph

Perkiraan durasi: 4–5 menit. Teks dalam [kurung siku] adalah petunjuk panggung, tidak dibacakan.

---

## 1. Pembuka (±30 detik)

[Tampilkan diagram graf dari `http://localhost:8000/v1/agent/graph`]

"Agen AI kami tidak ditulis sebagai satu prompt panjang, tetapi sebagai **graf** dengan LangGraph.
Bayangkan graf ini seperti peta jalur kerja. Setiap kotak adalah satu **node**, yaitu satu tugas kecil.
Setiap panah adalah **jalur** ke tugas berikutnya. Semua tugas berbagi satu catatan bersama yang disebut
**state**: isi pesan pengguna, hasil bacaan struk, riwayat percakapan, dan jawaban sementara.

Keuntungannya: alurnya jelas, setiap langkah bisa diperiksa, dan semuanya berjalan di laptop ini,
tanpa mengirim data keuangan ke cloud."

## 2. Pintu masuk (±30 detik)

[Tunjuk node `masuk`]

"Semua permintaan masuk lewat node **masuk**. Di sini graf melihat jenis masukannya, lalu memilih jalur:

- Kalau berupa **teks**, misalnya 'beli bensin 30 ribu', langsung ke node **teks**.
- Kalau berupa **rekaman suara**, ke node **suara_stt**. Whisper mengubah suara menjadi teks.
  Agen juga diberi tahu bahwa ini hasil transkripsi, jadi bisa ada salah dengar.
- Kalau berupa **foto struk**, masuk ke jalur struk yang lebih panjang."

## 3. Jalur struk (±1 menit)

[Tunjuk node `struk_*` satu per satu]

"Jalur struk adalah bagian paling 'agentic'. Ada beberapa lapis pemeriksaan:

1. **struk_dedup**: foto dicek sidik jarinya. Kalau foto yang sama pernah diunggah,
   proses langsung berhenti. Tidak ada transaksi dobel, dan AI tidak perlu dipanggil.
2. **struk_ocr**: OCR membaca semua tulisan di struk beserta posisinya. Ini berjalan di CPU,
   sekitar satu detik.
3. **struk_aturan**: program mencoba membaca toko, tanggal, item, dan total dengan aturan biasa.
   Untuk struk yang rapi, ini sudah cukup, sehingga AI tidak perlu dipanggil.
4. Kalau aturan tidak yakin, **struk_llm_teks** meminta AI menyusun data dari teks OCR.
5. **struk_validasi** mengecek ulang angkanya: apakah total benar-benar tertulis di struk,
   apakah jumlah item cocok dengan total, apakah tanggalnya ada.
6. Kalau validasi gagal, graf pindah ke **struk_vision**. Model AI melihat langsung gambarnya
   sebagai cadangan terakhir.
7. Terakhir, **struk_serupa** mengecek apakah struk dengan toko, tanggal, dan total yang sama
   sudah pernah dicatat. Kalau ada, agen bertanya dulu kepada pengguna.

Jadi AI tidak dipercaya begitu saja. Hasilnya diperiksa program, dan ada jalur cadangan
kalau pemeriksaan gagal."

## 4. Otak agen: agent ↔ tools (±1 menit)

[Tunjuk lingkaran `konteks` → `agent` ↔ `tools`]

"Setelah masukan menjadi teks, node **konteks** mengambil riwayat percakapan,
supaya agen ingat apa yang dibicarakan sebelumnya.

Lalu masuk ke inti: node **agent**. Model AI, Qwen 3.5 di Ollama, berpikir dulu.
Proses berpikir ini terlihat langsung di web app. Lalu agen memutuskan perlu memakai alat apa.
Alatnya ada enam: **catat**, **ubah**, **hapus**, **cari** transaksi, **ringkasan**, dan **minta konfirmasi**.

Kalau agen memanggil alat, graf pindah ke node **tools**. Alat dijalankan, hasilnya
dikembalikan ke agen, dan agen berpikir lagi. Putaran agent → tools → agent ini bisa terjadi
beberapa kali, misalnya 'cari transaksi ngopi' lalu 'hapus transaksi nomor 3'.
Putarannya dibatasi enam langkah, supaya agen tidak berputar tanpa akhir."

## 5. Penjaga: guard (±30 detik)

[Tunjuk node `guard`]

"Model AI kecil kadang 'mengaku' sudah mencatat, padahal tidak memanggil alat apa pun.
Karena itu ada node **guard**. Kalau agen bilang 'sudah dicatat' tanpa alat yang berhasil,
guard menyuruhnya mengulang dan benar-benar memanggil alat. Kalau agen selesai tanpa jawaban,
guard menyusun jawaban dari hasil alat. Pengguna tidak pernah menerima klaim palsu."

## 6. Bertanya balik: interrupt (±45 detik)

[Tunjuk `selesai` → `tunggu_jawaban` → `agent`]

"Kalau datanya kurang jelas, misalnya pengguna cuma menulis 'tadi bayar 50', agen
memanggil alat **minta konfirmasi**: '50 rupiah atau 50 ribu? Untuk apa?'

Di sinilah fitur khas LangGraph dipakai: **interrupt**. Graf **berhenti sementara** di node
**tunggu_jawaban**, dan seluruh state-nya disimpan ke SQLite sebagai **checkpoint**.
Ketika pengguna menjawab, graf **dilanjutkan dari titik yang sama**, bukan mulai dari awal.
Agen masih ingat konteksnya, lalu mencatat transaksinya."

## 7. Penutup & trace (±30 detik)

[Pindah ke web app sandbox, buka satu run, tunjuk tabel Trace]

"Setiap node yang dilewati tercatat sebagai satu langkah di **trace** dan dikirim live ke web app:
nama node, isi pikiran AI, alat yang dipanggil beserta hasilnya, durasi, dan jumlah token.
Jadi agen ini bukan kotak hitam. Kita bisa melihat persis jalur mana yang ia ambil dan alasannya.

Ringkasnya: **LangGraph** mengatur alur, **Qwen di Ollama** berpikir dan memilih alat,
**program** memeriksa hasilnya, dan semua itu berjalan **lokal di satu laptop**."

---

### Contoh alur untuk demo langsung

| Masukan | Jalur yang terlihat di trace |
|---|---|
| "beli bensin 30rb tadi pagi" | masuk → teks → konteks → agent → tools (catat) → agent → guard → selesai |
| Foto struk yang rapi | masuk → struk_dedup → struk_ocr → struk_aturan → struk_hasil → struk_serupa → konteks → agent → tools → … → selesai |
| Foto struk yang sama diunggah lagi | masuk → struk_dedup → selesai (ditolak sebagai duplikat) |
| "tadi bayar 50" | … → agent → tools (minta konfirmasi) → selesai → **tunggu_jawaban** (berhenti) |
| Jawaban "50 ribu, parkir motor" | tunggu_jawaban (dilanjutkan) → agent → tools (catat) → … → selesai |
