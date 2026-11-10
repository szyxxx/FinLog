# Narasi Teknis: Perjalanan Sebuah Permintaan di Dalam Sistem

Sistem kami adalah agen AI pencatat keuangan yang seluruhnya berjalan di satu laptop. Cara terbaik untuk memahaminya adalah mengikuti satu permintaan sejak ditekan tombol Kirim sampai jawabannya muncul di layar. Mari kita mulai.

## 1. Dari browser ke server: web app dan proxy

Semuanya berawal di web app, aplikasi React yang dijalankan oleh Vite di port 5180. Ketika pengguna mengetik "beli bensin 30rb tadi pagi" lalu menekan Kirim, browser mengirim permintaan HTTP `POST /v1/chat` ke web app, bukan langsung ke server AI.

Web app berperan sebagai **reverse proxy**. Saat dinyalakan, ia membaca API key dari file `server/.env`, lalu setiap permintaan ke `/v1/*` diteruskan ke server di `localhost:8000` dengan header `X-API-Key` yang disisipkan di sisi laptop. Hasilnya, browser tidak pernah memegang kunci itu: pengguna tidak perlu mengisinya, dan kunci tidak bisa dicuri dari halaman. Pola yang sama memungkinkan web app dibagikan lewat Cloudflare Tunnel: yang terbuka ke internet hanya satu alamat https menuju web app, sementara server AI tetap tertutup.

## 2. Gerbang server: autentikasi, validasi, pembatasan

Permintaan tiba di server **FastAPI**. Sebelum isinya diproses, server melakukan tiga pemeriksaan.

Pertama, **autentikasi**: nilai `X-API-Key` dicocokkan dengan daftar `API_KEYS`. Kalau tidak cocok, server menjawab `401 Unauthorized`.

Kedua, **validasi input** dengan skema Pydantic: pesan kosong atau hanya spasi ditolak dengan `422`, dan file unggahan dicek jenis serta ukurannya. Tujuannya mencegah pekerjaan sia-sia sampai ke model AI.

Ketiga, **rate limiting**: paling banyak 30 job per menit. GPU adalah sumber daya paling mahal di sistem ini, jadi tidak boleh dibanjiri.

Setelah lolos, file unggahan (foto atau rekaman) disimpan ke folder `data/uploads`, lalu server membuat dua baris di database SQLite: satu **job** (tiket antrean) dan satu **run** (catatan eksekusi), masing-masing dengan ID unik dan status `queued`. Run juga diberi `conversation_id` yang mengikat pesan-pesan dalam satu percakapan. Server langsung membalas dengan `job_id` dan `run_id`, tanpa menunggu AI selesai. Pola **asinkron** ini membuat antarmuka tetap responsif meskipun pemrosesan AI memakan waktu belasan detik.

## 3. Antrean: satu GPU, satu pekerjaan

Job yang dibuat masuk ke **job queue** berbasis `asyncio.Queue` dengan satu worker. Desain ini disengaja: laptop hanya punya satu GPU dengan VRAM 8 GB, dan model bahasanya saja sudah memakai sekitar 5,7 GB. Kalau dua permintaan diproses bersamaan, keduanya akan berebut memori dan bisa gagal. Karena itu job diproses **serial**, satu per satu, dan statusnya berubah dari `queued` ke `running` lalu ke `done`. Posisi antrean bisa dilihat di endpoint `/v1/jobs`.

## 4. Siaran langsung: trace dan SSE

Begitu menerima `run_id`, web app membuka koneksi **Server-Sent Events** ke `/v1/runs/{id}/stream`. SSE adalah koneksi HTTP satu arah yang dibiarkan terbuka, sehingga server bisa terus mendorong data ke browser.

Selama run berjalan, setiap langkah dicatat oleh komponen **Tracer**. Setiap langkah ditulis sebagai satu baris di tabel `steps` di SQLite, dengan jenis langkah, input, output, durasi, dan jumlah token, lalu disiarkan sebagai event `step_started` dan `step`. Saat model AI sedang berpikir, potongan teksnya dikirim sebagai event `llm_delta`, sehingga proses berpikir model tampil di layar kata demi kata. Saat run selesai, event `run_finished` dikirim. Inilah yang membuat agen ini transparan: setiap keputusan bisa ditelusuri.

## 5. Mesin agen: graf LangGraph

