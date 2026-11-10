// Klien mock: meniru server (antrean FIFO, run, SSE dengan llm_delta) tanpa jaringan dan tanpa LLM.
import type {
  ApiClient, Category, Health, InputResponse, InputType, Job, JobsResponse, Metrics, RunQuery,
  RunSummary, Step, StreamEvent, Summary, SystemInfo, Tx, TxList, TxQuery,
} from "../api/types";
import {
  CATEGORIES, MOCK_AUDIO, MOCK_IMAGES, confirmationReplyRun, confirmationRun, fallbackReceiptRun, questionRun,
  realRunWhere, rid, seed, templateFor,
} from "./fixtures";
import { periodRange } from "../lib/format";

type Listener = (e: StreamEvent) => void;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const now = () => Date.now() / 1000;

/** Kompresi waktu simulasi: run 40 detik diputar ±9 detik. */
const simMs = (ms: number | null | undefined) => Math.min(4000, Math.max(120, (ms ?? 0) * 0.22));

export class MockClient implements ApiClient {
  readonly mode = "mock" as const;
  private store: RunSummary[];
  private txs: Tx[];
  private jobsList: Job[] = [];
  private queue: { job: Job; run: RunSummary }[] = [];
  private current: Job | null = null;
  private runListeners = new Map<string, Set<Listener>>();
  private globalListeners = new Set<Listener>();
  private seenFiles = new Map<string, string>(); // nama file → run_id (anti-dobel mock)
  private nextTx = 200;
  private receiptToggle = 0;
  private tpsHistory: number[] = [];
  private workerOn = false;

  constructor() {
    const s = seed();
    this.store = s.runs;
    this.txs = s.txs;
    this.jobsList = this.store.map((r) => ({
      id: r.job_id, input_type: r.input_type, status: r.status === "error" ? "error" : "done", run_id: r.id,
      conversation_id: r.conversation_id, queue_position: null, payload: r.input_meta ?? null,
      created_at: r.created_at, started_at: r.started_at, finished_at: r.finished_at,
      wait_ms: Math.round(((r.started_at ?? r.created_at) - r.created_at) * 1000), run_ms: r.duration_ms, error: r.error,
    }));
    for (const r of this.store) {
      const fn = r.input_meta?.filename;
      if (fn && r.outcome !== "duplicate" && r.input_type !== "text") this.seenFiles.set(fn, r.id);
    }
  }

  // ------------------------------------------------------------ sistem
  async health(): Promise<Health> {
    await sleep(80);
    return { status: "ok", time: new Date().toISOString(), queue_length: this.queue.length + (this.current ? 1 : 0) };
  }

  async system(): Promise<SystemInfo> {
    await sleep(80);
    return {
      llm_model: "qwen3.5:9b", vision_model: "qwen3.5:9b", think: true, vision_think: false,
      receipt_mode: "ocr_llm", receipt_fallback_vision: true, receipt_rules_first: false,
      model_capabilities: { "qwen3.5:9b": ["completion", "vision", "tools", "thinking"] },
      whisper_model: "small", whisper_device: "cuda", categories: CATEGORIES,
      tools: [
        ["catat_transaksi", "Catat transaksi pemasukan/pengeluaran baru", { jenis: "string", jumlah: "integer", kategori: "string", deskripsi: "string", merchant: "string", tanggal: "string" }],
        ["ubah_transaksi", "Ubah transaksi berdasarkan id", { id: "integer", jumlah: "integer", kategori: "string" }],
        ["hapus_transaksi", "Hapus (soft delete) transaksi", { id: "integer" }],
        ["cari_transaksi", "Cari/filter transaksi", { kata_kunci: "string", periode: "string", kategori: "string" }],
        ["ringkasan", "Ringkasan per kategori dan periode", { periode: "string", kategori: "string" }],
        ["minta_konfirmasi", "Tanya balik ke pengguna bila data ambigu", { pertanyaan: "string", opsi: "array" }],
      ].map(([name, description, props]) => ({
        type: "function",
        function: { name: name as string, description: description as string,
          parameters: { type: "object", properties: Object.fromEntries(Object.entries(props as object).map(([k, t]) => [k, { type: t }])) } },
      })),
    };
  }

