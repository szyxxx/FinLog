// Tipe data mengikuti docs/api.md (v0.1). Field yang tidak pasti dibuat opsional.

/** "email" hanya untuk menampilkan run lama; fitur email sudah dihapus dari server/web app. */
export type InputType = "text" | "receipt" | "voice" | "email" | "automation";
export type JobStatus = "queued" | "running" | "done" | "error";
export type RunStatus = "queued" | "running" | "done" | "awaiting_confirmation" | "done_with_warnings" | "error";
export type RunOutcome =
  | "recorded" | "modified" | "answered" | "not_transaction" | "duplicate"
  | "possible_duplicate" | "needs_confirmation" | "report" | "error";

export type StepType =
  | "input" | "preprocess" | "ocr" | "rules" | "validation" | "fallback" | "reasoning" | "llm" | "tool"
  | "dedup" | "context" | "guard" | "confirmation" | "final" | "error" | "sandbox";

export interface Job {
  id: string;
  input_type: InputType;
  status: JobStatus;
  run_id: string;
  conversation_id?: string | null;
  queue_position?: number | null;
  payload?: Record<string, unknown> | null;
  created_at: number;
  started_at?: number | null;
  finished_at?: number | null;
  wait_ms?: number | null;
  run_ms?: number | null;
  error?: string | null;
}

export interface InputResponse {
  job: Job;
  run: RunSummary | null;
  timed_out: boolean;
}

export interface ReceiptItem {
  nama: string;
  qty?: number;
  harga_satuan?: number;
  subtotal?: number;
}

