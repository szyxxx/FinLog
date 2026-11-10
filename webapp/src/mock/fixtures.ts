// Fixture mock. Sumber: (1) trace NYATA dari server/data/finance.db & bench_receipt.db (lihat
// scripts/build_fixtures.py → real.json), (2) skenario sintetis yang bentuknya mengikuti docs/api.md
// untuk jalur yang belum ada contohnya di database (konfirmasi, fallback vision, pertanyaan, error).
// Fitur email sudah dihapus: run & transaksi email dari database tidak dipakai di mock.
import realJson from "./real.json";
import type { RunSummary, Step, Tx } from "../api/types";

const real = realJson as unknown as {
  runs: RunSummary[];
  transactions: Tx[];
  categories: { name: string; kind: string; description: string }[];
  bench_fallback: { run_id: string; steps: Step[] };
  emails?: Record<string, string>;
  images: Record<string, string>;
  audio?: Record<string, string>;
};

export const CATEGORIES = real.categories;

/** Gambar struk contoh untuk run mock (dicocokkan dengan nama file asli). */
export const MOCK_IMAGES: Record<string, string> = {};
export const MOCK_AUDIO: Record<string, string> = {};

const finished = (r: RunSummary) => !["queued", "running"].includes(r.status);
const realRuns = real.runs.filter((r) => finished(r) && r.input_type !== "email");

// Prompt sistem asli agent (diambil dari step llm nyata) untuk dipakai ulang di skenario sintetis.
const realLlm = real.runs.flatMap((r) => r.steps ?? []).find((s) => s.type === "llm" && s.name === "agent_step_1");
const SYSTEM_MSG = realLlm?.input?.messages?.[0] ?? { role: "system", content: "Kamu adalah asisten pencatat keuangan pribadi." };
const TOOL_NAMES: string[] = realLlm?.input?.tools ?? [
  "catat_transaksi", "ubah_transaksi", "hapus_transaksi", "cari_transaksi", "ringkasan", "minta_konfirmasi",
];

let idSeq = 9000;
export const rid = (p: string) => `${p}_${Math.random().toString(16).slice(2, 14).padEnd(12, "0")}`;

interface StepSpec {
  type: string; name: string; status?: string; input?: any; output?: any; dur?: number | null;
  pt?: number; ct?: number;
}

/** Rangkai step dengan timestamp berurutan. */
function buildSteps(runId: string, t0: number, specs: StepSpec[]): Step[] {
  let t = t0;
  return specs.map((s, i) => {
    const dur = s.dur === undefined ? 1 : s.dur;
    const step: Step = {
      id: idSeq++, run_id: runId, seq: i + 1, type: s.type, name: s.name, status: s.status ?? "ok",
      input: s.input ?? null, output: s.output ?? null, started_at: +t.toFixed(3), duration_ms: dur,
      prompt_tokens: s.pt ?? null, completion_tokens: s.ct ?? null,
      tokens_per_second: s.ct && dur ? +((s.ct / dur) * 1000).toFixed(2) : null,
    };
    t += (dur ?? 2) / 1000;
    return step;
  });
}

function agentPair(n: number, seqOfReasoning: number, thinking: string, userMsgs: any[], out: { content: string; tool_calls?: any[] | null },
  thinkMs: number, wallMs: number, pt: number, ct: number): StepSpec[] {
  return [
    { type: "reasoning", name: `agent_step_${n}`, input: { llm_step: `agent_step_${n}`, think: true },
      output: { thinking, chars: thinking.length, time_to_first_token_ms: 640 }, dur: thinkMs },
    { type: "llm", name: `agent_step_${n}`, pt, ct, dur: wallMs,
      input: { model: "qwen3.5:9b", messages: [SYSTEM_MSG, ...userMsgs], tools: TOOL_NAMES, format: null,
        options: { think: true, temperature: 0.2, num_ctx: 8192, num_predict: 1024 } },
      output: { content: out.content, tool_calls: out.tool_calls ?? null, reasoning_step_seq: seqOfReasoning,
        thinking_chars: thinking.length, done_reason: "stop",
        timings_ms: { wall: wallMs, total: wallMs - 3, load: 2, prompt_eval: 640, eval: wallMs - 700, time_to_first_token: 690, thinking: thinkMs } } },
  ];
}

const call = (name: string, args: any) => ({ id: `call_${Math.random().toString(36).slice(2, 10)}`, function: { name, arguments: args } });