Ketika giliran job tiba, worker menjalankan mesin agen. Mesin ini disusun dengan **LangGraph** sebagai **StateGraph**, yaitu graf berarah. Setiap simpulnya (node) adalah satu fungsi Python dengan satu tanggung jawab, dan setiap sisinya (edge) menentukan langkah berikutnya. Beberapa edge bersifat **kondisional**: fungsi router membaca keadaan saat itu, lalu memilih jalur.

Semua node berbagi satu objek **state**: jenis input, isi pesan, daftar pesan percakapan, hasil pembacaan struk, nomor langkah, status, dan jawaban. Setiap node membaca state, mengerjakan tugasnya, lalu mengembalikan perubahan pada state. Graf ini dikompilasi bersama **checkpointer SQLite**, sehingga state bisa disimpan dan dilanjutkan kembali. Fungsi ini nanti dipakai untuk konfirmasi. Seluruh telemetri cloud seperti LangSmith dimatikan di kode, jadi tidak ada data yang keluar dari laptop.

## 6. Mengenali jenis input

Node pertama mencatat input ke trace, lalu router memeriksa field `input_type` dan memilih satu dari tiga jalur: teks, suara, atau struk. Langkah ini setara dengan penyortiran: setiap jenis data butuh pra-pemrosesan berbeda sebelum bisa dipahami model bahasa.

Untuk **teks**, tidak ada pra-pemrosesan. Isi pesan langsung menjadi masukan bagi agen.

Untuk **suara**, file audio didekode menjadi gelombang suara, lalu ditranskripsi oleh **faster-whisper** model `small`, implementasi Whisper berbasis CTranslate2 yang berjalan di GPU dan otomatis pindah ke CPU kalau GPU tidak tersedia. Hasil transkripsi diberi label "hasil transkripsi otomatis, bisa ada salah dengar" sebelum diteruskan. Label ini penting: ia memberi tahu model bahasa bahwa teksnya berpotensi keliru, sehingga model lebih condong bertanya daripada menebak.

## 7. Pipeline struk: OCR, aturan, validasi, cadangan

Jalur struk adalah pipeline berlapis yang dirancang supaya cepat, tetapi tetap bisa dipercaya.

**Deduplikasi.** Isi file gambar di-hash dengan **SHA-256**. Kalau hash yang sama pernah diproses, graf langsung melompat ke akhir dengan hasil `duplicate`, dalam beberapa milidetik dan tanpa memanggil model AI. Tujuannya mencegah transaksi ganda akibat unggahan berulang.

**OCR.** Gambar diperkecil (sisi terpanjang maksimal 1600 piksel), lalu dibaca oleh **RapidOCR**, yaitu model deteksi dan pengenalan teks PaddleOCR PP-OCRv4 yang dijalankan dengan ONNX Runtime di **CPU**. OCR sengaja dipindah ke CPU supaya tidak berebut VRAM dengan model bahasa. Keluarannya adalah kotak-kotak teks beserta koordinat (*bounding box*) dan skor keyakinan. Kotak-kotak itu dikelompokkan menjadi baris, dan sudut kemiringan foto dihitung serta dikoreksi. Prosesnya sekitar satu detik. Koordinat bounding box juga dikirim ke web app, sehingga kotak bacaan bisa digambar di atas foto.

**Parser aturan.** Teks OCR diproses dengan aturan berbasis **regular expression**. Pola angka rupiah dikenali sebagai nominal, pola tanggal dinormalisasi ke format `YYYY-MM-DD`, baris dengan kata kunci seperti "TOTAL" diberi peringkat sebagai kandidat total, dan baris teratas dijadikan kandidat nama toko. Untuk struk yang rapi, cara deterministik ini sudah cukup dan tidak butuh model AI sama sekali, sehingga ekstraksi selesai dalam kurang dari satu detik. Mode ini diatur oleh `RECEIPT_RULES_FIRST=true`.

**Validasi.** Hasil ekstraksi, dari mana pun asalnya, diperiksa dengan lima uji terhadap teks OCR:
1. Total lebih dari nol.
2. Angka total benar-benar muncul di teks OCR.
3. Tanggal benar-benar muncul di teks OCR.
4. Jumlah subtotal item dikurangi diskon dan ditambah pajak sama dengan total, dengan toleransi 1% atau minimal Rp100.
5. Rata-rata keyakinan OCR minimal 0,80 dengan sedikitnya tiga baris terbaca.

Prinsipnya: keluaran model tidak pernah diterima tanpa diperiksa oleh kode deterministik. Validasi ini menangkap **halusinasi**, misalnya model "membaca" angka yang sebenarnya tidak ada di struk.

