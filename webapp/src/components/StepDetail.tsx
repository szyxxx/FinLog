import { useState } from "react";
import type { Step } from "../api/types";
import { dur, num, rp } from "../lib/format";
import { Badge, Json, RichText, Tabs } from "./ui";
import { ValidationChecks } from "./ReceiptViewer";

function Messages({ messages }: { messages: any[] }) {
  return (
    <div>
      {messages.map((m, i) => (
        <div key={i} className="msg">
          <div className={`role role-${m.role}`}>
            {m.role}{m.tool_name && <span className="faint">· {m.tool_name}</span>}
            {m.images && <Badge>{Array.isArray(m.images) ? m.images.length : 1} gambar</Badge>}
            <span className="faint" style={{ marginLeft: "auto", fontWeight: 500, letterSpacing: 0, textTransform: "none" }}>{(m.content ?? "").length} karakter</span>
          </div>
          {m.content ? <pre>{m.content}</pre> : null}
          {m.tool_calls && m.tool_calls.map((tc: any, k: number) => <ToolCall key={k} tc={tc} />)}
        </div>
      ))}
    </div>
  );
}

function ToolCall({ tc }: { tc: any }) {
  const args = tc.function?.arguments;
  return (
    <div className="toolcall">
      <div className="row" style={{ marginBottom: 6 }}><span className="fn">{tc.function?.name}(…)</span><span className="tiny faint mono">{tc.id}</span></div>
      <Json value={typeof args === "string" ? safeParse(args) : args} maxHeight={200} />
    </div>
  );
}

const safeParse = (s: string) => { try { return JSON.parse(s); } catch { return s; } };

function LlmDetail({ step, reasoning }: { step: Step; reasoning?: Step }) {
  const [tab, setTab] = useState<"out" | "prompt" | "think" | "timing" | "raw">("out");
  const o = step.output ?? {};
  const i = step.input ?? {};
  const msgs: any[] = i.messages ?? [];
  const think = reasoning?.output?.thinking;
  return (
    <div>
      <Tabs value={tab} onChange={setTab} items={[
        ["out", "Output"], ["prompt", `Prompt (${msgs.length} pesan)`], ...(think ? [["think", "Reasoning"] as ["think", string]] : []),
        ["timing", "Waktu & token"], ["raw", "JSON mentah"],
      ]} />
      {tab === "out" && (
        <div className="stack" style={{ gap: 10 }}>
          {o.content ? <div className="msg"><div className="role role-assistant">content</div><pre><RichText text={o.content} /></pre></div> : <div className="small faint">(content kosong — model langsung memanggil tool)</div>}
          {(o.tool_calls ?? []).map((tc: any, k: number) => <ToolCall key={k} tc={tc} />)}
          <div className="row wrap small muted" style={{ gap: 14 }}>
            <span>done_reason: <b>{o.done_reason ?? "–"}</b></span>
            {i.format && <span>format: JSON schema</span>}
            {i.tools && <span>tools: {Array.isArray(i.tools) ? i.tools.length : "?"}</span>}
            {o.reasoning_step_seq && <span>reasoning: step #{o.reasoning_step_seq}</span>}
          </div>
        </div>
      )}
      {tab === "prompt" && (
        <div>
          <div className="row wrap small muted" style={{ gap: 14, marginBottom: 10 }}>
            <span>model <b>{i.model}</b></span>
            {i.options && Object.entries(i.options).map(([k, v]) => <span key={k}>{k}=<b>{String(v)}</b></span>)}
          </div>
          <Messages messages={msgs} />
          {Array.isArray(i.tools) && <div className="small muted">Tools ditawarkan: {i.tools.map((t: any) => (typeof t === "string" ? t : t.function?.name)).join(", ")}</div>}
        </div>
      )}
      {tab === "think" && <div className="live-text thinking inset" style={{ maxHeight: 360 }}>{think}</div>}
      {tab === "timing" && <Timing step={step} />}
      {tab === "raw" && <Json value={step} />}
    </div>
  );
}

function Timing({ step }: { step: Step }) {
  const t = step.output?.timings_ms ?? {};
  const total = t.wall ?? step.duration_ms ?? 1;
  const parts: [string, number, string][] = [
    ["load", t.load ?? 0, "#a3a9b5"], ["prompt_eval", t.prompt_eval ?? 0, "#6aaef5"],
    ["thinking", t.thinking ?? 0, "#c792ea"], ["eval (jawaban)", Math.max(0, (t.eval ?? 0) - (t.thinking ?? 0)), "#8b9cff"],
  ];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div style={{ display: "flex", height: 14, borderRadius: 99, overflow: "hidden", background: "var(--canvas-inset)" }}>
        {parts.map(([k, v, c]) => <i key={k} title={`${k} ${dur(v)}`} style={{ width: `${(v / total) * 100}%`, background: c }} />)}
      </div>
      <div className="row wrap small" style={{ gap: 16 }}>
        {parts.map(([k, v, c]) => <span key={k}><span className="sw" style={{ background: c }} />{k} <b>{dur(v)}</b></span>)}
      </div>
      <dl className="kv">
        <dt>Wall</dt><dd>{dur(t.wall ?? step.duration_ms)}</dd>
        <dt>Time to first token</dt><dd>{dur(t.time_to_first_token)}</dd>
        <dt>Prompt tokens</dt><dd>{num(step.prompt_tokens)}</dd>
        <dt>Completion tokens</dt><dd>{num(step.completion_tokens)} <span className="faint">(termasuk thinking)</span></dd>
        <dt>Kecepatan</dt><dd>{num(step.tokens_per_second, 1)} tok/dtk</dd>
      </dl>
    </div>
  );
}

