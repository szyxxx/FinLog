import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, CornerDownLeft } from "lucide-react";
import type { RunSummary, Step } from "../api/types";
import { useApp, useLoad } from "../lib/store";
import { useRunTrace } from "../lib/useRunTrace";
import { INPUT_LABEL, OUTCOME, catLabel, clock, dur, num, rp } from "../lib/format";
import { Badge, InputIcon, OutcomeBadge, RichText, StatusBadge } from "./ui";
import { StepDetail } from "./StepDetail";
import { StepIcon, stepGist, stepMeta } from "./stepMeta";
import { ReceiptViewer } from "./ReceiptViewer";

export function RunView({ runId, openStep, onSubmitted }: { runId: string; openStep?: number; onSubmitted: (runId: string) => void }) {
  const { client, bump, toast } = useApp();
  const tr = useRunTrace(client, runId, () => bump());
  const { run, steps, pending, deltas } = tr;
  const [open, setOpen] = useState<Set<number>>(new Set(openStep ? [openStep] : []));
  useEffect(() => setOpen(new Set(openStep ? [openStep] : [])), [runId, openStep]);

  if (tr.error && !run) return <div className="card"><div className="banner err">{tr.error}</div></div>;
  if (!run) return <div className="card"><div className="row muted"><span className="spin" /> Memuat run…</div></div>;

  const ocr = steps.find((s) => s.type === "ocr");
  const extract = steps.find((s) => s.type === "preprocess" && s.name === "ekstrak_struk");
  const validation = [...steps].reverse().find((s) => s.type === "validation");
  const image = client.receiptImage(run);
  const confirm = [...steps].reverse().find((s) => s.type === "confirmation");
  const live = ["queued", "running"].includes(run.status);
  const llmSteps = steps.filter((s) => s.type === "llm");
  const ptok = live ? llmSteps.reduce((a, s) => a + (s.prompt_tokens ?? 0), 0) : run.prompt_tokens;
  const ctok = live ? llmSteps.reduce((a, s) => a + (s.completion_tokens ?? 0), 0) : run.completion_tokens;
  const tone = run.outcome ? OUTCOME[run.outcome]?.tone ?? "ok" : "ok";
  const inputText = run.input_type === "text" ? run.input_text : steps.find((s) => s.type === "input")?.input?.filename ?? run.input_meta?.filename ?? INPUT_LABEL[run.input_type];
  const transcript = steps.find((s) => s.name === "transkripsi_suara")?.output?.text;
  const email = steps.find((s) => s.name === "baca_email")?.output;

  return (
    <div className="stack">
      <section className="card pad-lg">
        <div className="run-hero">
          <div style={{ minWidth: 0 }}>
            <div className="row wrap" style={{ gap: 8 }}>
              <span className="type-ic"><InputIcon type={run.input_type} /></span>
              <span className="label">{INPUT_LABEL[run.input_type]}</span>
              <StatusBadge status={run.status} />
              <OutcomeBadge outcome={run.outcome} />
              {run.answered_by_run_id && <button className="badge tone-ok" style={{ border: 0, cursor: "pointer" }} onClick={() => onSubmitted(run.answered_by_run_id!)}>sudah dijawab → {run.answered_by_run_id}</button>}
              {run.answers_run_id && <button className="badge tone-info" style={{ border: 0, cursor: "pointer" }} onClick={() => onSubmitted(run.answers_run_id!)}>menjawab ← {run.answers_run_id}</button>}
              {tr.job && live && tr.job.queue_position ? <Badge tone="muted">posisi antrean {tr.job.queue_position}</Badge> : null}
            </div>
            <p className="run-input">{inputText}</p>
            {transcript && <p className="muted" style={{ margin: "6px 0 0" }}>“{transcript}”</p>}
            {run.input_type === "voice" && <RunAudio src={client.runAudio(run)} />}
            {email && <p className="muted" style={{ margin: "6px 0 0" }}>{email.subject} · {email.from}</p>}
          </div>
          <div className="small faint" style={{ textAlign: "right" }}>
            <div className="mono">{run.id}</div>
            <div>conv <span className="mono">{run.conversation_id}</span></div>
            <div>{clock(run.created_at)}{run.model ? ` · ${run.model}` : ""}</div>
          </div>
        </div>
        <div className="kpis">
          <div className="kpi"><b>{live ? <LiveTimer from={run.started_at ?? run.created_at} /> : dur(run.duration_ms)}</b><span>durasi</span></div>
          <div className="kpi"><b>{live ? llmSteps.length : run.llm_calls}</b><span>panggilan LLM</span></div>
          <div className="kpi"><b>{live ? steps.filter((s) => s.type === "tool").length : run.tool_calls}</b><span>tool call</span></div>
          <div className="kpi"><b>{num(ptok)}</b><span>prompt token</span></div>
          <div className="kpi"><b>{num(ctok)}</b><span>completion token</span></div>
        </div>
        {run.final_answer && run.status !== "awaiting_confirmation" && (
          <div className={`answer ${tone}`}><RichText text={run.final_answer} /></div>
        )}
        {run.error && <div className="answer bad">{run.error}</div>}
        {(run.transactions?.length ?? 0) > 0 && (
          <div className="row wrap" style={{ marginTop: 12, gap: 8 }}>
            {run.transactions!.map((t) => (
              <span key={t.id} className="badge tone-muted" style={{ padding: "5px 10px" }}>
                #{t.id} · <b style={{ color: t.kind === "pemasukan" ? "var(--ok)" : "var(--text-base)" }}>{t.kind === "pemasukan" ? "+" : "−"}{rp(t.amount)}</b> · {catLabel(t.category)}{t.merchant ? ` · ${t.merchant}` : ""}
              </span>
            ))}
          </div>
        )}
      </section>

      {run.status === "awaiting_confirmation" && (
        <ConfirmBox run={run} question={confirm?.output?.pertanyaan ?? run.final_answer ?? ""} options={confirm?.output?.opsi}
          onSent={(id) => { toast("Jawaban terkirim, agent melanjutkan…"); onSubmitted(id); }} onOpen={onSubmitted} />
      )}

      {run.input_type === "receipt" && (ocr || extract || image) && (
        <section className="card pad-lg">
          <div className="card-title"><h2>Struk</h2>{ocr && <span className="small faint">{ocr.input?.engine}</span>}</div>
          <ReceiptViewer image={image} ocr={ocr} extract={extract} validation={validation} mockNote={client.mode === "mock"} />
        </section>
      )}

      <section className="card">
        <div className="card-title" style={{ padding: "4px 6px 0" }}>
          <h2>Trace</h2>
          <div className="row small muted">
            {tr.streaming ? <><span className="spin" /> {client.transport === "polling" ? "live via polling" : "live via SSE"}</> : <span>{steps.length} step</span>}
            <button className="btn ghost sm" onClick={() => setOpen(open.size ? new Set() : new Set(steps.map((s) => s.seq)))}>{open.size ? "Tutup semua" : "Buka semua"}</button>
          </div>
        </div>
        {tr.error && <div className="banner err" style={{ margin: "0 6px 8px" }}>{tr.error}</div>}
        <Timeline run={run} steps={steps} open={open} toggle={(seq) => {
          const n = new Set(open); if (n.has(seq)) n.delete(seq); else n.add(seq); setOpen(n);
        }} />
        {pending && <PendingRow pending={pending} delta={deltas[pending.name]} />}
        {!pending && live && client.transport === "polling" && (
          <div className="row small muted" style={{ padding: "12px 10px", gap: 10 }}>
            <span className="spin" />
            <span>Memproses… step muncul setelah selesai. Lewat link publik (Cloudflare Quick Tunnel) reasoning tidak bisa mengalir per kata; buka dari laptop server/LAN untuk melihatnya live.</span>
          </div>
        )}
        {!steps.length && !pending && <div className="empty">{run.status === "queued" ? "Menunggu giliran di antrean (GPU satu, FIFO)…" : "Belum ada step."}</div>}
      </section>
    </div>
  );
}