function runShell(p: Partial<RunSummary> & { id: string; input_type: RunSummary["input_type"] }, steps: Step[]): RunSummary {
  const llm = steps.filter((s) => s.type === "llm");
  const t0 = steps[0]?.started_at ?? Date.now() / 1000;
  const last = steps[steps.length - 1];
  const fin = (last?.started_at ?? t0) + (last?.duration_ms ?? 0) / 1000;
  return {
    job_id: rid("job"), conversation_id: rid("conv"), input_text: "", input_meta: null,
    status: "done", outcome: "recorded", final_answer: null, error: null, model: "qwen3.5:9b",
    prompt_tokens: llm.reduce((a, s) => a + (s.prompt_tokens ?? 0), 0),
    completion_tokens: llm.reduce((a, s) => a + (s.completion_tokens ?? 0), 0),
    llm_calls: llm.length, tool_calls: steps.filter((s) => s.type === "tool").length,
    created_at: t0 - 0.4, started_at: t0, finished_at: fin, duration_ms: Math.round((fin - t0) * 1000),
    transactions: [], steps, ...p,
  };
}

// ----------------------------------------------------------------- skenario sintetis

const DAY = 86400;
const NOW = Math.max(...real.runs.map((r) => r.created_at), 1794215000);

/** Konfirmasi: "tadi bayar 50" → minta_konfirmasi. */
export function confirmationRun(t0 = NOW - 3 * 3600, text = "tadi bayar 50"): RunSummary {
  const id = rid("run");
  const q = "Nominal 50 itu 50 ribu atau 50 rupiah? Dan untuk keperluan apa?";
  const opsi = ["Rp50.000", "Rp50"];
  const think = `Pengguna bilang "${text}". Nominal 50 ambigu: bisa 50 rupiah atau 50 ribu. Keperluannya juga tidak disebut, jadi kategori tidak bisa ditentukan. Sesuai aturan, bila data ambigu saya harus memanggil minta_konfirmasi, bukan menebak.`;
  const args = { pertanyaan: q, opsi, draf_transaksi: { jenis: "pengeluaran", jumlah: null, kategori: null } };
  const steps = buildSteps(id, t0, [
    { type: "input", name: "text", input: { text }, output: { conversation_id: "" }, dur: null },
    ...agentPair(1, 2, think, [{ role: "user", content: text }], { content: "", tool_calls: [call("minta_konfirmasi", args)] }, 9100, 13800, 2210, 168),
    { type: "tool", name: "minta_konfirmasi", input: args, output: { ok: true, menunggu_jawaban: true }, dur: 1 },
    { type: "confirmation", name: "minta_konfirmasi", input: null, output: { pertanyaan: q, opsi, draf_transaksi: args.draf_transaksi }, dur: null },
    { type: "final", name: "awaiting_confirmation", output: { answer: q, outcome: "needs_confirmation", warnings: [] }, dur: null },
  ]);
  const conv = rid("conv");
  steps[0].output.conversation_id = conv;
  return runShell({ id, input_type: "text", conversation_id: conv, input_text: text, input_meta: { text },
    status: "awaiting_confirmation", outcome: "needs_confirmation", final_answer: q }, steps);
}

