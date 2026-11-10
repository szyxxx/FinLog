import { useEffect, useRef, useState } from "react";
import { Mic, Send, Square, Upload } from "lucide-react";
import { useApp } from "../lib/store";
import { InputIcon } from "./ui";

type Mode = "text" | "receipt" | "voice";

const EXAMPLES = ["beli bensin 30rb tadi pagi", "gajian 4,5 juta", "tadi bayar 50", "pengeluaran makan bulan ini berapa?", "hapus transaksi yang barusan"];

export function InputPanel({ conversationId, onSubmitted }: { conversationId?: string; onSubmitted: (runId: string) => void }) {
  const [mode, setMode] = useState<Mode>("text");
  return (
    <section className="card">
      <div className="card-title"><h2>Input baru</h2><span className="small faint">jadi job di antrean</span></div>
      <div className="seg" style={{ marginBottom: 14 }}>
        {([["text", "Chat"], ["receipt", "Struk"], ["voice", "Suara"]] as [Mode, string][]).map(([k, l]) => (
          <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}><InputIcon type={k} size={14} />{l}</button>
        ))}
      </div>
      {mode === "text" && <ChatInput conversationId={conversationId} onSubmitted={onSubmitted} />}
      {mode === "receipt" && <ReceiptInput onSubmitted={onSubmitted} />}
      {mode === "voice" && <VoiceInput onSubmitted={onSubmitted} />}
    </section>
  );
}

function useSubmit(onSubmitted: (id: string) => void) {
  const { toast, bump } = useApp();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<{ job: { run_id: string; queue_position?: number | null } }>) => {
    setBusy(true);
    try {
      const r = await fn();
      bump();
      onSubmitted(r.job.run_id);
      if ((r.job.queue_position ?? 0) > 1) toast(`Masuk antrean, posisi ${r.job.queue_position}`);
      return true;
    } catch (e) {
      toast((e as Error).message, true);
      return false;
    } finally { setBusy(false); }
  };
  return { busy, run };
}

function ChatInput({ conversationId, onSubmitted }: { conversationId?: string; onSubmitted: (id: string) => void }) {
  const { client } = useApp();
  const [text, setText] = useState("");
  const [cont, setCont] = useState(false);
  const { busy, run } = useSubmit(onSubmitted);
  const send = async () => {
    if (!text.trim()) return;
    if (await run(() => client.chat(text.trim(), cont ? conversationId : null))) setText("");
  };
  return (
    <div className="stack" style={{ gap: 10 }}>
      <textarea className="input" placeholder="mis. “ngopi di warkop 15 ribu” atau “pengeluaran bulan ini berapa?”" value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
      <div className="chips">{EXAMPLES.map((x) => <button key={x} className="chip" onClick={() => setText(x)}>{x}</button>)}</div>
      <label className="row small muted" style={{ gap: 8, cursor: conversationId ? "pointer" : "not-allowed" }}>
        <input type="checkbox" checked={cont && !!conversationId} disabled={!conversationId} onChange={(e) => setCont(e.target.checked)} />
        Lanjutkan percakapan run terpilih {conversationId && <span className="mono tiny faint">{conversationId}</span>}
      </label>
      <button className="btn primary" disabled={busy || !text.trim()} onClick={send}>{busy ? <span className="spin" /> : <Send size={15} />} Kirim</button>
    </div>
  );
}

function DropZone({ accept, onFile, children }: { accept: string; onFile: (f: File) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div className={`dropzone ${over ? "over" : ""}`} role="button" tabIndex={0}
      onClick={() => ref.current?.click()} onKeyDown={(e) => e.key === "Enter" && ref.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) onFile(f); }}>
      <input ref={ref} type="file" accept={accept} hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
      {children}
    </div>
  );
}

const kb = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`);

function ReceiptInput({ onSubmitted }: { onSubmitted: (id: string) => void }) {
  const { client } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const { busy, run } = useSubmit(onSubmitted);
  const pick = (f: File) => { setFile(f); setUrl(URL.createObjectURL(f)); };
  return (
    <div className="stack" style={{ gap: 10 }}>
      {url ? <img className="preview-img" src={url} alt="Pratinjau struk" /> : (
        <DropZone accept="image/jpeg,image/png,image/webp" onFile={pick}><Upload size={20} /><div style={{ marginTop: 6 }}>Seret foto struk ke sini atau klik</div><div className="tiny faint">jpg / png / webp ≤ 10 MB</div></DropZone>
      )}
      {file && <div className="row small muted"><span className="grow mono" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{file.name}</span><span>{kb(file.size)}</span><button className="btn ghost sm" onClick={() => { setFile(null); setUrl(null); }}>Ganti</button></div>}
      <input className="input" placeholder="Catatan (opsional), mis. “belanja bulanan”" value={note} onChange={(e) => setNote(e.target.value)} />
      <button className="btn primary" disabled={busy || !file} onClick={async () => { if (file && await run(() => client.receipt(file, note || undefined))) { setFile(null); setUrl(null); setNote(""); } }}>
        {busy ? <span className="spin" /> : <Send size={15} />} Kirim struk
      </button>
    </div>
  );
}

function VoiceInput({ onSubmitted }: { onSubmitted: (id: string) => void }) {
  const { client, toast } = useApp();
  const [blob, setBlob] = useState<{ data: Blob; name: string } | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [rec, setRec] = useState<MediaRecorder | null>(null);
  const [secs, setSecs] = useState(0);
  const { busy, run } = useSubmit(onSubmitted);

  useEffect(() => {
    if (!rec) return;
    const i = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(i);
  }, [rec]);

  const start = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      toast("Browser tidak mengizinkan mikrofon. Mikrofon hanya jalan di https atau http://localhost; dari IP LAN, unggah file audio.", true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
      const mr = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const type = mr.mimeType || "audio/webm";
        const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "m4a" : "webm";
        const b = new Blob(chunks, { type });
        setBlob({ data: b, name: `rekaman.${ext}` });
        setUrl(URL.createObjectURL(b));
        setRec(null);
      };
      mr.start();
      setSecs(0);
      setRec(mr);
    } catch (e) { toast(`Mikrofon gagal: ${(e as Error).message}`, true); }
  };

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 10 }}>
        {rec ? (
          <button className="btn rec grow" onClick={() => rec.stop()}><Square size={14} /> Stop · {secs}s</button>
        ) : (
          <button className="btn grow" onClick={start}><Mic size={15} /> Rekam suara</button>
        )}
        <DropZone accept="audio/*,.ogg,.opus,.m4a,.mp3,.wav,.webm" onFile={(f) => { setBlob({ data: f, name: f.name }); setUrl(URL.createObjectURL(f)); }}>
          <span className="small">Unggah audio</span>
        </DropZone>
      </div>
      {url && blob && (
        <div className="inset" style={{ padding: 10 }}>
          <div className="row small muted" style={{ marginBottom: 6 }}><span className="grow mono">{blob.name}</span><span>{kb(blob.data.size)}</span></div>
          <audio controls src={url} style={{ width: "100%", height: 36 }} />
        </div>
      )}
      <div className="tiny faint">Contoh ucapan: “beli bakso dua mangkok dua puluh lima ribu”. Diproses Whisper di server lalu agent.</div>
      <button className="btn primary" disabled={busy || !blob || !!rec} onClick={async () => { if (blob && await run(() => client.voice(blob.data, blob.name))) { setBlob(null); setUrl(null); } }}>
        {busy ? <span className="spin" /> : <Send size={15} />} Kirim voice note
      </button>
    </div>
  );
}
