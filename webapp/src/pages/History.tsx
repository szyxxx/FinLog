import { useEffect, useState } from "react";
import { useApp, useLoad } from "../lib/store";
import { ACTIVE_INPUTS, INPUT_LABEL, OUTCOME, STATUS, ago, clock, dur, num } from "../lib/format";
import { Badge, InputIcon, OutcomeBadge, StatusBadge } from "../components/ui";
import { runTitle } from "./Sandbox";

export function HistoryPage() {
  const { client, version, go, settings } = useApp();
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [outcome, setOutcome] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => { const i = setInterval(() => setTick((t) => t + 1), settings.pollMs); return () => clearInterval(i); }, [settings.pollMs]);
  const runs = useLoad(() => client.runs({ input_type: type, status, outcome, limit: 100 }), [client, type, status, outcome, version]);
  const jobs = useLoad(() => client.jobs(undefined, 30), [client, version, tick]);

  const q = jobs.data;
  const active = (q?.jobs ?? []).filter((j) => j.status === "queued" || j.status === "running").sort((a, b) => (a.queue_position ?? 99) - (b.queue_position ?? 99));

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head"><div><h1>Riwayat & antrean</h1><p>Run agent (<span className="mono">/v1/runs</span>) dan antrean job FIFO satu GPU (<span className="mono">/v1/jobs</span>).</p></div></div>

      <section className="card pad-lg">
        <div className="card-title"><h2>Antrean job</h2><span className="small muted">panjang antrean <b style={{ color: "var(--text-base)" }}>{q?.queue_length ?? "–"}</b> · diperbarui tiap {settings.pollMs / 1000} dtk</span></div>
        {q?.current_job ? (
          <div className="row inset" style={{ padding: 12, marginBottom: 12, gap: 12 }}>
            <span className="spin" /><span className="type-ic"><InputIcon type={q.current_job.input_type} /></span>
            <div className="grow"><b>Sedang diproses</b> <span className="mono small faint">{q.current_job.id}</span><div className="small muted">mulai {clock(q.current_job.started_at)} · tunggu {dur(q.current_job.wait_ms)}</div></div>
            <button className="btn sm" onClick={() => go({ page: "sandbox", run: q.current_job!.run_id })}>Lihat trace live</button>
          </div>
        ) : <div className="small muted" style={{ marginBottom: 12 }}>GPU idle: tidak ada job yang sedang diproses.</div>}
        {active.filter((j) => j.status === "queued").length > 0 && (
          <div className="row wrap" style={{ gap: 8, marginBottom: 14 }}>
            {active.filter((j) => j.status === "queued").map((j) => <Badge key={j.id} tone="muted">#{j.queue_position} · {INPUT_LABEL[j.input_type]} · {j.id}</Badge>)}
          </div>
        )}
        <table className="tbl">
          <thead><tr><th>Job</th><th>Input</th><th>Status</th><th>Posisi</th><th>Dibuat</th><th className="r">Tunggu</th><th className="r">Proses</th></tr></thead>
          <tbody>
            {(q?.jobs ?? []).slice(0, 12).map((j) => (
              <tr key={j.id} className="clickable" onClick={() => go({ page: "sandbox", run: j.run_id })}>
                <td className="mono small">{j.id}</td>
                <td><span className="row" style={{ gap: 6 }}><InputIcon type={j.input_type} size={13} />{INPUT_LABEL[j.input_type]}</span></td>
                <td><StatusBadge status={j.status} /></td>
                <td className="small">{j.queue_position === 0 ? "diproses" : j.queue_position ?? "–"}</td>
                <td className="small muted">{ago(j.created_at)}</td>
                <td className="r small">{dur(j.wait_ms)}</td>
                <td className="r small">{dur(j.run_ms)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {jobs.error && <div className="banner err">{jobs.error}</div>}
      </section>

      <section className="card">
        <div className="row wrap" style={{ padding: "6px 6px 14px", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 15, marginRight: 8 }}>Run</h2>
          <select className="input" style={{ width: 150 }} value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Semua input</option>{ACTIVE_INPUTS.map((k) => <option key={k} value={k}>{INPUT_LABEL[k]}</option>)}
          </select>
          <select className="input" style={{ width: 200 }} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Semua status</option>{Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <select className="input" style={{ width: 190 }} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">Semua outcome</option>{Object.entries(OUTCOME).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
          <span className="grow" /><span className="small muted">{runs.data?.length ?? 0} run</span>
        </div>
        {runs.error && <div className="banner err">{runs.error}</div>}
        <table className="tbl">
          <thead><tr><th>Waktu</th><th>Input</th><th>Isi</th><th>Status</th><th>Outcome</th><th className="r">Durasi</th><th className="r">LLM / tool</th><th className="r">Token</th></tr></thead>
          <tbody>
            {(runs.data ?? []).map((r) => (
              <tr key={r.id} className="clickable" onClick={() => go({ page: "sandbox", run: r.id })}>
                <td className="small" style={{ whiteSpace: "nowrap" }}>{clock(r.created_at)}<div className="tiny faint">{ago(r.created_at)}</div></td>
                <td><span className="row" style={{ gap: 6 }}><InputIcon type={r.input_type} size={13} />{INPUT_LABEL[r.input_type]}</span></td>
                <td style={{ maxWidth: 420 }}><div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{runTitle(r)}</div><div className="tiny faint mono">{r.id}</div></td>
                <td><StatusBadge status={r.status} /></td>
                <td><OutcomeBadge outcome={r.outcome} /></td>
                <td className="r small">{dur(r.duration_ms)}</td>
                <td className="r small">{r.llm_calls} / {r.tool_calls}</td>
                <td className="r small">{num(r.prompt_tokens + r.completion_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
