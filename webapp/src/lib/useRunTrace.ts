import { useEffect, useRef, useState } from "react";
import type { ApiClient, Job, RunSummary, Step } from "../api/types";

export interface Pending { type: string; name: string; started_at: number }
export type Deltas = Record<string, { thinking: string; content: string }>;

export interface Trace {
  run: RunSummary | null;
  steps: Step[];
  pending: Pending | null;
  deltas: Deltas;
  job: Job | null;
  error: string | null;
  streaming: boolean;
}

/** Ambil run lalu ikuti SSE /v1/runs/{id}/stream: step, step_started, llm_delta, run_finished. */
export function useRunTrace(client: ApiClient, runId: string | undefined, onFinished?: (r: RunSummary) => void): Trace {
  const [t, setT] = useState<Trace>({ run: null, steps: [], pending: null, deltas: {}, job: null, error: null, streaming: false });
  const finRef = useRef(onFinished);
  finRef.current = onFinished;

  useEffect(() => {
    if (!runId) { setT({ run: null, steps: [], pending: null, deltas: {}, job: null, error: null, streaming: false }); return; }
    let alive = true;
    let close: (() => void) | null = null;
    setT({ run: null, steps: [], pending: null, deltas: {}, job: null, error: null, streaming: true });

    const upsert = (steps: Step[], s: Step) => {
      const i = steps.findIndex((x) => x.seq === s.seq);
      const next = i >= 0 ? steps.map((x, k) => (k === i ? s : x)) : [...steps, s];
      return next.sort((a, b) => a.seq - b.seq);
    };

    client.run(runId).then((r) => {
      if (!alive) return;
      setT((p) => ({ ...p, run: r, steps: (r.steps ?? []).reduce(upsert, p.steps) }));
    }).catch((e) => alive && setT((p) => ({ ...p, error: (e as Error).message })));

    close = client.streamRun(runId, (e) => {
      if (!alive) return;
      setT((p) => {
        switch (e.event) {
          case "step": {
            const s = e.data;
            // Step reasoning/llm menutup pending dengan nama yang sama.
            const clear = p.pending && (p.pending.name === s.name || s.type === "final");
            const keepPending = clear && s.type === "reasoning"; // llm masih menyusul
            return { ...p, steps: upsert(p.steps, s), pending: clear && !keepPending ? null : p.pending };
          }
          case "step_started":
            return { ...p, pending: { type: e.data.type, name: e.data.name, started_at: e.data.started_at } };
          case "llm_delta": {
            const d = e.data;
            const cur = p.deltas[d.step_name] ?? { thinking: "", content: "" };
            // Bergabung di tengah step (step_started terlewat): buat pending dari delta.
            const pending = p.pending && p.pending.name === d.step_name ? p.pending : { type: "llm", name: d.step_name, started_at: Date.now() / 1000 };
            return { ...p, pending, deltas: { ...p.deltas, [d.step_name]: { ...cur, [d.kind]: cur[d.kind] + d.text } } };
          }
          case "run_status":
            return p.run ? { ...p, run: { ...p.run, status: e.data.status } } : p;
          case "job_status":
            return { ...p, job: e.data };
          case "run_finished":
            queueMicrotask(() => finRef.current?.(e.data));
            return { ...p, run: { ...(p.run ?? {}), ...e.data, steps: undefined } as RunSummary, pending: null, streaming: false };
          default:
            return p;
        }
      });
    }, (msg) => alive && setT((p) => (p.streaming ? { ...p, error: msg } : p)));

    return () => { alive = false; close?.(); };
  }, [client, runId]);

  return t;
}
