import { useEffect } from "react";
import { useApp, useLoad } from "../lib/store";
import { InputPanel } from "../components/InputPanel";
import { RunView } from "../components/RunView";
import { InputIcon, OutcomeBadge, StatusBadge } from "../components/ui";
import { ago, dur, INPUT_LABEL, rp } from "../lib/format";
import type { RunSummary } from "../api/types";

export function runTitle(r: RunSummary) {
  if (r.input_type === "text") return r.input_text?.trim() || "(teks kosong)";
  const txt = r.input_text ?? "";
  // /v1/runs (RunListItem) tidak membawa input_meta, jadi judul diambil dari input_text.
  if (r.input_type === "receipt") {
    const m = txt.match(/\{[\s\S]*\}/);
    try {
      const d = m ? JSON.parse(m[0]) : null;
      if (d?.toko || d?.total) return `${r.input_meta?.filename ? `${r.input_meta.filename} · ` : ""}${d.toko ?? "?"} · ${rp(d.total)}`;
    } catch { /* bukan JSON */ }
  }
  // input_meta ringkas dari /v1/runs: {filename} (atau {filename, subject} untuk run email lama).
  if (r.input_type === "email") {
    const subj = r.input_meta?.subject ?? txt.match(/^Subjek: (.*)$/m)?.[1];
    if (subj) return subj;
  }
  const fn = r.input_meta?.filename;
  if (fn) return fn;
  const firstLine = (r.input_text ?? "").split("\n").find((l) => l && !l.startsWith("["));
  return firstLine?.slice(0, 80) || INPUT_LABEL[r.input_type];
}

export function SandboxPage() {
  const { client, route, go, version } = useApp();
  const runs = useLoad(() => client.runs({ limit: 30 }), [client, version]);
  const selected = route.run ?? runs.data?.[0]?.id;
  const selRun = runs.data?.find((r) => r.id === selected);

  useEffect(() => {
    if (!route.run && runs.data?.[0]) go({ page: "sandbox", run: runs.data[0].id });
  }, [route.run, runs.data, go]);

  return (
    <div className="grid-sandbox">
      <div className="stack sticky-col">
        <InputPanel conversationId={selRun?.conversation_id} onSubmitted={(id) => go({ page: "sandbox", run: id })} />
        <section className="card" style={{ padding: 8 }}>
          <div className="card-title" style={{ padding: "8px 8px 0" }}><h2>Run terbaru</h2><button className="btn ghost sm" onClick={() => go({ page: "riwayat" })}>Semua</button></div>
          {runs.error && <div className="banner err" style={{ margin: 8 }}>{runs.error}</div>}
          <div style={{ maxHeight: 430, overflow: "auto" }}>
            {(runs.data ?? []).map((r) => (
              <div key={r.id} className={`run-item ${r.id === selected ? "on" : ""}`} onClick={() => go({ page: "sandbox", run: r.id })}>
                <span className="type-ic"><InputIcon type={r.input_type} size={14} /></span>
                <div style={{ minWidth: 0 }}>
                  <div className="t">{runTitle(r)}</div>
                  <div className="row tiny faint" style={{ gap: 6, marginTop: 3 }}>
                    {r.answered_by_run_id ? <span className="badge tone-ok">Sudah dijawab</span> : <StatusBadge status={r.status} />}<OutcomeBadge outcome={r.outcome} />
                  </div>
                </div>
                <div className="tiny faint" style={{ textAlign: "right" }}><div>{ago(r.created_at)}</div><div>{dur(r.duration_ms)}</div></div>
              </div>
            ))}
            {runs.data && !runs.data.length && <div className="empty">Belum ada run. Kirim input pertama.</div>}
          </div>
        </section>
      </div>
      <div style={{ minWidth: 0 }}>
        {selected ? <RunView runId={selected} openStep={route.step} onSubmitted={(id) => go({ page: "sandbox", run: id })} />
          : <div className="card empty">Pilih atau buat run.</div>}
      </div>
    </div>
  );
}
