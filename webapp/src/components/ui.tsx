import { useState, type ReactNode } from "react";
import { AudioLines, Image, Mail, MessageSquareText } from "lucide-react";
import { OUTCOME, STATUS, type Tone, json as toJson } from "../lib/format";

export function Badge({ tone = "muted", children, dot }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return <span className={`badge tone-${tone}`}>{dot && <i className="dot" />}{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "muted" as Tone };
  return <Badge tone={s.tone} dot>{s.label}</Badge>;
}

export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  if (!outcome) return null;
  const o = OUTCOME[outcome] ?? { label: outcome, tone: "muted" as Tone };
  return <Badge tone={o.tone}>{o.label}</Badge>;
}

export function InputIcon({ type, size = 15 }: { type: string; size?: number }) {
  const C = type === "receipt" ? Image : type === "voice" ? AudioLines : type === "email" ? Mail : MessageSquareText;
  return <C size={size} strokeWidth={2} />;
}

/** JSON dengan pewarnaan sintaks sederhana. */
export function Json({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  const text = typeof value === "string" ? value : toJson(value);
  const html = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)/gi, (m, str, colon, bool, n) => {
      if (str) return colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`;
      if (bool) return `<span class="b">${m}</span>`;
      if (n) return `<span class="n">${m}</span>`;
      return m;
    });
  return <pre className="json mono inset" style={maxHeight ? { maxHeight } : undefined} dangerouslySetInnerHTML={{ __html: html }} />;
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: [T, ReactNode][] }) {
  return (
    <div className="tabs" role="tablist">
      {items.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{label}</button>
      ))}
    </div>
  );
}

export function Collapsible({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button className="btn ghost sm" onClick={() => setOpen(!open)}>{open ? "▾" : "▸"} {title}</button>
      {open && <div style={{ marginTop: 6 }}>{children}</div>}
    </div>
  );
}

/** Render teks jawaban: **tebal** dan baris baru. */
export function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : <span key={i}>{p}</span>))}</>;
}