  async metrics(): Promise<Metrics> {
    await sleep(100);
    const done = this.store.filter((r) => r.duration_ms);
    const byType: Metrics["latency_by_input_type"] = {};
    for (const t of ["text", "receipt", "voice"] as InputType[]) {
      const ls = done.filter((r) => r.input_type === t && r.status !== "error").map((r) => r.duration_ms!).sort((a, b) => a - b);
      if (!ls.length) continue;
      const pick = (q: number) => ls[Math.min(ls.length - 1, Math.floor(q * ls.length))];
      byType[t] = { runs: ls.length, errors: done.filter((r) => r.input_type === t && r.status === "error").length,
        latency_ms: { avg: Math.round(ls.reduce((a, b) => a + b, 0) / ls.length), p50: pick(0.5), p95: pick(0.95), max: ls[ls.length - 1] },
        queue_wait_ms_avg: 1200 };
    }
    const llmSteps = this.store.flatMap((r) => r.steps ?? []).filter((s) => s.type === "llm" && s.tokens_per_second);
    const busy = !!this.current;
    const tps = busy ? 14 + Math.random() * 3 : 15.8 + Math.random() * 0.8;
    this.tpsHistory.push(tps);
    const status: Record<string, number> = {};
    for (const r of this.store) status[r.status] = (status[r.status] ?? 0) + 1;
    return {
      window_runs: this.store.length,
      queue: { length: this.queue.length + (busy ? 1 : 0), current_job: this.current },
      runs_by_status: status,
      latency_by_input_type: byType,
      llm: {
        calls: llmSteps.length, tokens_per_second_avg: +tps.toFixed(1), tokens_per_second_p50: 16.4,
        llm_step_ms_avg: Math.round(llmSteps.reduce((a, s) => a + (s.duration_ms ?? 0), 0) / Math.max(1, llmSteps.length)),
        prompt_tokens_total: this.store.reduce((a, r) => a + r.prompt_tokens, 0),
        completion_tokens_total: this.store.reduce((a, r) => a + r.completion_tokens, 0),
      },
      stt: { model: "small", device: "cuda", compute_type: "int8_float16", load_ms: 2076, real_time_factor_avg: 0.08 },
      ollama_loaded_models: [{ name: "qwen3.5:9b", size_vram_bytes: 5744749444, context_length: 8192, expires_at: new Date(Date.now() + 4 * 60000).toISOString() }],
      gpu: {
        available: true,
        gpus: [{ name: "NVIDIA GeForce RTX 5060 Laptop GPU", memory_used_mib: busy ? 7480 + Math.round(Math.random() * 120) : 6920 + Math.round(Math.random() * 60),
          memory_total_mib: 8151, utilization_pct: busy ? 88 + Math.round(Math.random() * 10) : Math.round(Math.random() * 6), temperature_c: busy ? 71 : 58, power_w: busy ? 74 : 14 }],
        processes: [{ pid: 4121, name: "/usr/local/bin/ollama", used_memory_mib: 5480 }, { pid: 4388, name: "python3", used_memory_mib: 610 }],
      },
    };
  }

  // ------------------------------------------------------------ input
  async chat(text: string, conversationId?: string | null) {
    if (!text.trim()) throw new Error("422: text: teks tidak boleh kosong");
    const t0 = now();
    const prev = conversationId ? this.store.find((r) => r.conversation_id === conversationId && r.status === "awaiting_confirmation") : undefined;
    let run: RunSummary;
    // Jawaban konfirmasi: bila database punya lanjutan nyata untuk percakapan ini, putar ulang itu.
    const follow = prev && realRunWhere((r) => r.conversation_id === prev.conversation_id && r.created_at > prev.created_at);
    if (prev && follow) {
      run = this.cloneRun(follow);
      run.conversation_id = prev.conversation_id;
      run.input_text = text;
      for (const s of run.steps ?? []) if (s.type === "input") s.input = { text };
    } else if (prev) run = confirmationReplyRun(prev, text, t0, this.nextTx++);
    else if (/\?|berapa|total|ringkas|rekap/i.test(text)) run = questionRun(t0, text, this.sumCat("makanan_minuman"));
    else if (/^\D*\b\d{1,3}\s*$/.test(text.trim()) || /bayar\s+\d{1,3}\s*$/i.test(text)) run = confirmationRun(t0, text);
    else run = this.textRun(text, t0);
    if (conversationId && !prev) run.conversation_id = conversationId;
    return this.submit("text", run, { text });
  }