**Cadangan bertingkat.** Kalau parser aturan gagal validasi, teks OCR diberikan ke model bahasa dalam mode teks saja, tanpa thinking, dengan **skema JSON** yang memaksa keluaran terstruktur (toko, tanggal, item, diskon, pajak, total, kategori). Hasilnya divalidasi ulang. Kalau masih gagal, sistem memakai cadangan terakhir: gambar dikirim langsung ke model dalam mode **vision**, karena Qwen 3.5 bisa membaca gambar. Urutannya disusun dari yang paling murah ke yang paling mahal: aturan sekitar 1 detik, model teks sekitar 13 detik, vision paling teliti tapi paling berat.

**Deteksi struk serupa.** Sebelum diteruskan, database diperiksa: apakah sudah ada transaksi dari struk dengan nominal dan tanggal yang sama, dan nama toko yang sama setelah spasi dinormalisasi? Kalau ada, run berhenti dengan status `awaiting_confirmation` dan pengguna ditanya apakah ini memang pembelian berbeda. Ini lapisan anti-dobel kedua, untuk kasus foto berbeda dari struk yang sama.

## 8. Konteks percakapan

Semua jalur bertemu di node konteks. Di sini riwayat pesan dengan `conversation_id` yang sama dimuat dari database, supaya model memahami pesan yang merujuk ke obrolan sebelumnya. Kalau pesan ini adalah jawaban atas pertanyaan konfirmasi, konteks dari run sebelumnya ikut diwarisi, misalnya data struk yang tertunda dan tanggal default.

## 9. Inti agen: model bahasa dan tool calling

Node agen adalah inti sistem. Ia menyusun daftar pesan yang diawali **system prompt**, yaitu instruksi kerja berisi tanggal hari ini, aturan konversi nominal ("30rb" = 30000, "4,5 juta" = 4500000), aturan tanggal relatif ("kemarin", "tadi pagi"), daftar 12 kategori beserta jenisnya (pemasukan atau pengeluaran), dan aturan perilaku. Aturan perilaku yang paling penting: data hanya tersimpan lewat tool, dan kalau ragu, model wajib bertanya.

Daftar pesan itu dikirim ke model **qwen3.5:9b** yang dilayani **Ollama** di GPU, lewat `ChatOllama` dari LangChain. Model menerima deskripsi enam **tool** dalam format JSON Schema: `catat_transaksi`, `ubah_transaksi`, `hapus_transaksi`, `cari_transaksi`, `ringkasan`, dan `minta_konfirmasi`. Mode **thinking** aktif, jadi model menghasilkan penalaran terlebih dahulu. Penalaran itu dipisahkan dari jawaban, disimpan sebagai langkah `reasoning`, dan dialirkan live ke web app.

Keluaran model ada dua kemungkinan: **tool call** (permintaan menjalankan tool dengan argumen tertentu) atau jawaban teks biasa. Router memeriksa keluaran itu. Kalau ada tool call, graf menuju node tools. Kalau tidak, graf menuju node penjaga.

## 10. Eksekusi tool: kode deterministik sebagai penentu

Node tools menjalankan setiap tool call. Di sinilah batas tegas antara AI dan kode biasa. Model hanya **mengusulkan** argumen, sedangkan kode Python yang **memutuskan** apakah argumen itu sah. Nominal di-parse ulang menjadi bilangan bulat, tanggal dinormalisasi, kategori dicek terhadap daftar resmi dan dicocokkan dengan jenis transaksinya, lalu data ditulis ke tabel `transactions`. Penghapusan dilakukan sebagai **soft delete**: baris hanya ditandai terhapus, sehingga jejaknya tetap bisa diaudit. Pertanyaan seperti "pengeluaran bulan ini berapa?" dijawab oleh tool `ringkasan` yang menjalankan agregasi SQL, sehingga angka yang disampaikan berasal dari database, bukan dari ingatan model.

Hasil setiap tool dikembalikan ke model sebagai pesan berperan `tool`, dan graf kembali ke node agen. Putaran agen dan tools ini adalah pola **ReAct**: model bernalar, bertindak, mengamati hasil, lalu bernalar lagi. Contohnya, untuk "hapus catatan ngopi tadi", model lebih dulu memanggil `cari_transaksi` untuk menemukan ID-nya, baru kemudian `hapus_transaksi`. Putaran dibatasi enam langkah (`AGENT_MAX_STEPS`), dan setiap run dibatasi 600 detik, supaya agen tidak terjebak dalam loop tanpa akhir.