/** Jawaban konfirmasi → lanjutan_konfirmasi → catat. */
export function confirmationReplyRun(prev: RunSummary, text: string, t0: number, txId: number): RunSummary {
  const id = rid("run");
  const amount = /rp?\s*50$/i.test(text.trim()) ? 50 : 50000;
  const args = { jenis: "pengeluaran", jumlah: amount, kategori: /parkir|bensin|ojek/i.test(text) ? "transportasi" : "lainnya_keluar", deskripsi: text.slice(0, 60) };
  const tx: Tx = { id: txId, kind: "pengeluaran", amount, category: args.kategori, description: args.deskripsi, merchant: null,
    date: new Date(t0 * 1000).toISOString().slice(0, 10), source: "text", items: null, run_id: id,
    created_at: new Date(t0 * 1000 + 30000).toISOString(), updated_at: new Date(t0 * 1000 + 30000).toISOString() };
  const ans = `Siap, saya catat pengeluaran Rp${amount.toLocaleString("id-ID")} (${args.kategori}).`;
  const hist = [{ role: "user", content: prev.input_text }, { role: "assistant", content: prev.final_answer }, { role: "user", content: text }];
  const think = `Ini jawaban atas pertanyaan konfirmasi sebelumnya. Pengguna menjawab "${text}". Jadi nominalnya ${amount} dan kategori ${args.kategori}. Saya panggil catat_transaksi.`;
  const steps = buildSteps(id, t0, [
    { type: "input", name: "text", input: { text }, output: { conversation_id: prev.conversation_id }, dur: null },
    { type: "context", name: "lanjutan_konfirmasi", input: { run_sebelumnya: prev.id },
      output: { sumber_transaksi: "text", run_asal: prev.id, tanggal_default: tx.date, jumlah_item: 0 }, dur: null },
    ...agentPair(1, 3, think, hist, { content: "", tool_calls: [call("catat_transaksi", args)] }, 7400, 12600, 2620, 151),
    { type: "tool", name: "catat_transaksi", input: args, output: { ok: true, transaksi: tx }, dur: 1 },
    ...agentPair(2, 6, "Transaksi tercatat, beri konfirmasi singkat.", [...hist, { role: "tool", tool_name: "catat_transaksi", content: JSON.stringify({ ok: true, transaksi: tx }) }],
      { content: ans }, 2100, 4800, 3010, 72),
    { type: "final", name: "done", output: { answer: ans, outcome: "recorded", warnings: [] }, dur: null },
  ]);
  return runShell({ id, input_type: "text", conversation_id: prev.conversation_id, input_text: text, input_meta: { text },
    status: "done", outcome: "recorded", final_answer: ans, transactions: [tx] }, steps);
}

/** Pertanyaan → ringkasan → answered. */
export function questionRun(t0 = NOW - 2 * 3600, text = "pengeluaran makan bulan ini berapa?", total = 40000): RunSummary {
  const id = rid("run");
  const args = { periode: "bulan_ini", kategori: "makanan_minuman" };
  const res = { ok: true, periode: "bulan_ini (2026-11-01 s/d 2026-11-09)", total_pemasukan: 0, total_pengeluaran: total, saldo: -total,
    per_kategori: [{ jenis: "pengeluaran", kategori: "makanan_minuman", jumlah_transaksi: 2, total }] };
  const ans = `Pengeluaran makanan & minuman bulan ini Rp${total.toLocaleString("id-ID")} dari 2 transaksi.`;
  const steps = buildSteps(id, t0, [
    { type: "input", name: "text", input: { text }, output: { conversation_id: "" }, dur: null },
    ...agentPair(1, 2, "Pengguna bertanya total pengeluaran kategori makanan_minuman bulan ini. Jangan mengarang angka, panggil tool ringkasan dengan periode bulan_ini dan kategori makanan_minuman.",
      [{ role: "user", content: text }], { content: "", tool_calls: [call("ringkasan", args)] }, 6800, 10900, 2190, 121),
    { type: "tool", name: "ringkasan", input: args, output: res, dur: 3 },
    ...agentPair(2, 5, "Hasil tool: total 40000 dari 2 transaksi. Jawab singkat.", [{ role: "user", content: text },
      { role: "tool", tool_name: "ringkasan", content: JSON.stringify(res) }], { content: ans }, 2300, 5200, 2840, 88),
    { type: "final", name: "done", output: { answer: ans, outcome: "answered", warnings: [] }, dur: null },
  ]);
  return runShell({ id, input_type: "text", input_text: text, input_meta: { text }, status: "done", outcome: "answered", final_answer: ans }, steps);
}