  async receipt(file: File, note?: string) {
    const dup = this.seenFiles.get(file.name);
    let run: RunSummary;
    if (dup) run = this.duplicateRun("receipt", dup, `receipt:sha256:${file.name}`);
    else {
      const tpl = this.receiptToggle++ % 2 === 0 ? templateFor("receipt") : undefined;
      run = tpl ? this.cloneRun(tpl) : fallbackReceiptRun(now(), this.nextTx++);
      if (tpl) MOCK_IMAGES[run.id] = MOCK_IMAGES[tpl.id] ?? "mock/struk_serba_ada.png";
      this.seenFiles.set(file.name, run.id);
    }
    run.input_meta = { ...(run.input_meta ?? {}), filename: file.name, bytes: file.size, note: note ?? null };
    return this.submit("receipt", run, { filename: file.name, bytes: file.size, note: note ?? null });
  }

  async voice(file: Blob, filename: string) {
    const tpl = templateFor("voice");
    const run = tpl ? this.cloneRun(tpl) : this.textRun("beli bakso dua mangkok 25 ribu", now());
    run.input_type = "voice";
    run.input_meta = { filename, bytes: file.size };
    return this.submit("voice", run, { filename, bytes: file.size });
  }

  // ------------------------------------------------------------ antrean & trace
  async jobs(status?: string, limit = 50): Promise<JobsResponse> {
    await sleep(60);
    const list = [...this.jobsList].sort((a, b) => b.created_at - a.created_at).filter((j) => !status || j.status === status).slice(0, limit);
    return { queue_length: this.queue.length + (this.current ? 1 : 0), current_job: this.current, jobs: list.map((j) => this.jobInfo(j)) };
  }

  async runs(q: RunQuery = {}) {
    await sleep(60);
    return this.store
      .filter((r) => (!q.input_type || r.input_type === q.input_type) && (!q.status || r.status === q.status)
        && (!q.outcome || r.outcome === q.outcome) && (!q.conversation_id || r.conversation_id === q.conversation_id))
      .slice(q.offset ?? 0, (q.offset ?? 0) + (q.limit ?? 50))
      .map(({ steps: _s, ...rest }) => {
        // Seperti server: input_meta ringkas tanpa path.
        const m = rest.input_meta;
        const meta = rest.input_type === "text" ? null : { filename: m?.filename ?? null };
        return this.links({ ...(rest as RunSummary), input_meta: meta });
      });
  }

  async run(id: string) {
    await sleep(60);
    const r = this.store.find((x) => x.id === id);
    if (!r) throw new Error("404: run tidak ditemukan");
    return structuredClone(this.links(r));
  }

  streamRun(runId: string, onEvent: Listener) {
    let closed = false;
    const r = this.store.find((x) => x.id === runId);
    const set = this.runListeners.get(runId) ?? new Set();
    this.runListeners.set(runId, set);
    const fn: Listener = (e) => { if (!closed) onEvent(e); };
    queueMicrotask(() => {
      if (closed || !r) return;
      for (const s of r.steps ?? []) fn({ event: "step", data: structuredClone(s) });
      if (!["queued", "running"].includes(r.status)) {
        const { steps: _s, ...sum } = r;
        fn({ event: "run_finished", data: structuredClone(sum) as RunSummary });
      } else set.add(fn);
    });
    return () => { closed = true; set.delete(fn); };
  }

  streamAll(onEvent: Listener) {
    this.globalListeners.add(onEvent);
    queueMicrotask(() => onEvent({ event: "hello", data: { queue_length: this.queue.length } }));
    return () => { this.globalListeners.delete(onEvent); };
  }

  // ------------------------------------------------------------ transaksi
  async transactions(q: TxQuery = {}): Promise<TxList> {
    await sleep(60);
    const [from, to] = q.tanggal_mulai || q.tanggal_akhir ? [q.tanggal_mulai ?? "0000", q.tanggal_akhir ?? "9999"] : periodRange(q.periode ?? "semua");
    const kw = (q.kata_kunci ?? "").toLowerCase();
    const list = this.txs
      .filter((t) => t.date >= from && t.date <= to && (!q.jenis || t.kind === q.jenis) && (!q.kategori || t.category === q.kategori)
        && (!q.source || t.source === q.source)
        && (!kw || `${t.description} ${t.merchant} ${t.category}`.toLowerCase().includes(kw)))
      .sort((a, b) => b.date.localeCompare(a.date) || b.id - a.id)
      .slice(0, q.limit ?? 100);
    return { jumlah_hasil: list.length, total_nominal: list.reduce((a, t) => a + t.amount, 0), transaksi: structuredClone(list) };
  }