export interface Tx {
  id: number;
  kind: "pemasukan" | "pengeluaran";
  amount: number;
  category: string;
  description?: string | null;
  merchant?: string | null;
  date: string;
  source: "text" | "receipt" | "voice" | "email" | "manual" | "demo" | "otomasi" | "tagihan" | string;
  items?: ReceiptItem[] | null;
  /** Transaksi mata uang asing (tool konversi_kurs). */
  orig_currency?: string | null;
  orig_amount?: number | null;
  fx_rate?: number | null;
  bill_id?: number | null;
  run_id?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Step {
  id: number;
  run_id: string;
  seq: number;
  type: StepType | string;
  name: string;
  status: "ok" | "error" | "warning" | string;
  input: any;
  output: any;
  started_at: number | null;
  duration_ms: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  tokens_per_second?: number | null;
}

export interface RunSummary {
  id: string;
  job_id: string;
  conversation_id: string;
  input_type: InputType;
  input_text?: string | null;
  input_meta?: Record<string, any> | null;
  status: RunStatus;
  outcome: RunOutcome | null;
  final_answer: string | null;
  error: string | null;
  model?: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  llm_calls: number;
  tool_calls: number;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  duration_ms: number | null;
  transactions?: Tx[];
  steps?: Step[];
  dedup_key?: string | null;
  /** Hanya untuk run awaiting_confirmation: run yang menjawabnya. */
  answered_by_run_id?: string | null;
  /** Run awaiting_confirmation yang dijawab oleh run ini. */
  answers_run_id?: string | null;
}

export interface JobsResponse {
  queue_length: number;
  current_job: Job | null;
  jobs: Job[];
}

export interface TxList {
  jumlah_hasil: number;
  total_nominal: number;
  transaksi: Tx[];
}

export interface Summary {
  periode: string;
  total_pemasukan: number;
  total_pengeluaran: number;
  saldo: number;
  per_kategori: { jenis: string; kategori: string; jumlah_transaksi: number; total: number }[];
}

export interface Category {
  name: string;
  kind: string;
  description?: string;
}

export interface LatencyStat {
  runs: number;
  errors: number;
  latency_ms: { avg: number; p50: number; p95: number; max: number };
  queue_wait_ms_avg?: number;
}

export interface Metrics {
  window_runs: number;
  queue: { length: number; current_job: Job | null };
  runs_by_status: Record<string, number>;
  latency_by_input_type: Record<string, LatencyStat>;
  llm: {
    calls: number;
    tokens_per_second_avg: number | null;
    tokens_per_second_p50?: number | null;
    llm_step_ms_avg?: number | null;
    prompt_tokens_total: number;
    completion_tokens_total: number;
  };
  stt?: { model?: string; device?: string; compute_type?: string; load_ms?: number; real_time_factor_avg?: number | null };
  ollama_loaded_models?: { name: string; size_vram_bytes: number; context_length?: number; expires_at?: string }[];
  gpu?: {
    available: boolean;
    gpus?: { name: string; memory_used_mib: number | null; memory_total_mib: number | null; utilization_pct: number | null; temperature_c?: number | null; power_w?: number | null }[];
    processes?: { pid: number; name: string; used_memory_mib: number | string }[];
    reason?: string;
  };
}

export interface Health {
  status: string;
  time: number | string;
  queue_length: number;
}

/** Bentuk nyata dari server/app/main.py (docs/api.md hanya menyebut isinya secara umum). */
export interface SystemInfo {
  llm_model?: string;
  vision_model?: string;
  think?: boolean;
  vision_think?: boolean | null;
  receipt_mode?: string;
  receipt_fallback_vision?: boolean;
  receipt_rules_first?: boolean;
  model_capabilities?: Record<string, string[] | string>;
  whisper_model?: string;
  whisper_device?: string;
  imap_enabled?: boolean;
  tools?: { type?: string; function?: { name: string; description?: string; parameters?: any } }[];
  categories?: Category[];
  [k: string]: any;
}

/** Event SSE per run / global. */
export type StreamEvent =
  | { event: "hello"; data: any }
  | { event: "job_status"; data: Job }
  | { event: "run_status"; data: { run_id: string; status: RunStatus } }
  | { event: "step_started"; data: { run_id: string; type: string; name: string; started_at: number } }
  | { event: "llm_delta"; data: { run_id: string; step_name: string; kind: "thinking" | "content"; index: number; text: string } }
  | { event: "step"; data: Step }
  | { event: "run_finished"; data: RunSummary }
  | { event: "notification"; data: Notif };

// ---------------------------------------------------------------- layanan baru (anggaran, tagihan, otomasi)

export interface BudgetRow {
  kategori: string; batas: number; terpakai: number; sisa: number; persen: number; ambang_persen: number;
  proyeksi_akhir_bulan: number; sisa_per_hari: number;
  status: "aman" | "waspada" | "terlampaui" | "diproyeksikan_lewat";
}
export interface BudgetStatus { bulan: string; hari_ke: number; jumlah_hari: number; anggaran: BudgetRow[]; kategori_tanpa_anggaran: string[] }

export interface Bill {
  id: number; name: string; amount: number; category: string; merchant?: string | null;
  frequency: "mingguan" | "bulanan" | "tahunan"; next_due: string; auto_record: boolean; origin: string;
  active: boolean; last_paid?: string | null; hari_lagi: number | null;
}
export interface BillList { tagihan: Bill[]; jumlah: number; perkiraan_total_per_bulan: number }

export interface RecurringCandidate {
  nama: string; kunci: string; frekuensi: string; nominal_tipikal: number; kategori: string; kemunculan: number;
  tanggal: string[]; perkiraan_berikutnya: string; keyakinan: number;
}

export interface Notif {
  id: number; kind: "anggaran" | "tagihan" | "laporan" | "langganan" | "otomasi" | string;
  level: "info" | "warning" | "danger"; title: string; body?: string | null; data?: any; read: boolean; created_at: string;
}

export interface Report {
  id: string; kind: string; period_label: string; date_from: string; date_to: string; narrative?: string;
  narrative_source: "agent" | "template" | string; run_id?: string | null; created_at: string; url: string;
  total: { pemasukan: number; pengeluaran: number; saldo: number; jumlah_transaksi: number };
}

export interface Automation {
  name: string; label: string; jadwal: string; pakai_llm: boolean; aktif: boolean; slot_terakhir: string | null;
  terakhir_jalan: string | null; status_terakhir: string | null; detail_terakhir: any; jadwal_berikutnya: string;
}

export interface TxQuery {
  jenis?: string;
  kategori?: string;
  kata_kunci?: string;
  periode?: string;
  tanggal_mulai?: string;
  tanggal_akhir?: string;
  source?: string;
  limit?: number;
}

export interface RunQuery {
  input_type?: string;
  status?: string;
  outcome?: string;
  conversation_id?: string;
  limit?: number;
  offset?: number;
}

export interface ApiClient {
  readonly mode: "mock" | "live";
  /** Cara trace live diterima: SSE, atau polling bila SSE ditahan (mis. Cloudflare Quick Tunnel). */
  transport?: "sse" | "polling" | "unknown";
  health(): Promise<Health>;
  system(): Promise<SystemInfo>;
  metrics(window?: number): Promise<Metrics>;
  chat(text: string, conversationId?: string | null): Promise<InputResponse>;
  receipt(file: File, note?: string, conversationId?: string | null): Promise<InputResponse>;
  voice(file: Blob, filename: string, conversationId?: string | null): Promise<InputResponse>;
  jobs(status?: string, limit?: number): Promise<JobsResponse>;
  runs(q?: RunQuery): Promise<RunSummary[]>;
  run(id: string): Promise<RunSummary>;
  /** Buka stream SSE per run. Mengembalikan fungsi penutup. */
  streamRun(runId: string, onEvent: (e: StreamEvent) => void, onError?: (msg: string) => void): () => void;
  /** Stream global /v1/events. */
  streamAll(onEvent: (e: StreamEvent) => void, onError?: (msg: string) => void): () => void;
  transactions(q?: TxQuery): Promise<TxList>;
  createTx(body: Partial<{ jenis: string; jumlah: number; kategori: string; deskripsi: string; merchant: string; tanggal: string }>): Promise<Tx>;
  updateTx(id: number, body: Record<string, unknown>): Promise<{ sebelum: Tx; sesudah: Tx }>;
  deleteTx(id: number): Promise<{ dihapus: Tx }>;
  summary(q?: { periode?: string; tanggal_mulai?: string; tanggal_akhir?: string; kategori?: string }): Promise<Summary>;
  categories(): Promise<Category[]>;
  /** URL gambar struk asli untuk run (GET /v1/runs/{id}/image?key=). */
  receiptImage(run: RunSummary): string | null;
  /** URL voice note asli untuk run (GET /v1/runs/{id}/audio?key=). */
  runAudio(run: RunSummary): string | null;

