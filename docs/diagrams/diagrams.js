// Konfigurasi diagram dokumen ringkas. Kanvas 880 px = 15,5 cm; 20 px = 10 pt saat dicetak.
// Warna: biru = pengguna/web app, hijau = AI, abu = penyimpanan, oranye = pemeriksaan oleh program.
// Render: node diagrams/render.mjs  (hasil: docs/img/diagram-<nama>.png)
const DIAGRAMS = {
  // Gambar 1: arsitektur sistem
  arsitektur: {
    W: 880, H: 420,
    groups: [{ x: 152, y: 0, w: 728, h: 420, title: "💻 Satu laptop", sub: "· data tidak keluar", titleBottom: true }],
    boxes: [
      { id: "user", x: 0, y: 30, w: 136, h: 150, cls: "blue", icon: "🧑", title: "Pengguna" },
      { id: "web", x: 170, y: 30, w: 145, h: 150, cls: "blue", icon: "🌐", title: "Web app", sub: "kirim & lihat" },
      { id: "srv", x: 345, y: 30, w: 150, h: 150, cls: "gray", icon: "⚙️", title: "Server", sub: "kunci & antrean" },
      { id: "agen", x: 525, y: 30, w: 150, h: 150, cls: "green", icon: "🤖", title: "Agen AI", sub: "LangGraph" },
      { id: "db", x: 705, y: 30, w: 160, h: 150, cls: "gray", icon: "🗂️", title: "Catatan", sub: "SQLite" },
      { id: "llm", x: 180, y: 250, w: 215, h: 105, cls: "green", title: "Model AI", sub: "Ollama, GPU" },
      { id: "stt", x: 415, y: 250, w: 215, h: 105, cls: "green", title: "Suara jadi teks", sub: "Whisper, GPU" },
      { id: "ocr", x: 650, y: 250, w: 215, h: 105, cls: "green", title: "Baca struk", sub: "OCR, CPU" },
    ],
    links: [
      { from: "user", fs: "r", to: "web", ts: "l" },
      { from: "web", fs: "r", to: "srv", ts: "l" },
      { from: "srv", fs: "r", to: "agen", ts: "l" },
      { from: "agen", fs: "r", to: "db", ts: "l" },
      { from: "agen", fs: "b", to: "llm", ts: "t", my: 215 },
      { from: "agen", fs: "b", to: "stt", ts: "t", my: 215 },
      { from: "agen", fs: "b", to: "ocr", ts: "t", my: 215 },
    ],
  },

  // Gambar 2: alur mencatat (berlaku untuk semua jenis masukan)
  alur: {
    W: 880, H: 330,
    boxes: [
      { id: "in", x: 0, y: 0, w: 152, h: 150, cls: "blue", icon: "📨", title: "Kirim", sub: "teks, foto, suara" },
      { id: "teks", x: 182, y: 0, w: 152, h: 150, cls: "green", icon: "📝", title: "Jadi teks", sub: "foto & suara" },
      { id: "ai", x: 364, y: 0, w: 152, h: 150, cls: "green", icon: "🤖", title: "AI paham", sub: "jenis & nominal" },
      { id: "cek", x: 546, y: 0, w: 152, h: 150, cls: "amber", icon: "🧮", title: "Cek angka", sub: "oleh program" },
      { id: "simpan", x: 728, y: 0, w: 152, h: 150, cls: "gray", icon: "🗂️", title: "Simpan", sub: "ke catatan" },
      { id: "tanya", x: 546, y: 225, w: 334, h: 100, cls: "blue row c", icon: "❓", title: "Tanya pengguna" },
    ],
    links: [
      { from: "in", fs: "r", to: "teks", ts: "l" },
      { from: "teks", fs: "r", to: "ai", ts: "l" },
      { from: "ai", fs: "r", to: "cek", ts: "l" },
      { from: "cek", fs: "r", to: "simpan", ts: "l" },
      { from: "cek", fs: "b", to: "tanya", ts: "t", too: -91, label: "ragu" },
      { from: "tanya", fs: "t", fo: 91, to: "simpan", ts: "b", label: "dijawab" },
    ],
  },
  // ===================== Diagram untuk srs-cara-kerja.md (pembaca pemula) =====================
  // SRS Gambar: arsitektur lengkap (satu laptop + link publik)
  "srs-arsitektur": {
    W: 880, H: 560,
    groups: [{ x: 196, y: 0, w: 684, h: 560, title: "💻 Satu laptop", sub: "· semua data tetap di sini", titleBottom: true }],
    boxes: [
      { id: "user", x: 0, y: 20, w: 160, h: 140, cls: "blue", icon: "🧑", title: "Pengguna", sub: "browser" },
      { id: "teman", x: 0, y: 250, w: 160, h: 160, cls: "ghost", icon: "🧑‍🤝‍🧑", title: "Teman", sub: "link publik Cloudflare Tunnel" },
      { id: "web", x: 222, y: 20, w: 190, h: 140, cls: "blue", icon: "🌐", title: "Web app", sub: "React, sisip kunci" },
      { id: "srv", x: 443, y: 20, w: 190, h: 140, cls: "gray", icon: "⚙️", title: "Server", sub: "FastAPI, antrean" },
      { id: "agen", x: 664, y: 20, w: 190, h: 140, cls: "green", icon: "🤖", title: "Agen AI", sub: "LangGraph" },
      { id: "llm", x: 222, y: 250, w: 200, h: 105, cls: "green", title: "Model AI", sub: "Qwen lewat Ollama, GPU" },
      { id: "stt", x: 443, y: 250, w: 190, h: 105, cls: "green", title: "Suara → teks", sub: "Whisper, GPU" },
      { id: "ocr", x: 664, y: 250, w: 190, h: 105, cls: "green", title: "Baca struk", sub: "OCR, CPU" },
      { id: "db", x: 443, y: 410, w: 411, h: 100, cls: "gray row c", icon: "🗂️", title: "SQLite", sub: "transaksi, jejak kerja, titik simpan" },
    ],
    links: [
      { from: "user", fs: "r", to: "web", ts: "l", too: -25, fo: -25 },
      { from: "teman", fs: "r", to: "web", ts: "l", dash: true, mx: 186, too: 35 },
      { from: "web", fs: "r", to: "srv", ts: "l" },
      { from: "srv", fs: "r", to: "agen", ts: "l" },
      { from: "agen", fs: "b", to: "llm", ts: "t", my: 210, fo: -50 },
      { from: "agen", fs: "b", to: "stt", ts: "t", my: 210, fo: -50 },
      { from: "agen", fs: "b", to: "ocr", ts: "t" },
      { from: "agen", fs: "r", to: "db", ts: "r", mx: 868, fo: 40 },
    ],
  },


  // SRS Gambar: perjalanan satu permintaan
  "srs-perjalanan": {
    W: 880, H: 400,
    boxes: [
      { id: "s1", x: 0, y: 0, w: 190, h: 150, cls: "blue", icon: "📨", title: "1. Kirim", sub: "teks, foto, suara" },
      { id: "s2", x: 230, y: 0, w: 190, h: 150, cls: "blue", icon: "🔑", title: "2. Web app", sub: "tempel kunci" },
      { id: "s3", x: 460, y: 0, w: 190, h: 150, cls: "amber", icon: "🚪", title: "3. Gerbang", sub: "cek kunci & isi" },
      { id: "s4", x: 690, y: 0, w: 190, h: 150, cls: "gray", icon: "🎫", title: "4. Antrean", sub: "dapat tiket" },
      { id: "s5", x: 690, y: 240, w: 190, h: 150, cls: "green", icon: "🤖", title: "5. Agen kerja", sub: "giliran GPU tiba" },
      { id: "s6", x: 460, y: 240, w: 190, h: 150, cls: "gray", icon: "📝", title: "6. Catat jejak", sub: "tiap langkah" },
      { id: "s7", x: 230, y: 240, w: 190, h: 150, cls: "blue", icon: "📡", title: "7. Siaran live", sub: "ke layar" },
      { id: "s8", x: 0, y: 240, w: 190, h: 150, cls: "blue", icon: "✅", title: "8. Jawaban", sub: "tampil" },
    ],
    links: [
      { from: "s1", fs: "r", to: "s2", ts: "l" },
      { from: "s2", fs: "r", to: "s3", ts: "l" },
      { from: "s3", fs: "r", to: "s4", ts: "l" },
      { from: "s4", fs: "b", to: "s5", ts: "t" },
      { from: "s5", fs: "l", to: "s6", ts: "r" },
      { from: "s6", fs: "l", to: "s7", ts: "r" },
      { from: "s7", fs: "l", to: "s8", ts: "r" },
    ],
  },


  // SRS Gambar: pipeline struk berlapis (murah dulu, mahal belakangan)
  "srs-struk": {
    W: 880, H: 900,
    boxes: [
      { id: "foto", x: 0, y: 0, w: 180, h: 100, cls: "blue", icon: "📷", title: "Foto struk" },
      { id: "sidik", x: 240, y: 0, w: 220, h: 100, cls: "amber", icon: "🔎", title: "Sidik jari", sub: "pernah dikirim?" },
      { id: "stop", x: 580, y: 0, w: 300, h: 100, cls: "gray", icon: "⛔", title: "Berhenti", sub: "foto sama, tidak dicatat" },
      { id: "ocr", x: 240, y: 160, w: 220, h: 100, cls: "green", icon: "🔤", title: "Baca teks (OCR)", sub: "di CPU, ±1 detik" },
      { id: "atur", x: 0, y: 330, w: 210, h: 100, cls: "amber", title: "Cara 1: aturan", sub: "±1 detik, tanpa AI" },
      { id: "cek1", x: 250, y: 330, w: 200, h: 100, cls: "amber", icon: "🧮", title: "Cek 5 hal" },
      { id: "llm", x: 250, y: 480, w: 200, h: 100, cls: "green", title: "Cara 2: AI teks", sub: "±13 detik" },
      { id: "cek2", x: 490, y: 480, w: 180, h: 100, cls: "amber", icon: "🧮", title: "Cek 5 hal" },
      { id: "vis", x: 490, y: 630, w: 180, h: 100, cls: "green", title: "Cara 3: AI lihat foto", sub: "paling berat" },
      { id: "hasil", x: 730, y: 330, w: 150, h: 400, cls: "gray", icon: "📄", title: "Data struk", sub: "toko, tanggal, barang, total" },
      { id: "mirip", x: 300, y: 790, w: 280, h: 100, cls: "amber", icon: "📋", title: "Mirip catatan lama?", sub: "toko + tanggal + total" },
      { id: "agen", x: 0, y: 790, w: 220, h: 100, cls: "green", icon: "🤖", title: "Ke agen", sub: "untuk dicatat" },
      { id: "tanya", x: 660, y: 790, w: 220, h: 100, cls: "blue", icon: "❓", title: "Tanya dulu", sub: "ke pengguna" },
    ],
    links: [
      { from: "foto", fs: "r", to: "sidik", ts: "l" },
      { from: "sidik", fs: "r", to: "stop", ts: "l", label: "sudah pernah" },
      { from: "sidik", fs: "b", to: "ocr", ts: "t", label: "baru" },
      { from: "ocr", fs: "b", to: "atur", ts: "t", my: 295 },
      { from: "atur", fs: "r", to: "cek1", ts: "l" },
      { from: "cek1", fs: "r", to: "hasil", ts: "l", too: -150, label: "lolos" },
      { from: "cek1", fs: "b", to: "llm", ts: "t", label: "gagal" },
      { from: "llm", fs: "r", to: "cek2", ts: "l" },
      { from: "cek2", fs: "r", to: "hasil", ts: "l", label: "lolos", lx: 700, ly: 506 },
      { from: "cek2", fs: "b", to: "vis", ts: "t", label: "gagal" },
      { from: "vis", fs: "r", to: "hasil", ts: "l", too: 150 },
      { from: "hasil", fs: "b", to: "mirip", ts: "t", my: 760 },
      { from: "mirip", fs: "l", to: "agen", ts: "r", label: "tidak" },
      { from: "mirip", fs: "r", to: "tanya", ts: "l", label: "ya" },
    ],
  },

  // SRS Gambar: graf agen versi pemula
  "srs-graf": {
    W: 880, H: 900,
    boxes: [
      { id: "masuk", x: 300, y: 0, w: 220, h: 85, cls: "gray", title: "Masuk", sub: "cek jenis input" },
      { id: "teks", x: 0, y: 140, w: 220, h: 85, cls: "blue", title: "Teks", sub: "langsung lanjut" },
      { id: "suara", x: 300, y: 140, w: 220, h: 85, cls: "green", title: "Suara", sub: "Whisper → teks" },
      { id: "struk", x: 600, y: 140, w: 220, h: 85, cls: "green", title: "Struk", sub: "jalur berlapis" },
      { id: "konteks", x: 300, y: 275, w: 220, h: 85, cls: "gray", title: "Konteks", sub: "ingat obrolan" },
      { id: "agen", x: 300, y: 410, w: 220, h: 95, cls: "green", title: "Agen", sub: "model AI berpikir" },
      { id: "alat", x: 640, y: 410, w: 180, h: 95, cls: "amber", title: "Alat", sub: "6 alat" },
      { id: "jaga", x: 300, y: 570, w: 220, h: 85, cls: "amber", title: "Penjaga", sub: "cek klaim palsu" },
      { id: "selesai", x: 300, y: 705, w: 220, h: 85, cls: "gray", title: "Selesai", sub: "tentukan hasil" },
      { id: "tunggu", x: 0, y: 688, w: 175, h: 118, cls: "blue", title: "Tunggu jawaban", sub: "simpan, berhenti" },
      { id: "akhir", x: 300, y: 830, w: 220, h: 70, cls: "blue oval", title: "Jawaban tampil" },
    ],
    links: [
      { from: "masuk", fs: "b", to: "teks", ts: "t", my: 112 },
      { from: "masuk", fs: "b", to: "suara", ts: "t" },
      { from: "masuk", fs: "b", to: "struk", ts: "t", my: 112 },
      { from: "teks", fs: "b", to: "konteks", ts: "t", my: 250, too: -60 },
      { from: "suara", fs: "b", to: "konteks", ts: "t" },
      { from: "struk", fs: "b", to: "konteks", ts: "t", my: 250, too: 60, fo: -40 },
      { from: "struk", fs: "r", to: "selesai", ts: "r", mx: 862, label: "dobel /<br>mirip", lx: 852, ly: 320, lstyle: { transform: "translate(-100%,-50%)" } },
      { from: "konteks", fs: "b", to: "agen", ts: "t" },
      { from: "agen", fs: "r", to: "alat", ts: "l", fo: -24, too: -24, label: "pakai alat", dy: -22 },
      { from: "alat", fs: "l", to: "agen", ts: "r", fo: 24, too: 24, label: "lanjut", dy: 22 },
      { from: "agen", fs: "b", to: "jaga", ts: "t", fo: 50, too: 50, label: "jawab teks", lx: 446, ly: 538, lstyle: { transform: "translate(0,-50%)" } },
      { from: "jaga", fs: "t", to: "agen", ts: "b", fo: -50, too: -50, label: "ditegur 1×", lx: 354, ly: 538, lstyle: { transform: "translate(-100%,-50%)" } },
      { from: "alat", fs: "b", to: "selesai", ts: "t", my: 682, too: 70, label: "konfirmasi /<br>6 langkah", lx: 730, ly: 600 },
      { from: "jaga", fs: "b", to: "selesai", ts: "t", fo: -30, too: -30, label: "beres", dx: 40 },
      { from: "selesai", fs: "l", to: "tunggu", ts: "r", label: "perlu tanya", dy: -18 },
      { from: "tunggu", fs: "t", to: "agen", ts: "l", dash: true, label: "pengguna<br>menjawab", lx: 88, ly: 590 },
      { from: "selesai", fs: "b", to: "akhir", ts: "t" },
    ],
  },

  // SRS Gambar: konfirmasi = berhenti, simpan, lanjut
  "srs-konfirmasi": {
    W: 880, H: 480,
    boxes: [
      { id: "a1", x: 0, y: 40, w: 190, h: 150, cls: "blue", icon: "💬", title: "“tadi bayar 50”" },
      { id: "a2", x: 230, y: 40, w: 190, h: 150, cls: "green", icon: "🤔", title: "Agen ragu", sub: "Rp50 atau 50rb?" },
      { id: "a3", x: 460, y: 40, w: 190, h: 150, cls: "blue", icon: "❓", title: "Bertanya", sub: "belum mencatat" },
      { id: "a4", x: 690, y: 40, w: 190, h: 150, cls: "gray", icon: "💾", title: "Simpan & berhenti" },
      { id: "b1", x: 0, y: 330, w: 190, h: 150, cls: "blue", icon: "💬", title: "“50 ribu, parkir”" },
      { id: "b2", x: 230, y: 330, w: 190, h: 150, cls: "gray", icon: "📂", title: "Buka simpanan" },
      { id: "b3", x: 460, y: 330, w: 190, h: 150, cls: "green", icon: "🤖", title: "Agen lanjut", sub: "dari titik henti" },
      { id: "b4", x: 690, y: 330, w: 190, h: 150, cls: "gray", icon: "✅", title: "Tercatat", sub: "Rp50.000" },
    ],
    notes: [
      { x: 0, y: 2, w: 880, html: "<b>Permintaan pertama</b>" },
      { x: 360, y: 280, w: 520, html: "<b>Permintaan kedua</b> (percakapan sama)" },
      { x: 360, y: 214, w: 520, html: "⏸️ berhenti; GPU bebas untuk orang lain" },
    ],
    links: [
      { from: "a1", fs: "r", to: "a2", ts: "l" },
      { from: "a2", fs: "r", to: "a3", ts: "l" },
      { from: "a3", fs: "r", to: "a4", ts: "l" },
      { from: "a4", fs: "b", to: "b2", ts: "t", dash: true, my: 262 },
      { from: "b1", fs: "r", to: "b2", ts: "l" },
      { from: "b2", fs: "r", to: "b3", ts: "l" },
      { from: "b3", fs: "r", to: "b4", ts: "l" },
    ],
  },


  // SRS Gambar: pola ReAct (pikir - bertindak - amati)
  "srs-react": {
    W: 880, H: 330,
    boxes: [
      { id: "p", x: 0, y: 60, w: 200, h: 140, cls: "green", icon: "💭", title: "Pikir", sub: "apa langkahku?" },
      { id: "t", x: 340, y: 60, w: 200, h: 140, cls: "amber", icon: "🛠️", title: "Bertindak", sub: "panggil alat" },
      { id: "o", x: 680, y: 60, w: 200, h: 140, cls: "gray", icon: "👀", title: "Amati", sub: "baca hasil alat" },
      { id: "j", x: 340, y: 240, w: 260, h: 86, cls: "blue row c", icon: "💬", title: "Jawab pengguna" },
    ],
    links: [
      { from: "p", fs: "r", to: "t", ts: "l" },
      { from: "t", fs: "r", to: "o", ts: "l" },
      { from: "o", fs: "t", to: "p", ts: "t", my: 22, label: "ulangi (maksimal 6 langkah)" },
      { from: "p", fs: "b", to: "j", ts: "l", label: "sudah cukup", lx: 220, ly: 283 },
    ],
  },

};