/** Struk dengan validasi gagal → fallback vision (step ekstraksi diambil dari benchmark nyata). */
export function fallbackReceiptRun(t0 = NOW - 5 * 3600, txId = 90, id = rid("run")): RunSummary {
  const bench = real.bench_fallback.steps;
  const vision = bench.find((s) => s.name === "ekstrak_struk_vision");
  let data: any = {};
  try { data = JSON.parse(vision?.output?.content ?? "{}"); } catch { /* biarkan kosong */ }
  data.total = data.total ?? 109250;
  data.sumber_ekstraksi = "vision_fallback";
  data.cek_konsistensi = { jumlah_subtotal: 95000, diskon: 0, pajak_layanan: data.pajak_layanan ?? 0, total: data.total, cocok: false };
  const ocr = bench.find((s) => s.type === "ocr");
  const tx: Tx = { id: txId, kind: "pengeluaran", amount: data.total, category: "makanan_minuman",
    description: "Nasi rendang, ayam pop, gulai tunjang, es teh", merchant: data.toko, date: data.tanggal ?? "2026-11-06",
    source: "receipt", items: data.items, run_id: id, created_at: "2026-11-09T11:20:41+07:00", updated_at: "2026-11-09T11:20:41+07:00" };
  const args = { jenis: "pengeluaran", jumlah: tx.amount, kategori: tx.category, deskripsi: tx.description, merchant: tx.merchant, tanggal: tx.date };
  const userMsg = `[Foto struk diunggah. Hasil ekstraksi struk (JSON, sumber: vision_fallback)]\n${JSON.stringify(data)}`;
  const ans = `Struk ${tx.merchant} tercatat sebagai pengeluaran Rp${tx.amount.toLocaleString("id-ID")} (makanan_minuman, ${tx.date}).`;
  const specs: StepSpec[] = [
    { type: "input", name: "receipt", input: { image_path: "data/uploads/receipt_resto.png", filename: "struk_resto_pajak.png", bytes: 39449, note: null }, output: { conversation_id: "" }, dur: null },
    { type: "dedup", name: "cek_duplikat", input: { kunci: "receipt:sha256:7be1c0a4…" }, output: { duplikat: false, run_sebelumnya: null, transaksi_sebelumnya: [] }, dur: null },
    ...bench.map((s) => ({ type: s.type, name: s.name, status: s.status, input: s.input, output: s.output, dur: s.duration_ms, pt: s.prompt_tokens ?? undefined, ct: s.completion_tokens ?? undefined })),
    { type: "preprocess", name: "ekstrak_struk", input: { file: "receipt_resto.png", bytes: 39449, receipt_mode: "ocr_llm", fallback_vision: true },
      output: { data, mode: "ocr_llm", jalur: ["ocr", "llm_teks", "vision"], fallback: { alasan: ["item_cocok_total"], hasil_ocr_llm: bench.find((s) => s.type === "fallback")?.input?.hasil_ocr_llm },
        validasi_lolos: false, ocr: { mean_conf: ocr?.output?.mean_conf, n_lines: ocr?.output?.lines?.length, ms: ocr?.duration_ms }, raw_output: vision?.output?.content, prompt: "(lihat step llm)", llm: [] },
      dur: bench.reduce((a, s) => a + (s.duration_ms ?? 0), 0) + 40 },
    { type: "dedup", name: "cek_struk_serupa", input: { toko: data.toko, tanggal: data.tanggal, total: data.total }, output: { serupa: false, transaksi_serupa: null }, dur: null },
    ...agentPair(1, 0, "Hasil ekstraksi dari fallback vision. Total 109250, toko RM Padang, tanggal 2026-11-06, kategori makanan_minuman. cek_konsistensi tidak cocok, tetapi selisihnya karena pajak layanan; keyakinan 0.95. Catat satu transaksi sebesar total struk.",
      [{ role: "user", content: userMsg }], { content: "", tool_calls: [call("catat_transaksi", args)] }, 14200, 21900, 2840, 236),
    { type: "tool", name: "catat_transaksi", input: args, output: { ok: true, transaksi: tx }, dur: 2 },
    ...agentPair(2, 0, "Sudah tercatat. Jawab singkat.", [{ role: "user", content: userMsg }, { role: "tool", tool_name: "catat_transaksi", content: JSON.stringify({ ok: true, transaksi: tx }) }],
      { content: ans }, 2600, 5900, 3320, 94),
    { type: "final", name: "done", output: { answer: ans, outcome: "recorded", warnings: [] }, dur: null },
  ];
  const steps = buildSteps(id, t0, specs);
  // perbaiki reasoning_step_seq
  steps.forEach((s, i) => { if (s.type === "llm" && s.output?.reasoning_step_seq === 0) s.output.reasoning_step_seq = steps[i - 1].seq; });
  MOCK_IMAGES[id] = "mock/struk_resto_pajak.png";
  return runShell({ id, input_type: "receipt", input_text: userMsg, input_meta: { filename: "struk_resto_pajak.png", bytes: 39449 },
    status: "done", outcome: "recorded", final_answer: ans, transactions: [tx] }, steps);
}