  async createTx(b: Record<string, any>) {
    await sleep(80);
    const ts = new Date().toISOString();
    const tx: Tx = { id: this.nextTx++, kind: b.jenis, amount: Number(b.jumlah), category: b.kategori, description: b.deskripsi ?? null,
      merchant: b.merchant ?? null, date: b.tanggal || ts.slice(0, 10), source: "manual", items: null, run_id: null, created_at: ts, updated_at: ts };
    this.txs.push(tx);
    return structuredClone(tx);
  }

  async updateTx(id: number, b: Record<string, any>) {
    await sleep(80);
    const t = this.txs.find((x) => x.id === id);
    if (!t) throw new Error("404: transaksi tidak ditemukan");
    const kind = b.jenis ?? t.kind;
    const cat = b.kategori ?? t.category;
    const known = CATEGORIES.find((c) => c.name === cat);
    if (!known) throw new Error(`400: kategori tidak dikenal: '${cat}'`);
    if (known.kind !== kind) throw new Error(`400: kategori '${cat}' tidak cocok dengan jenis '${kind}'; kirim jenis + kategori sekaligus`);
    if (!Object.keys(b).length) throw new Error("400: tidak ada field yang diubah");
    const sebelum = structuredClone(t);
    if (b.jenis) t.kind = b.jenis;
    if (b.jumlah) t.amount = Number(b.jumlah);
    if (b.kategori) t.category = b.kategori;
    if (b.deskripsi !== undefined) t.description = b.deskripsi;
    if (b.merchant !== undefined) t.merchant = b.merchant;
    if (b.tanggal) t.date = b.tanggal;
    t.updated_at = new Date().toISOString();
    return { sebelum, sesudah: structuredClone(t) };
  }

  async deleteTx(id: number) {
    await sleep(80);
    const i = this.txs.findIndex((x) => x.id === id);
    if (i < 0) throw new Error("404: transaksi tidak ditemukan");
    const [d] = this.txs.splice(i, 1);
    return { dihapus: d };
  }

  async summary(q: { periode?: string; tanggal_mulai?: string; tanggal_akhir?: string; kategori?: string } = {}): Promise<Summary> {
    const periode = q.periode ?? "bulan_ini";
    const { transaksi } = await this.transactions({ periode, tanggal_mulai: q.tanggal_mulai, tanggal_akhir: q.tanggal_akhir, kategori: q.kategori, limit: 10000 });
    const [from, to] = periodRange(periode);
    const map = new Map<string, { jenis: string; kategori: string; jumlah_transaksi: number; total: number }>();
    for (const t of transaksi) {
      const k = `${t.kind}:${t.category}`;
      const e = map.get(k) ?? { jenis: t.kind, kategori: t.category, jumlah_transaksi: 0, total: 0 };
      e.jumlah_transaksi++; e.total += t.amount; map.set(k, e);
    }
    const inc = transaksi.filter((t) => t.kind === "pemasukan").reduce((a, t) => a + t.amount, 0);
    const out = transaksi.filter((t) => t.kind === "pengeluaran").reduce((a, t) => a + t.amount, 0);
    return { periode: `${periode} (${from} s/d ${to > "9000" ? "sekarang" : to})`, total_pemasukan: inc, total_pengeluaran: out, saldo: inc - out,
      per_kategori: [...map.values()].sort((a, b) => b.total - a.total) };
  }

  async categories(): Promise<Category[]> { return CATEGORIES; }

  receiptImage(run: RunSummary) { return MOCK_IMAGES[run.id] ?? null; }
  runAudio(run: RunSummary) { return MOCK_AUDIO[run.id] ?? null; }

  /** Isi answered_by_run_id / answers_run_id seperti server (run berikutnya di percakapan yang sama). */
  private links(r: RunSummary): RunSummary {
    const conv = this.store.filter((x) => x.conversation_id === r.conversation_id).sort((a, b) => a.created_at - b.created_at);
    const i = conv.findIndex((x) => x.id === r.id);
    const next = conv[i + 1];
    const prev = conv[i - 1];
    return {
      ...r,
      answered_by_run_id: r.status === "awaiting_confirmation" ? next?.id ?? null : null,
      answers_run_id: prev?.status === "awaiting_confirmation" ? prev.id : null,
    };
  }

