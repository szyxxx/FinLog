import { useEffect, useRef, useState } from "react";
import type { Health, Metrics } from "../api/types";
import { useApp, useLoad } from "../lib/store";
import { INPUT_LABEL, STATUS, clock, dur, num } from "../lib/format";
import { Badge, Collapsible, Json } from "../components/ui";

interface Sample { t: number; tps: number | null; vram: number | null; util: number | null; queue: number }

function Spark({ data, max, color = "var(--accent)", h = 44 }: { data: (number | null)[]; max?: number; color?: string; h?: number }) {
  const vals = data.map((v) => v ?? 0);
  const m = max ?? Math.max(1, ...vals);
  const W = 240;
  if (vals.length < 2) return <svg viewBox={`0 0 ${W} ${h}`} width="100%" height={h} />;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * W},${h - 2 - (v / m) * (h - 6)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${h}`} width="100%" height={h} preserveAspectRatio="none" aria-hidden>
      <polyline points={`0,${h} ${pts} ${W},${h}`} fill={color} opacity={0.12} stroke="none" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function SystemPage() {
  const { client, settings } = useApp();
  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const [m, setM] = useState<Metrics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [last, setLast] = useState<number | null>(null);
  const hist = useRef<Sample[]>([]);
  const sys = useLoad(() => client.system(), [client]);

  useEffect(() => {
    hist.current = [];
    let alive = true;
    const poll = async () => {
      const t0 = performance.now();
      try { const h = await client.health(); if (alive) { setHealth(h); setHealthErr(null); } } catch (e) { if (alive) setHealthErr((e as Error).message); }
      try {
        const mm = await client.metrics(200);
        if (!alive) return;
        const g = mm.gpu?.gpus?.[0];
        hist.current = [...hist.current, { t: Date.now(), tps: mm.llm.tokens_per_second_avg, vram: g?.memory_used_mib ?? null, util: g?.utilization_pct ?? null, queue: mm.queue.length }].slice(-60);
        setM(mm); setErr(null); setLast(Math.round(performance.now() - t0));
      } catch (e) { if (alive) setErr((e as Error).message); }
    };
    void poll();
    const i = setInterval(poll, settings.pollMs);
    return () => { alive = false; clearInterval(i); };
  }, [client, settings.pollMs]);

  const g = m?.gpu?.gpus?.[0];
  const h = hist.current;
  const vramPct = g && g.memory_used_mib !== null && g.memory_total_mib ? (g.memory_used_mib / g.memory_total_mib) * 100 : 0;

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head">
        <div><h1>Sistem</h1><p>Health, GPU/VRAM, kecepatan LLM, dan antrean dari <span className="mono">/health</span> + <span className="mono">/v1/metrics</span>, diperbarui tiap {settings.pollMs / 1000} dtk.</p></div>
        <div className="row small muted">{last !== null && <span>respons {last} ms</span>}<Badge tone={healthErr ? "bad" : health ? "ok" : "muted"} dot>{healthErr ? "server tidak terjangkau" : health ? `health ${health.status}` : "memeriksa…"}</Badge></div>
      </div>
      {(err || healthErr) && <div className="banner err">{healthErr ?? err}</div>}

      <div className="stat-grid">
        <section className="card pad-lg">
          <div className="label">VRAM GPU</div>
          <div className="gauge-num" style={{ marginTop: 8 }}>{g?.memory_used_mib != null ? num(g.memory_used_mib / 1024, 1) : "–"}<span className="muted" style={{ fontSize: 16, fontWeight: 600 }}> / {g?.memory_total_mib != null ? num(g.memory_total_mib / 1024, 1) : "–"} GB</span></div>
          <div className="meter" style={{ margin: "12px 0 8px", height: 8 }}><i style={{ width: `${vramPct}%`, background: vramPct > 92 ? "var(--bad)" : vramPct > 80 ? "var(--warn)" : "var(--accent)" }} /></div>
          <Spark data={h.map((s) => s.vram)} max={g?.memory_total_mib ?? undefined} />
        </section>
        <section className="card pad-lg">
          <div className="label">Kecepatan LLM</div>
          <div className="gauge-num" style={{ marginTop: 8 }}>{num(m?.llm.tokens_per_second_avg, 1)}<span className="muted" style={{ fontSize: 16, fontWeight: 600 }}> tok/dtk</span></div>
          <div className="small faint" style={{ margin: "6px 0 4px" }}>p50 {num(m?.llm.tokens_per_second_p50, 1)} · {num(m?.llm.calls)} panggilan · rata² step {dur(m?.llm.llm_step_ms_avg)}</div>
          <Spark data={h.map((s) => s.tps)} />
        </section>
        <section className="card pad-lg">
          <div className="label">Utilisasi GPU</div>
          <div className="gauge-num" style={{ marginTop: 8 }}>{g?.utilization_pct ?? "–"}<span className="muted" style={{ fontSize: 16, fontWeight: 600 }}>%</span></div>
          <div className="small faint" style={{ margin: "6px 0 4px" }}>{g ? `${g.temperature_c ?? "?"}°C · ${g.power_w ?? "?"} W` : m?.gpu?.reason ?? "GPU tidak tersedia"}</div>
          <Spark data={h.map((s) => s.util)} max={100} />
        </section>
        <section className="card pad-lg">
          <div className="label">Antrean</div>
          <div className="gauge-num" style={{ marginTop: 8 }}>{m?.queue.length ?? health?.queue_length ?? "–"}<span className="muted" style={{ fontSize: 16, fontWeight: 600 }}> job</span></div>
          <div className="small faint" style={{ margin: "6px 0 4px" }}>{m?.queue.current_job ? `memproses ${m.queue.current_job.id}` : "idle"}</div>
          <Spark data={h.map((s) => s.queue)} max={Math.max(3, ...h.map((s) => s.queue))} />
        </section>
      </div>

      <div className="two-col">
        <section className="card pad-lg">
          <div className="card-title"><h3>Latensi per jenis input</h3><span className="small faint">{m?.window_runs ?? 0} run terakhir</span></div>
          <table className="tbl">
            <thead><tr><th>Input</th><th className="r">Run</th><th className="r">Error</th><th className="r">Rata²</th><th className="r">p50</th><th className="r">p95</th><th className="r">Tunggu antre</th></tr></thead>
            <tbody>
              {Object.entries(m?.latency_by_input_type ?? {}).map(([k, v]) => (
                <tr key={k}><td>{INPUT_LABEL[k] ?? k}</td><td className="r">{v.runs}</td><td className="r">{v.errors}</td><td className="r">{dur(v.latency_ms.avg)}</td><td className="r">{dur(v.latency_ms.p50)}</td><td className="r">{dur(v.latency_ms.p95)}</td><td className="r">{dur(v.queue_wait_ms_avg)}</td></tr>
              ))}
            </tbody>
          </table>
          <div className="row wrap" style={{ gap: 8, marginTop: 14 }}>
            {Object.entries(m?.runs_by_status ?? {}).map(([k, v]) => <Badge key={k} tone={STATUS[k]?.tone ?? "muted"}>{STATUS[k]?.label ?? k}: {v}</Badge>)}
          </div>
          <div className="small muted" style={{ marginTop: 12 }}>Token total: prompt {num(m?.llm.prompt_tokens_total)} · completion {num(m?.llm.completion_tokens_total)}</div>
        </section>
        <section className="card pad-lg">
          <div className="card-title"><h3>Model & runtime</h3></div>
          <dl className="kv">
            <dt>LLM agent</dt><dd><b>{sys.data?.llm_model ?? "–"}</b> {sys.data?.think !== undefined && <Badge tone={sys.data.think ? "info" : "muted"}>thinking {sys.data.think ? "aktif" : "mati"}</Badge>}</dd>
            <dt>Vision</dt><dd>{sys.data?.vision_model ?? "–"} {sys.data?.vision_think !== undefined && <span className="faint">(thinking {sys.data.vision_think === null ? "ikut agent" : sys.data.vision_think ? "aktif" : "mati"})</span>}</dd>
            <dt>Ekstraksi struk</dt><dd>{sys.data?.receipt_mode ? <><Badge tone="info">{sys.data.receipt_mode}</Badge> <span className="faint">fallback vision {sys.data.receipt_fallback_vision ? "aktif" : "mati"} · rules first {sys.data.receipt_rules_first ? "aktif" : "mati"}</span></> : "–"}</dd>
            <dt>Kapabilitas</dt><dd>{Object.entries(sys.data?.model_capabilities ?? {}).map(([k, v]) => <div key={k}>{k}: <span className="muted">{Array.isArray(v) ? v.join(", ") : String(v)}</span></div>)}</dd>
            <dt>Speech-to-text</dt><dd>faster-whisper {m?.stt?.model ?? sys.data?.whisper_model} · {m?.stt?.device ?? sys.data?.whisper_device} · {m?.stt?.compute_type ?? ""}{m?.stt?.real_time_factor_avg ? ` · RTF ${m.stt.real_time_factor_avg}` : ""}</dd>
            <dt>Dimuat di Ollama</dt><dd>{(m?.ollama_loaded_models ?? []).map((x) => <div key={x.name}>{x.name} · {num(x.size_vram_bytes / 1024 ** 3, 2)} GB VRAM · ctx {num(x.context_length)}{x.expires_at ? <span className="faint"> · lepas {clock(Date.parse(x.expires_at) / 1000)}</span> : null}</div>)}{!m?.ollama_loaded_models?.length && <span className="faint">tidak ada (model belum dimuat)</span>}</dd>
            <dt>GPU</dt><dd>{g?.name ?? "–"}</dd>
            <dt>Proses GPU</dt><dd>{(m?.gpu?.processes ?? []).map((p: any, i: number) => <div key={i} className="small">{p.name ?? p.process_name ?? "?"} <span className="faint">pid {p.pid}</span> · {typeof p.used_memory_mib === "number" ? `${num(p.used_memory_mib)} MiB` : String(p.used_memory_mib ?? "?")}</div>)}</dd>
          </dl>
        </section>
      </div>

      <section className="card pad-lg">
        <div className="card-title"><h3>Tools agent</h3><span className="small faint">dari /v1/system → tools</span></div>
        <div className="three-col" style={{ gap: 12 }}>
          {(sys.data?.tools ?? []).map((t, i) => {
            const f = t.function ?? (t as any);
            return (
              <div key={i} className="inset" style={{ padding: 12 }}>
                <div className="mono" style={{ fontWeight: 600, color: "var(--accent)" }}>{f.name}</div>
                <div className="small muted" style={{ margin: "4px 0 8px" }}>{f.description}</div>
                <div className="tiny faint mono">{Object.keys(f.parameters?.properties ?? {}).join(", ")}</div>
              </div>
            );
          })}
        </div>
        {sys.error && <div className="banner err">{sys.error}</div>}
        <div style={{ marginTop: 12 }}><Collapsible title="JSON mentah /v1/metrics"><Json value={m} /></Collapsible></div>
      </section>
    </div>
  );
}
