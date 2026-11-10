// Klien live: REST + SSE ke AI server sungguhan (docs/api.md).
import type {
  ApiClient, Category, Health, InputResponse, JobsResponse, Metrics, RunQuery, RunSummary,
  StreamEvent, Summary, SystemInfo, Tx, TxList, TxQuery,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const STREAM_EVENTS = ["hello", "job_status", "run_status", "step_started", "llm_delta", "step", "run_finished"] as const;

function qs(params: Record<string, unknown> = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export class HttpClient implements ApiClient {
  readonly mode = "live" as const;
  /** Gambar struk yang diunggah dari browser ini, per run_id (server tidak punya endpoint untuk mengambil gambar). */
  private images = new Map<string, string>();

  constructor(private base: string, private key: string) {
    this.base = base.replace(/\/+$/, "");
  }

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    if (this.key) headers.set("X-API-Key", this.key);
    if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
    let res: Response;
    try {
      res = await fetch(this.base + path, { ...init, headers });
    } catch (e) {
      const where = this.base || `${location.origin} (proxy ke server)`;
      throw new ApiError(0, `Tidak bisa menghubungi ${where} (${(e as Error).message}). Cek URL, jaringan, firewall.`);
    }
    if (!res.ok) {
      let msg = res.statusText;
      try {
        const body = await res.json();
        // 400: {"detail": "pesan"}; 422 FastAPI: {"detail": [{loc, msg}, ...]}
        msg = typeof body.detail === "string" ? body.detail
          : Array.isArray(body.detail) ? body.detail.map((d: any) => `${(d.loc ?? []).filter((x: unknown) => x !== "body").join(".")}: ${d.msg}`).join("; ")
          : JSON.stringify(body.detail ?? body);
      } catch { /* bukan JSON */ }
      const hint = res.status === 401 ? " (API key ditolak: cek API_KEYS di server/.env, atau key di Pengaturan bila diisi)" : res.status === 429 ? " (rate limit)" : "";
      throw new ApiError(res.status, `${res.status}: ${msg}${hint}`);
    }
    return res.json() as Promise<T>;
  }

  health() { return this.req<Health>("/health"); }
  system() { return this.req<SystemInfo>("/v1/system"); }
  metrics(window = 200) { return this.req<Metrics>(`/v1/metrics${qs({ window })}`); }

  chat(text: string, conversationId?: string | null) {
    return this.req<InputResponse>("/v1/chat", {
      method: "POST", body: JSON.stringify({ text, conversation_id: conversationId ?? null }),
    });
  }

  private upload(path: string, file: Blob, filename: string, extra: Record<string, string | null | undefined> = {}) {
    const fd = new FormData();
    fd.append("file", file, filename);
    for (const [k, v] of Object.entries(extra)) if (v) fd.append(k, v);
    return this.req<InputResponse>(path, { method: "POST", body: fd });
  }

  async receipt(file: File, note?: string, conversationId?: string | null) {
    const r = await this.upload("/v1/receipt", file, file.name, { note, conversation_id: conversationId });
    this.images.set(r.job.run_id, URL.createObjectURL(file));
    return r;
  }
  voice(file: Blob, filename: string, conversationId?: string | null) {
    return this.upload("/v1/voice", file, filename, { conversation_id: conversationId });
  }

  jobs(status?: string, limit = 50) { return this.req<JobsResponse>(`/v1/jobs${qs({ status, limit })}`); }

  async runs(q: RunQuery = {}) {
    // Server mengembalikan {"runs": [...]}; terima juga array polos.
    const r = await this.req<{ runs: RunSummary[] } | RunSummary[]>(`/v1/runs${qs({ limit: 50, ...q })}`);
    return Array.isArray(r) ? r : r.runs;
  }
  run(id: string) { return this.req<RunSummary>(`/v1/runs/${encodeURIComponent(id)}`); }

  private sse(path: string, onEvent: (e: StreamEvent) => void, onError?: (m: string) => void, closeOn?: string) {
    const url = this.withKeyParam(`${this.base}${path}`, "api_key");
    const es = new EventSource(url);
    let closed = false;
    for (const name of STREAM_EVENTS) {
      es.addEventListener(name, (ev) => {
        let data: any = null;
        try { data = JSON.parse((ev as MessageEvent).data); } catch { return; }
        onEvent({ event: name, data } as StreamEvent);
        if (closeOn && name === closeOn) { closed = true; es.close(); }
      });
    }
    es.onerror = () => {
      if (closed) return;
      if (es.readyState === EventSource.CLOSED) onError?.("Stream SSE terputus");
      else onError?.("Stream SSE tersendat, mencoba menyambung ulang…");
    };
    return () => { closed = true; es.close(); };
  }

  /**
   * Cek sekali apakah SSE benar-benar mengalir: /v1/events langsung mengirim `hello`.
   * Lewat Cloudflare Quick Tunnel (trycloudflare.com) SSE ditahan sampai respons selesai
   * ("Quick Tunnels do not support Server-Sent Events"), jadi web app beralih ke polling.
   */
  private sseProbe: Promise<boolean> | null = null;
  transport: "sse" | "polling" | "unknown" = "unknown";
  private probeSse(): Promise<boolean> {
    if (this.sseProbe) return this.sseProbe;
    this.sseProbe = new Promise<boolean>((resolve) => {
      if (typeof EventSource === "undefined") { resolve(false); return; }
      const es = new EventSource(this.withKeyParam(`${this.base}/v1/events`, "api_key"));
      const done = (ok: boolean) => { clearTimeout(t); es.close(); resolve(ok); };
      const t = setTimeout(() => done(false), 4000);
      es.addEventListener("hello", () => done(true));
    }).then((ok) => { this.transport = ok ? "sse" : "polling"; return ok; });
    return this.sseProbe;
  }

  /** Fallback tanpa SSE: ambil GET /v1/runs/{id} berkala dan kirim step baru sebagai event `step`. */
  private pollRun(runId: string, onEvent: (e: StreamEvent) => void, onError?: (m: string) => void) {
    let stop = false;
    const seen = new Set<number>();
    let last = "";
    const tick = async () => {
      if (stop) return;
      try {
        const r = await this.run(runId);
        if (stop) return;
        for (const s of r.steps ?? []) if (!seen.has(s.seq)) { seen.add(s.seq); onEvent({ event: "step", data: s }); }
        if (r.status !== last) { last = r.status; onEvent({ event: "run_status", data: { run_id: runId, status: r.status } }); }
        if (!["queued", "running"].includes(r.status)) {
          const { steps: _s, ...sum } = r;
          onEvent({ event: "run_finished", data: sum as RunSummary });
          return;
        }
      } catch (e) { onError?.((e as Error).message); }
      setTimeout(tick, 1500);
    };
    void tick();
    return () => { stop = true; };
  }

  /** Fallback untuk /v1/events: pantau /v1/jobs dan kirim job_status saat ada perubahan. */
  private pollAll(onEvent: (e: StreamEvent) => void) {
    let stop = false;
    let sig = "";
    const tick = async () => {
      if (stop) return;
      try {
        const j = await this.jobs(undefined, 20);
        const now = j.jobs.map((x) => `${x.id}:${x.status}`).join(",");
        if (sig && now !== sig) for (const x of j.jobs) onEvent({ event: "job_status", data: x });
        sig = now;
      } catch { /* abaikan, coba lagi */ }
      setTimeout(tick, 3000);
    };
    void tick();
    return () => { stop = true; };
  }

  /** Jalankan SSE bila mengalir, selain itu polling. Mengembalikan fungsi penutup yang langsung berlaku. */
  private withTransport(sse: () => () => void, poll: () => () => void) {
    let close: (() => void) | null = null;
    let cancelled = false;
    void this.probeSse().then((ok) => { if (!cancelled) close = ok ? sse() : poll(); });
    return () => { cancelled = true; close?.(); };
  }

  streamRun(runId: string, onEvent: (e: StreamEvent) => void, onError?: (m: string) => void) {
    return this.withTransport(
      () => this.sse(`/v1/runs/${encodeURIComponent(runId)}/stream`, onEvent, onError, "run_finished"),
      () => this.pollRun(runId, onEvent, onError),
    );
  }
  streamAll(onEvent: (e: StreamEvent) => void, onError?: (m: string) => void) {
    return this.withTransport(() => this.sse("/v1/events", onEvent, onError), () => this.pollAll(onEvent));
  }

  transactions(q: TxQuery = {}) { return this.req<TxList>(`/v1/transactions${qs({ limit: 200, ...q })}`); }
  createTx(body: Record<string, unknown>) { return this.req<Tx>("/v1/transactions", { method: "POST", body: JSON.stringify(body) }); }
  updateTx(id: number, body: Record<string, unknown>) {
    return this.req<{ sebelum: Tx; sesudah: Tx }>(`/v1/transactions/${id}`, { method: "PATCH", body: JSON.stringify(body) });
  }
  deleteTx(id: number) { return this.req<{ dihapus: Tx }>(`/v1/transactions/${id}`, { method: "DELETE" }); }
  summary(q: Record<string, string | undefined> = {}) { return this.req<Summary>(`/v1/summary${qs(q)}`); }
  categories() { return this.req<Category[]>("/v1/categories"); }

  private fileUrl(run: RunSummary, kind: "image" | "audio") {
    return this.withKeyParam(`${this.base}/v1/runs/${encodeURIComponent(run.id)}/${kind}`, "key");
  }
  /** Key di query hanya bila diisi di Pengaturan; kosong = proxy web app yang menyisipkan X-API-Key. */
  private withKeyParam(url: string, name: "api_key" | "key") {
    if (!this.key) return url;
    return `${url}${url.includes("?") ? "&" : "?"}${name}=${encodeURIComponent(this.key)}`;
  }
  // Pakai salinan lokal bila struk diunggah dari browser ini (langsung tampil), selain itu ambil dari server.
  receiptImage(run: RunSummary) { return this.images.get(run.id) ?? this.fileUrl(run, "image"); }
  runAudio(run: RunSummary) { return this.fileUrl(run, "audio"); }
}