## 11. Penjaga: mengoreksi perilaku model

Model berukuran 9 miliar parameter kadang berperilaku keliru, dan node penjaga (guard) menangani dua kasus yang paling sering terjadi.

Kasus pertama, model **mengklaim** "sudah dicatat" padahal tidak ada tool tulis yang berhasil dijalankan. Klaim seperti itu dideteksi dengan pola teks. Graf lalu menyuntikkan pesan koreksi dan mengembalikan alur ke node agen, supaya model benar-benar memanggil tool.

Kasus kedua, model mengakhiri gilirannya hanya dengan penalaran, **tanpa jawaban**. Penjaga lalu menyusun jawaban otomatis dari hasil tool yang sudah berhasil.

Setiap koreksi dicatat sebagai langkah `guard` di trace, sehingga terlihat kapan dan mengapa terjadi.

## 12. Penyelesaian dan konfirmasi: interrupt dan checkpoint

Node selesai menentukan hasil akhir run. **Status** menjelaskan keadaan run (`done`, `awaiting_confirmation`, atau `done_with_warnings` kalau ada tool tulis yang gagal). **Outcome** menjelaskan hasil bagi data (`recorded` kalau ada transaksi baru, `modified` kalau ada yang diubah atau dihapus, `answered` kalau hanya menjawab pertanyaan, `duplicate` kalau ditolak sebagai dobel).

Kalau model memanggil `minta_konfirmasi`, misalnya karena pesan "tadi bayar 50" ambigu, graf menuju node tunggu-jawaban yang memanggil fungsi **`interrupt()`** dari LangGraph. Eksekusi graf berhenti, dan seluruh state disimpan oleh **checkpointer** ke file SQLite tersendiri (`finance_langgraph.db`), ditandai dengan ID thread percakapan. Run ditutup dengan status `awaiting_confirmation`, dan worker bebas memproses job lain.

Ketika pengguna menjawab "50 ribu, parkir motor" dengan `conversation_id` yang sama, server membuat run baru dan melanjutkan thread graf yang tersimpan lewat `Command(resume=...)`. Graf **tidak mulai dari awal**: ia melanjutkan tepat dari titik interrupt dengan state lengkap, lalu kembali ke node agen yang kini punya informasi cukup untuk mencatat. Kedua run dihubungkan lewat field `answers_run_id` dan `answered_by_run_id`, sehingga web app bisa menampilkan pertanyaan dan jawabannya sebagai satu rangkaian.

## 13. Kembali ke pengguna

Setelah graf selesai, baris run di database diperbarui dengan status, outcome, jawaban akhir, durasi, dan total token, lalu event `run_finished` dikirim lewat SSE. Web app menampilkan jawaban, kartu transaksi yang tercatat, dan tabel trace lengkap. Setiap baris trace bisa dibuka untuk melihat prompt yang dikirim ke model, penalarannya, argumen dan hasil tool, sampai bounding box OCR di atas foto struk.

## 14. Observabilitas dan ketahanan

Endpoint `/v1/metrics` membaca `nvidia-smi` dan statistik internal untuk menampilkan pemakaian VRAM, kecepatan model dalam token per detik, panjang antrean, dan jumlah run per status. Data ini ditampilkan di halaman Sistem web app. Endpoint `/v1/agent/graph` mengembalikan diagram graf dalam format Mermaid.

Untuk ketahanan, ada rencana cadangan. Mesin agen versi pertama, sebuah loop buatan sendiri, masih tersedia dan bisa diaktifkan kembali dengan `AGENT_ENGINE=custom`, tanpa mengubah API, database, maupun web app.

## Penutup

Kalau dirangkum, sistem ini membagi peran dengan tegas:
- **Web app dan proxy** mengurus antarmuka dan menyembunyikan kunci.
- **FastAPI** menjaga gerbang dan mengatur antrean GPU.
- **LangGraph** mengatur alur kerja dan bisa berhenti lalu melanjutkan.
- **Whisper dan OCR** mengubah suara dan gambar menjadi teks.
- **Model bahasa** bernalar dan memilih tindakan.
- **Kode deterministik** memvalidasi setiap angka sebelum disimpan.
- **SQLite** menyimpan transaksi, jejak kerja, dan checkpoint.

Semuanya berjalan di satu laptop, tanpa data yang keluar ke cloud.
