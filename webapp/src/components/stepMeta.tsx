import {
  AlertOctagon, Brain, CircleCheckBig, Copy, FileInput, Flag, GitBranch, History, MessageCircleQuestion, ScanText,
  ShieldAlert, ShieldCheck, Sparkles, Wand2, Wrench, ListChecks,
} from "lucide-react";
import type { Step } from "../api/types";

type Meta = { label: string; color: string; Icon: typeof Brain };

export const STEP_META: Record<string, Meta> = {
  input: { label: "Input", color: "#a3a9b5", Icon: FileInput },
  preprocess: { label: "Praproses", color: "#62c8d6", Icon: Wand2 },
  ocr: { label: "OCR", color: "#62c8d6", Icon: ScanText },
  rules: { label: "Aturan", color: "#62c8d6", Icon: ListChecks },
  validation: { label: "Validasi", color: "#6fd3b8", Icon: ShieldCheck },
  fallback: { label: "Fallback", color: "#f5b454", Icon: GitBranch },
  reasoning: { label: "Reasoning", color: "#c792ea", Icon: Brain },
  llm: { label: "LLM", color: "#8b9cff", Icon: Sparkles },
  tool: { label: "Tool", color: "#f2c86f", Icon: Wrench },
  dedup: { label: "Anti-dobel", color: "#a3a9b5", Icon: Copy },
  context: { label: "Konteks", color: "#a3a9b5", Icon: History },
  guard: { label: "Guard", color: "#f59e6b", Icon: ShieldAlert },
  confirmation: { label: "Konfirmasi", color: "#f5b454", Icon: MessageCircleQuestion },
  final: { label: "Final", color: "#5fd39a", Icon: Flag },
  error: { label: "Error", color: "#f2727f", Icon: AlertOctagon },
};

export const stepMeta = (type: string): Meta => STEP_META[type] ?? { label: type, color: "#a3a9b5", Icon: CircleCheckBig };

export function StepIcon({ type, status }: { type: string; status?: string }) {
  const m = stepMeta(type);
  const color = status === "error" ? "#f2727f" : status === "warning" ? "#f5b454" : m.color;
  return (
    <span className="st-ic" style={{ background: `${color}1f`, color }}>
      <m.Icon size={15} strokeWidth={2.1} />
    </span>
  );
}

/** Ringkasan satu baris untuk kolom "ringkas" di timeline. */
export function stepGist(s: Step): string {
  const o = s.output ?? {};
  const i = s.input ?? {};
  try {
    switch (s.type) {
      case "input": return i.text ?? i.filename ?? i.file ?? "";
      case "reasoning": return (o.thinking ?? "").replace(/\s+/g, " ").slice(0, 120);
      case "llm": {
        const tc = o.tool_calls?.map((t: any) => t.function?.name).join(", ");
        return tc ? `→ ${tc}` : (o.content ?? "").replace(/\s+/g, " ").slice(0, 120);
      }
      case "tool": return o.ok === false ? `gagal: ${o.error}` : Object.entries(i).map(([k, v]) => `${k}=${typeof v === "object" ? "…" : v}`).join(" ").slice(0, 120);
      case "ocr": return `${o.lines?.length ?? o.n_boxes ?? 0} baris · conf ${(o.mean_conf ?? 0).toFixed(3)}`;
      case "validation": return o.lolos ? "lolos semua cek" : `gagal: ${(o.gagal ?? []).join(", ")}`;
      case "fallback": return `alasan: ${(i.alasan ?? []).join(", ")} → ${o.mode_baru ?? ""}`;
      case "preprocess":
        if (s.name === "ekstrak_struk") return `${o.data?.toko ?? "?"} · ${o.data?.total ?? "?"} · ${o.data?.sumber_ekstraksi ?? o.mode ?? ""}`;
        if (s.name === "transkripsi_suara") return `"${(o.text ?? "").slice(0, 100)}"`;
        if (s.name === "baca_email") return `${o.subject ?? ""} — ${o.from ?? ""}`;
        return "";
      case "dedup": return s.name === "cek_duplikat" ? (o.duplikat ? `DUPLIKAT dari ${o.run_sebelumnya}` : "bukan duplikat") : (o.serupa ? "struk serupa ditemukan" : "tidak ada struk serupa");
      case "context": return `lanjutan dari ${i.run_sebelumnya}`;
      case "guard": return o.pesan ?? o.tindakan ?? "";
      case "confirmation": return o.pertanyaan ?? "";
      case "final": return `${o.outcome ?? s.name}${o.warnings?.length ? ` · ${o.warnings.length} peringatan` : ""}`;
      case "error": return o.error ?? "";
      default: return "";
    }
  } catch {
    return "";
  }
}