  // ---- layanan baru
  budgets(): Promise<BudgetStatus>;
  setBudget(kategori: string, batas: number, ambang?: number): Promise<BudgetRow>;
  deleteBudget(kategori: string): Promise<unknown>;
  bills(status?: string): Promise<BillList>;
  addBill(body: { nama: string; jumlah: number; jatuh_tempo: string; frekuensi?: string; kategori?: string; catat_otomatis?: boolean }): Promise<Bill>;
  payBill(id: number): Promise<{ transaksi: Tx; tagihan: Bill; peringatan_anggaran?: string[] }>;
  deleteBill(id: number): Promise<Bill>;
  detectSubscriptions(): Promise<{ kandidat: RecurringCandidate[]; jumlah: number }>;
  notifications(unread?: boolean): Promise<{ unread: number; notifications: Notif[] }>;
  readNotifications(ids?: number[]): Promise<unknown>;
  reports(): Promise<{ reports: Report[] }>;
  requestReport(periode: string): Promise<InputResponse>;
  automations(): Promise<{ enabled: boolean; timezone: string; tasks: Automation[] }>;
  runAutomation(name: string): Promise<any>;
  /** URL file dari server (grafik /v1/artifacts/{id}, PDF /v1/reports/{id}/pdf), dengan key bila perlu. */
  fileUrl(path: string): string;
}