  // ------------------------------------------------------------ internal
  private sumCat(cat: string) {
    const [from] = periodRange("bulan_ini");
    return this.txs.filter((t) => t.category === cat && t.date >= from).reduce((a, t) => a + t.amount, 0);
  }

  private jobInfo(j: Job): Job {
    const idx = this.queue.findIndex((q) => q.job.id === j.id);
    return { ...j, queue_position: this.current?.id === j.id ? 0 : idx >= 0 ? idx + 1 : null };
  }

  /** Salin run nyata dengan id dan timestamp baru. */
  private cloneRun(tpl: RunSummary): RunSummary {
    const r = structuredClone(tpl);
    const id = rid("run");
    const shift = now() - (r.started_at ?? r.created_at);
    r.id = id; r.job_id = rid("job"); r.conversation_id = rid("conv");
    r.created_at += shift; r.started_at = (r.started_at ?? 0) + shift; r.finished_at = (r.finished_at ?? 0) + shift;
    const idMap = new Map<number, number>();
    for (const t of r.transactions ?? []) { const n = this.nextTx++; idMap.set(t.id, n); t.id = n; t.run_id = id; }
    for (const s of r.steps ?? []) {
      s.run_id = id; s.started_at = (s.started_at ?? 0) + shift;
      if (s.type === "input") s.output = { conversation_id: r.conversation_id };
      const tx = s.output?.transaksi;
      if (tx && idMap.has(tx.id)) { tx.id = idMap.get(tx.id)!; tx.run_id = id; }
    }
    return r;
  }