function RunAudio({ src }: { src: string | null }) {
  const [err, setErr] = useState(false);
  useEffect(() => setErr(false), [src]);
  if (!src || err) return <p className="small faint" style={{ margin: "6px 0 0" }}>Audio asli tidak tersedia.</p>;
  return <audio controls preload="none" src={src} onError={() => setErr(true)} style={{ marginTop: 10, height: 36, width: 360, maxWidth: "100%" }} />;
}

function LiveTimer({ from }: { from: number }) {
  const [, setT] = useState(0);
  useEffect(() => { const i = setInterval(() => setT((x) => x + 1), 250); return () => clearInterval(i); }, []);
  return <>{dur(Math.max(0, Date.now() - from * 1000))}</>;
}

function Timeline({ run, steps, open, toggle }: { run: RunSummary; steps: Step[]; open: Set<number>; toggle: (seq: number) => void }) {
  const [t0, span] = useMemo(() => {
    const starts = steps.map((s) => s.started_at ?? 0).filter(Boolean);
    const a = Math.min(run.started_at ?? Infinity, ...starts);
    const ends = steps.map((s) => (s.started_at ?? a) + (s.duration_ms ?? 0) / 1000);
    // Run yang masih berjalan: skala sampai "sekarang" agar bar tidak melebar ke seluruh kolom.
    const live = ["queued", "running"].includes(run.status);
    const b = Math.max(run.finished_at ?? 0, ...ends, live ? Date.now() / 1000 : 0, a + 0.001);
    return [a, b - a];
  }, [run, steps]);
  if (!steps.length) return null;
  return (
    <div className="timeline" role="list">
      <div className="tl-head"><span>#</span><span /><span>Step</span><span>Ringkas · waktu</span><span style={{ textAlign: "right" }}>Durasi</span><span style={{ textAlign: "right" }}>Token</span><span /></div>
      {steps.map((s) => {
        const m = stepMeta(s.type);
        const isOpen = open.has(s.seq);
        const left = (((s.started_at ?? t0) - t0) / span) * 100;
        const width = ((s.duration_ms ?? 0) / 1000 / span) * 100;
        const color = s.status === "error" ? "var(--bad)" : s.status === "warning" ? "var(--warn)" : m.color;
        return (
          <div key={s.seq} role="listitem">
            <div className={`tl-row ${isOpen ? "open" : ""}`} onClick={() => toggle(s.seq)} aria-expanded={isOpen}>
              <span className="seq">{s.seq}</span>
              <StepIcon type={s.type} status={s.status} />
              <span className="nm"><b>{s.name}</b><span>{m.label}{s.status !== "ok" ? ` · ${s.status}` : ""}</span></span>
              <span style={{ minWidth: 0 }}>
                <span className="small muted" style={{ display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", marginBottom: 4 }}>{stepGist(s)}</span>
                <span className="gantt"><i style={{ left: `${Math.min(99, left)}%`, width: `${Math.max(width, 0.4)}%`, background: color }} /></span>
              </span>
              <span className="num">{s.duration_ms !== null ? dur(s.duration_ms) : "·"}</span>
              <span className="num">{s.prompt_tokens || s.completion_tokens ? <>{num(s.prompt_tokens)}<span className="faint">→</span>{num(s.completion_tokens)}{s.tokens_per_second ? <div className="tiny faint">{num(s.tokens_per_second, 1)} tok/dtk</div> : null}</> : <span className="faint">–</span>}</span>
              {isOpen ? <ChevronDown size={16} className="faint" /> : <ChevronRight size={16} className="faint" />}
            </div>
            {isOpen && <div className="tl-detail"><StepDetail step={s} steps={steps} /></div>}
          </div>
        );
      })}
    </div>
  );
}

function PendingRow({ pending, delta }: { pending: { type: string; name: string; started_at: number }; delta?: { thinking: string; content: string } }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [delta?.thinking, delta?.content]);
  const m = stepMeta(pending.type);
  return (
    <div>
      <div className="tl-row pending open">
        <span className="seq"><span className="spin" /></span>
        <StepIcon type={pending.type} />
        <span className="nm"><b>{pending.name}</b><span>{m.label} · berjalan</span></span>
        <span className="small muted">{delta?.thinking && !delta.content ? "berpikir…" : delta?.content ? "menulis jawaban…" : "memproses…"}</span>
        <span className="num"><LiveTimer from={pending.started_at} /></span>
        <span /><span />
      </div>
      {(delta?.thinking || delta?.content) && (
        <div className="tl-detail" style={{ background: "var(--accent-soft)" }}>
          {delta.thinking && <><div className="label" style={{ margin: "8px 0 4px" }}>Reasoning (live · llm_delta)</div>
            <div ref={delta.content ? undefined : ref} className={`live-text thinking inset ${delta.content ? "" : "caret"}`}>{delta.thinking}</div></>}
          {delta.content && <><div className="label" style={{ margin: "10px 0 4px" }}>Jawaban (live)</div>
            <div ref={ref} className="live-text inset caret" style={{ color: "var(--text-base)", fontStyle: "normal" }}>{delta.content}</div></>}
        </div>
      )}
    </div>
  );
}

function ConfirmBox({ run, question, options, onSent, onOpen }: { run: RunSummary; question: string; options?: string[]; onSent: (runId: string) => void; onOpen: (runId: string) => void }) {
  const { client, toast, version } = useApp();
  // Status run asal tetap awaiting_confirmation walau sudah dijawab; cari run lanjutan di percakapan yang sama.
  // Server baru mengisi answered_by_run_id; bila field tidak ada (server lama), cari run lanjutan di percakapan yang sama.
  const hasField = run.answered_by_run_id !== undefined;
  const conv = useLoad(() => (hasField ? Promise.resolve([]) : client.runs({ conversation_id: run.conversation_id, limit: 20 })), [client, run.conversation_id, version, hasField]);
  const answered = hasField
    ? (run.answered_by_run_id ? [{ id: run.answered_by_run_id }] : [])
    : (conv.data ?? []).filter((r) => r.created_at > run.created_at).sort((a, b) => a.created_at - b.created_at);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (answer: string) => {
    if (!answer.trim()) return;
    setBusy(true);
    try {
      const r = await client.chat(answer.trim(), run.conversation_id);
      setText("");
      onSent(r.job.run_id);
    } catch (e) { toast((e as Error).message, true); } finally { setBusy(false); }
  };
  return (
    <section className="card pad-lg confirm">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="label" style={{ color: "var(--warn)" }}>Agent bertanya</span>
        {answered.length > 0 && <button className="btn sm" onClick={() => onOpen(answered[0].id)}>Sudah dijawab → {answered[0].id}</button>}
      </div>
      <div className="q"><RichText text={question} /></div>
      {options && options.length > 0 && (
        <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
          {options.map((o) => <button key={o} className="btn" disabled={busy} onClick={() => send(o)}>{o}</button>)}
        </div>
      )}
      <form className="row" onSubmit={(e) => { e.preventDefault(); void send(text); }}>
        <input className="input grow" placeholder="Tulis jawaban, mis. “50 ribu, parkir”" value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
        <button className="btn primary" disabled={busy || !text.trim()}>{busy ? <span className="spin" /> : <CornerDownLeft size={15} />} Jawab</button>
      </form>
      <div className="tiny faint" style={{ marginTop: 8 }}>Dikirim ke <span className="mono">POST /v1/chat</span> dengan conversation_id <span className="mono">{run.conversation_id}</span></div>
    </section>
  );
}