function ToolDetail({ step }: { step: Step }) {
  const o = step.output ?? {};
  return (
    <div className="two-col" style={{ marginTop: 8 }}>
      <div><div className="label" style={{ marginBottom: 6 }}>Argumen dari LLM</div><Json value={step.input} /></div>
      <div>
        <div className="row" style={{ marginBottom: 6 }}>
          <span className="label">Hasil</span>
          {o.ok === true && <Badge tone="ok">ok</Badge>}
          {o.ok === false && <Badge tone="bad">gagal</Badge>}
        </div>
        {o.ok === false && <div className="banner err" style={{ marginBottom: 8 }}>{o.error}{o.instruksi && <> · <i>{o.instruksi}</i></>}</div>}
        {o.transaksi && <div className="small" style={{ marginBottom: 8 }}>Transaksi #{o.transaksi.id}: <b>{rp(o.transaksi.amount)}</b> {o.transaksi.kind} · {o.transaksi.category}</div>}
        <Json value={o} />
      </div>
    </div>
  );
}

export function StepDetail({ step, steps }: { step: Step; steps: Step[] }) {
  const o = step.output ?? {};
  const i = step.input ?? {};
  const meta = (
    <div className="row wrap tiny faint" style={{ gap: 14, margin: "8px 0 4px" }}>
      <span>seq {step.seq}</span><span>id {step.id}</span><span>status {step.status}</span>
      {step.duration_ms !== null && <span>durasi {dur(step.duration_ms)}</span>}
    </div>
  );

  let body: React.ReactNode;
  switch (step.type) {
    case "llm":
      body = <LlmDetail step={step} reasoning={steps.find((s) => s.seq === o.reasoning_step_seq)} />;
      break;
    case "reasoning":
      body = (
        <div>
          <div className="row wrap small muted" style={{ gap: 14, margin: "6px 0 10px" }}>
            <span>{num(o.chars)} karakter</span><span>TTFT {dur(o.time_to_first_token_ms)}</span><span>fase berpikir {dur(step.duration_ms)}</span>
          </div>
          <div className="live-text thinking inset" style={{ maxHeight: 360 }}>{o.thinking}</div>
        </div>
      );
      break;
    case "tool":
      body = <ToolDetail step={step} />;
      break;
    case "validation":
      body = <div style={{ marginTop: 8 }}><div className="row" style={{ marginBottom: 8 }}><Badge tone={o.lolos ? "ok" : "warn"}>{o.lolos ? "lolos" : `gagal: ${(o.gagal ?? []).join(", ")}`}</Badge><span className="small muted">sumber {i.sumber}</span></div><ValidationChecks step={step} /></div>;
      break;
    case "fallback":
      body = (
        <div className="two-col" style={{ marginTop: 8 }}>
          <div><div className="label">Alasan</div><div className="chips" style={{ margin: "6px 0 10px" }}>{(i.alasan ?? []).map((a: string) => <Badge key={a} tone="warn">{a}</Badge>)}</div>
            <div className="small muted">Hasil OCR + LLM ditolak, struk dibaca ulang oleh model vision → <b>{o.mode_baru}</b>.</div></div>
          <div><div className="label" style={{ marginBottom: 6 }}>Hasil OCR-LLM yang ditolak</div><Json value={i.hasil_ocr_llm} maxHeight={240} /></div>
        </div>
      );
      break;
    case "preprocess":
      if (step.name === "transkripsi_suara") {
        body = (
          <div className="stack" style={{ gap: 10, marginTop: 8 }}>
            <div className="msg"><div className="role role-user">transkripsi</div><pre>{o.text}</pre></div>
            <div className="row wrap small muted" style={{ gap: 14 }}>
              <span>bahasa {o.language} ({num((o.language_probability ?? 0) * 100, 0)}%)</span><span>audio {num(o.audio_seconds, 1)} dtk</span>
              {o.engine && <span>{o.engine.model} · {o.engine.device} · {o.engine.compute_type}</span>}
            </div>
            {o.segments?.length > 0 && (
              <table className="tbl"><thead><tr><th>Mulai</th><th>Selesai</th><th>Teks</th><th className="r">avg_logprob</th></tr></thead>
                <tbody>{o.segments.map((sg: any, k: number) => <tr key={k}><td className="mono">{num(sg.start, 2)}s</td><td className="mono">{num(sg.end, 2)}s</td><td>{sg.text}</td><td className="r mono">{num(sg.avg_logprob, 3)}</td></tr>)}</tbody>
              </table>
            )}
          </div>
        );
      } else if (step.name === "baca_email") {
        body = (
          <div className="stack" style={{ gap: 10, marginTop: 8 }}>
            <dl className="kv"><dt>Dari</dt><dd>{o.from}</dd><dt>Ke</dt><dd>{o.to}</dd><dt>Subjek</dt><dd><b>{o.subject}</b></dd><dt>Tanggal</dt><dd>{o.date}</dd><dt>Message-ID</dt><dd className="mono">{o.message_id}</dd><dt>Sumber</dt><dd>{i.sumber}</dd></dl>
            <div className="msg"><div className="role role-user">body ({o.body_type}{o.body_truncated ? ", dipotong" : ""})</div><pre>{o.body}</pre></div>
          </div>
        );
      } else {
        body = (
          <div className="two-col" style={{ marginTop: 8 }}>
            <div><div className="label" style={{ marginBottom: 6 }}>Input</div><Json value={i} maxHeight={260} /></div>
            <div><div className="label" style={{ marginBottom: 6 }}>Data hasil</div><Json value={o.data ?? o} maxHeight={260} /></div>
          </div>
        );
      }
      break;
    case "ocr":
      body = <div className="small muted" style={{ marginTop: 8 }}>{(o.lines ?? []).length} baris, engine <span className="mono">{i.engine}</span>, ukuran {JSON.stringify(o.image_size)}. Overlay bbox ada di panel <b>Struk</b> di atas. <div style={{ marginTop: 8 }}><Json value={{ ...o, boxes: `(${o.boxes?.length ?? 0} kotak)` }} maxHeight={260} /></div></div>;
      break;
    case "confirmation":
      body = <div className="stack" style={{ gap: 8, marginTop: 8 }}><div className="q" style={{ fontWeight: 650 }}>{o.pertanyaan}</div>{o.opsi && <div className="chips">{o.opsi.map((x: string) => <Badge key={x} tone="warn">{x}</Badge>)}</div>}{o.draf_transaksi && <Json value={o.draf_transaksi} />}</div>;
      break;
    case "dedup":
      body = <div className="two-col" style={{ marginTop: 8 }}><div><div className="label" style={{ marginBottom: 6 }}>Kunci</div><Json value={i} /></div><div><div className="label" style={{ marginBottom: 6 }}>Hasil</div><Json value={o} /></div></div>;
      break;
    case "final":
      body = (
        <div className="stack" style={{ gap: 8, marginTop: 8 }}>
          <div className="row"><Badge tone="ok">{o.outcome}</Badge><span className="small muted">status {step.name}</span></div>
          {o.answer && <div className="msg"><pre><RichText text={o.answer} /></pre></div>}
          {o.warnings?.length > 0 && o.warnings.map((w: any, k: number) => <div key={k} className="banner err">{typeof w === "string" ? w : JSON.stringify(w)}</div>)}
        </div>
      );
      break;
    default:
      body = <div className="two-col" style={{ marginTop: 8 }}><div><div className="label" style={{ marginBottom: 6 }}>Input</div><Json value={i} /></div><div><div className="label" style={{ marginBottom: 6 }}>Output</div><Json value={o} /></div></div>;
  }
  return <div>{meta}{body}</div>;
}