  private duplicateRun(type: InputType, prevId: string, key: string): RunSummary {
    const id = rid("run");
    const prev = this.store.find((r) => r.id === prevId);
    const txIds = (prev?.transactions ?? []).map((t) => t.id);
    const ans = `Input ini sudah pernah diproses di ${prevId}${txIds.length ? ` (transaksi #${txIds.join(", #")})` : ""}. Tidak dicatat ulang.`;
    const t = now();
    const mk = (seq: number, type_: string, name: string, input: any, output: any): Step => ({
      id: Math.floor(Math.random() * 1e6), run_id: id, seq, type: type_, name, status: "ok", input, output, started_at: t + seq * 0.001,
      duration_ms: null, prompt_tokens: null, completion_tokens: null, tokens_per_second: null });
    if (prev && type === "receipt" && MOCK_IMAGES[prevId]) MOCK_IMAGES[id] = MOCK_IMAGES[prevId];
    return {
      id, job_id: rid("job"), conversation_id: rid("conv"), input_type: type, input_text: "", input_meta: {}, status: "done", outcome: "duplicate",
      final_answer: ans, error: null, model: null, prompt_tokens: 0, completion_tokens: 0, llm_calls: 0, tool_calls: 0,
      created_at: t, started_at: t, finished_at: t + 0.004, duration_ms: 4, transactions: [],
      steps: [
        mk(1, "input", type, {}, { conversation_id: "" }),
        mk(2, "dedup", "cek_duplikat", { kunci: key }, { duplikat: true, run_sebelumnya: prevId, transaksi_sebelumnya: txIds }),
        mk(3, "final", "done", null, { answer: ans, outcome: "duplicate", warnings: [] }),
      ],
    };
  }

  private textRun(text: string, t0: number): RunSummary {
    const tpl = templateFor("text");
    const m = text.match(/(\d+(?:[.,]\d+)?)\s*(rb|ribu|k|jt|juta)?/i);
    let amount = m ? parseFloat(m[1].replace(",", ".")) : 10000;
    const unit = m?.[2]?.toLowerCase();
    if (unit && ["rb", "ribu", "k"].includes(unit)) amount *= 1000;
    if (unit && ["jt", "juta"].includes(unit)) amount *= 1_000_000;
    if (!unit && amount < 1000 && m?.[1].includes(".")) amount *= 1000;
    amount = Math.round(amount);
    const income = /gaji|gajian|terima|dapat|masuk|dibayar/i.test(text);
    const cats: [RegExp, string][] = [[/makan|minum|kopi|ngopi|bakso|nasi|warung|warkop|jajan/i, "makanan_minuman"],
      [/bensin|ojek|grab|gojek|parkir|tol|kereta|bus/i, "transportasi"], [/listrik|pulsa|internet|air|tagihan|wifi/i, "tagihan"],
      [/obat|dokter|apotek/i, "kesehatan"], [/buku|kuliah|kursus|ukt/i, "pendidikan"], [/nonton|game|spotify|netflix/i, "hiburan"],
      [/beli|belanja/i, "belanja"]];
    const category = income ? (/gaji/i.test(text) ? "gaji" : "lainnya_masuk") : cats.find(([re]) => re.test(text))?.[1] ?? "lainnya_keluar";
    const base = tpl ? this.cloneRun(tpl) : questionRun(t0, text);
    const id = base.id;
    const date = new Date().toISOString().slice(0, 10);
    const tx: Tx = { id: this.nextTx++, kind: income ? "pemasukan" : "pengeluaran", amount, category, description: text.slice(0, 80), merchant: null,
      date, source: "text", items: null, run_id: id, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
    const args = { jenis: tx.kind, jumlah: amount, kategori: category, deskripsi: tx.description, tanggal: date };
    const ans = `Saya sudah mencatat ${tx.kind} Rp${amount.toLocaleString("id-ID")} (kategori ${category}, tanggal ${date}).`;
    const thinking = `Pengguna menulis "${text}". Ini ${tx.kind} sebesar ${amount} rupiah. Kategori paling cocok: ${category}. Tanggal tidak disebut, jadi pakai hari ini (${date}). Datanya jelas, jadi langsung panggil catat_transaksi.`;
    // Ganti isi step agar sesuai input baru, struktur & token tetap dari trace nyata.
    const steps = (base.steps ?? []).filter((s) => !(s.type === "tool" && s.name === "catat_transaksi" && s !== base.steps!.find((x) => x.type === "tool")));
    steps.forEach((s, i) => { s.seq = i + 1; });
    for (const s of steps) {
      if (s.type === "input") s.input = { text };
      if (s.type === "reasoning" && s.name === "agent_step_1") s.output = { ...s.output, thinking, chars: thinking.length };
      if (s.type === "llm" && s.name === "agent_step_1") {
        s.input = { ...s.input, messages: [s.input.messages[0], { role: "user", content: text }] };
        s.output = { ...s.output, tool_calls: [{ id: "call_mock01", function: { name: "catat_transaksi", arguments: args } }], reasoning_step_seq: s.seq - 1 };
      }
      if (s.type === "tool") { s.input = args; s.output = { ok: true, transaksi: tx }; }
      if (s.type === "reasoning" && s.name === "agent_step_2") s.output = { ...s.output, thinking: "Transaksi berhasil dicatat. Beri konfirmasi singkat ke pengguna." };
      if (s.type === "llm" && s.name === "agent_step_2") s.output = { ...s.output, content: ans, reasoning_step_seq: s.seq - 1 };
      if (s.type === "final") s.output = { answer: ans, outcome: "recorded", warnings: [] };
    }
    return { ...base, steps, input_text: text, input_meta: { text }, final_answer: ans, transactions: [tx], tool_calls: 1 };
  }

  private async submit(type: InputType, run: RunSummary, payload: Record<string, unknown>): Promise<InputResponse> {
    await sleep(120);
    const t = now();
    const job: Job = { id: run.job_id, input_type: type, status: "queued", run_id: run.id, conversation_id: run.conversation_id,
      queue_position: this.queue.length + (this.current ? 1 : 0) + 1, payload, created_at: t, started_at: null, finished_at: null,
      wait_ms: null, run_ms: null, error: null };
    const full = structuredClone(run);
    // Run yang terlihat: kosong dulu, diisi step demi step.
    const live: RunSummary = { ...structuredClone(run), status: "queued", outcome: null, final_answer: null, error: null, steps: [],
      transactions: [], prompt_tokens: 0, completion_tokens: 0, llm_calls: 0, tool_calls: 0, created_at: t, started_at: null, finished_at: null, duration_ms: null };
    this.store.unshift(live);
    this.jobsList.push(job);
    this.queue.push({ job, run: full });
    this.emitAll({ event: "job_status", data: { ...job } });
    // Mulai sedikit tertunda agar UI sempat membuka SSE (llm_delta tidak disimpan, sama seperti server).
    if (!this.workerOn) { this.workerOn = true; setTimeout(() => void this.worker(), 450); }
    return { job: { ...job }, run: null, timed_out: false };
  }

  private emit(runId: string, e: StreamEvent) {
    for (const l of this.runListeners.get(runId) ?? []) l(e);
    this.emitAll(e);
  }
  private emitAll(e: StreamEvent) { for (const l of this.globalListeners) l(e); }

  private async worker() {
    while (this.queue.length) {
      const { job, run: full } = this.queue.shift()!;
      this.current = job;
      job.status = "running"; job.started_at = now(); job.wait_ms = Math.round((job.started_at - job.created_at) * 1000);
      this.emit(job.run_id, { event: "job_status", data: this.jobInfo(job) });
      const live = this.store.find((r) => r.id === job.run_id)!;
      live.status = "running"; live.started_at = now();
      this.emit(live.id, { event: "run_status", data: { run_id: live.id, status: "running" } });

      const steps = full.steps ?? [];
      for (let i = 0; i < steps.length; i++) {
        const s = steps[i];
        if (s.type === "reasoning") {
          const llm = steps[i + 1]?.type === "llm" && steps[i + 1].name === s.name ? steps[i + 1] : null;
          this.emit(live.id, { event: "step_started", data: { run_id: live.id, type: "llm", name: s.name, started_at: now() } });
          await this.streamText(live.id, s.name, "thinking", s.output?.thinking ?? "", simMs(s.duration_ms));
          this.pushStep(live, s);
          if (llm) {
            const content = llm.output?.content ?? "";
            if (content) await this.streamText(live.id, s.name, "content", content, simMs((llm.duration_ms ?? 0) - (s.duration_ms ?? 0)));
            else await sleep(simMs((llm.duration_ms ?? 0) - (s.duration_ms ?? 0)));
            this.pushStep(live, llm);
            i++;
          }
          continue;
        }
        if (s.type === "llm") {
          this.emit(live.id, { event: "step_started", data: { run_id: live.id, type: s.type, name: s.name, started_at: now() } });
          const content = s.output?.content ?? "";
          if (content) await this.streamText(live.id, s.name, "content", content, simMs(s.duration_ms));
          this.pushStep(live, s);
          continue;
        }
        if (s.type !== "final" && s.type !== "input") {
          this.emit(live.id, { event: "step_started", data: { run_id: live.id, type: s.type, name: s.name, started_at: now() } });
          await sleep(Math.max(220, simMs(s.duration_ms)));
        }
        this.pushStep(live, s);
      }

      Object.assign(live, { ...full, steps: live.steps, started_at: live.started_at, finished_at: now(),
        duration_ms: full.duration_ms, created_at: live.created_at });
      for (const t of full.transactions ?? []) this.txs.push(structuredClone(t));
      job.status = (live.status as string) === "error" ? "error" : "done"; job.finished_at = now();
      job.run_ms = Math.round((job.finished_at - (job.started_at ?? job.finished_at)) * 1000);
      this.current = null;
      const { steps: _s, ...sum } = live;
      this.emit(live.id, { event: "job_status", data: this.jobInfo(job) });
      this.emit(live.id, { event: "run_finished", data: structuredClone(sum) as RunSummary });
      this.runListeners.get(live.id)?.clear();
      await sleep(300);
    }
    this.workerOn = false;
  }

  private pushStep(live: RunSummary, s: Step) {
    const step = structuredClone(s);
    step.run_id = live.id;
    step.started_at = now() - (s.duration_ms ?? 0) / 1000;
    live.steps!.push(step);
    if (s.type === "llm") { live.llm_calls++; live.prompt_tokens += s.prompt_tokens ?? 0; live.completion_tokens += s.completion_tokens ?? 0; }
    if (s.type === "tool") live.tool_calls++;
    this.emit(live.id, { event: "step", data: structuredClone(step) });
  }

  private async streamText(runId: string, stepName: string, kind: "thinking" | "content", text: string, totalMs: number) {
    if (!text) { await sleep(totalMs); return; }
    const ticks = Math.max(1, Math.round(totalMs / 150));
    const size = Math.ceil(text.length / ticks);
    for (let k = 0; k < ticks; k++) {
      const chunk = text.slice(k * size, (k + 1) * size);
      if (!chunk) break;
      this.emit(runId, { event: "llm_delta", data: { run_id: runId, step_name: stepName, kind, index: k, text: chunk } });
      await sleep(150);
    }
  }
}