/** Run error (timeout) untuk contoh tampilan gagal. */
export function errorRun(t0 = NOW - 7 * 3600, id = rid("run")): RunSummary {
  const text = "rekap semua pengeluaran tahun ini per minggu dan bandingkan";
  const steps = buildSteps(id, t0, [
    { type: "input", name: "text", input: { text }, output: { conversation_id: "" }, dur: null },
    { type: "reasoning", name: "agent_step_1", input: { llm_step: "agent_step_1", think: true }, output: { thinking: "Pengguna minta rekap per minggu sepanjang tahun…", chars: 48, time_to_first_token_ms: 900 }, dur: 600000 },
    { type: "error", name: "TimeoutError", status: "error", output: { error: "timeout: run melebihi AGENT_RUN_TIMEOUT_S (600 s)" }, dur: null },
    { type: "final", name: "error", status: "error", output: { answer: null, outcome: "error", warnings: [] }, dur: null },
  ]);
  return runShell({ id, input_type: "text", input_text: text, input_meta: { text }, status: "error", outcome: "error",
    final_answer: null, error: "timeout: run melebihi AGENT_RUN_TIMEOUT_S (600 s)" }, steps);
}

// ----------------------------------------------------------------- seed

export function seed(): { runs: RunSummary[]; txs: Tx[] } {
  const runs: RunSummary[] = structuredClone(realRuns);
  Object.assign(MOCK_IMAGES, real.images ?? {});
  Object.assign(MOCK_AUDIO, real.audio ?? {});

  // Skenario sintetis hanya untuk jalur yang belum ada contoh nyatanya di database.
  const has = (f: (r: RunSummary) => boolean) => runs.some(f);
  const extra: RunSummary[] = [fallbackReceiptRun(NOW - 5 * 3600, 90, "run_mock_fallback"), errorRun(NOW - 7 * 3600, "run_mock_timeout")];
  if (!has((r) => r.outcome === "answered")) extra.push(questionRun());
  if (!has((r) => r.outcome === "needs_confirmation")) extra.push(confirmationRun());
  const all = [...runs, ...extra].sort((a, b) => b.created_at - a.created_at);

  const txs: Tx[] = structuredClone(real.transactions).filter((t) => t.source !== "email");
  for (const r of extra) for (const t of r.transactions ?? []) txs.push(t);
  // Riwayat transaksi beberapa minggu ke belakang agar grafik ringkasan berisi.
  const hist: [number, Tx["kind"], number, string, string, string | null][] = [
    [1, "pengeluaran", 22000, "makanan_minuman", "nasi padang", null],
    [2, "pengeluaran", 185000, "tagihan", "token listrik", "PLN"],
    [3, "pengeluaran", 64000, "hiburan", "nonton bioskop", "XXI"],
    [4, "pengeluaran", 35000, "transportasi", "ojek online", "Gojek"],
    [5, "pengeluaran", 120000, "kesehatan", "obat dan vitamin", "Apotek K-24"],
    [6, "pengeluaran", 18000, "makanan_minuman", "es kopi susu", "Kopi Kenangan"],
    [7, "pemasukan", 300000, "lainnya_masuk", "jual buku bekas", null],
    [8, "pengeluaran", 250000, "pendidikan", "fotokopi + buku kuliah", null],
    [9, "pengeluaran", 99000, "belanja", "kaos", "Tokopedia"],
    [10, "pengeluaran", 500000, "transfer_keluar", "kirim ke adik", null],
    [12, "pengeluaran", 42000, "makanan_minuman", "makan malam", null],
    [15, "pengeluaran", 30000, "transportasi", "bensin", "Pertamina"],
    [19, "pengeluaran", 75000, "tagihan", "paket data", "Telkomsel"],
  ];
  let tid = 50;
  const base = new Date("2026-11-09T12:00:00+07:00").getTime();
  for (const [d, kind, amount, category, description, merchant] of hist) {
    const date = new Date(base - d * DAY * 1000).toISOString().slice(0, 10);
    txs.push({ id: tid++, kind, amount, category, description, merchant, date, source: "manual", items: null, run_id: null,
      created_at: `${date}T12:00:00+07:00`, updated_at: `${date}T12:00:00+07:00` });
  }
  return { runs: all, txs };
}

export function templateFor(input: "text" | "receipt" | "voice"): RunSummary | undefined {
  // Utamakan run sederhana (tanpa guard/konteks) sebagai cetakan simulasi.
  const ok = realRuns.filter((r) => r.input_type === input && r.outcome === "recorded");
  return ok.find((r) => !(r.steps ?? []).some((s) => s.type === "guard" || s.type === "context")) ?? ok[0];
}

export function realRunWhere(f: (r: RunSummary) => boolean): RunSummary | undefined {
  return realRuns.find(f);
}
