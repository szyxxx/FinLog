// Tipe data mengikuti docs/api.md (v0.1). Field yang tidak pasti dibuat opsional.

/** "email" hanya untuk menampilkan run lama; fitur email sudah dihapus dari server/web app. */
export type InputType = "text" | "receipt" | "voice" | "email";
export type JobStatus = "queued" | "running" | "done" | "error";
export type RunStatus = "queued" | "running" | "done" | "awaiting_confirmation" | "done_with_warnings" | "error";
export type RunOutcome =
  | "recorded" | "modified" | "answered" | "not_transaction" | "duplicate"
  | "possible_duplicate" | "needs_confirmation" | "error";

export type StepType =
  | "input" | "preprocess" | "ocr" | "rules" | "validation" | "fallback" | "reasoning" | "llm" | "tool"
  | "dedup" | "context" | "guard" | "confirmation" | "final" | "error";

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
  source: "text" | "receipt" | "voice" | "email" | "manual";
  items?: ReceiptItem[] | null;
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
  | { event: "run_finished"; data: RunSummary };

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
}
