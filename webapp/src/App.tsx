import { useEffect, useState } from "react";
import { Activity, FlaskConical, History, Receipt, Settings as Cog, Wallet } from "lucide-react";
import { AppProvider, serverLabel, useApp, type Settings } from "./lib/store";
import { SandboxPage } from "./pages/Sandbox";
import { TransactionsPage } from "./pages/Transactions";
import { HistoryPage } from "./pages/History";
import { SystemPage } from "./pages/System";

function ServerPill({ onClick }: { onClick: () => void }) {
  const { client, settings } = useApp();
  const [ok, setOk] = useState<boolean | null>(null);
  const [q, setQ] = useState(0);
  useEffect(() => {
    let alive = true;
    const f = () => client.health().then((h) => { if (alive) { setOk(true); setQ(h.queue_length); } }).catch(() => alive && setOk(false));
    f();
    const i = setInterval(f, settings.pollMs);
    return () => { alive = false; clearInterval(i); };
  }, [client, settings.pollMs]);
  const color = ok === null ? "var(--text-faint)" : ok ? "var(--ok)" : "var(--bad)";
  return (
    <button className="server-pill" onClick={onClick} title="Ubah server">
      <i style={{ width: 8, height: 8, borderRadius: 9, background: color, boxShadow: `0 0 10px ${color}` }} />
      {settings.mock ? <span>Mode mock</span> : <span className="mono">{serverLabel(settings)}</span>}
      <span className="faint">· antrean {q}</span>
    </button>
  );
}

function SettingsDialog({ onClose }: { onClose: () => void }) {
  const { settings, setSettings, toast } = useApp();
  const [f, setF] = useState<Settings>(settings);
  const [test, setTest] = useState<string | null>(null);
  const check = async () => {
    setTest("menguji…");
    try {
      const h = await fetch(`${f.baseUrl.replace(/\/+$/, "")}/health`).then((r) => r.json());
      const r = await fetch(`${f.baseUrl.replace(/\/+$/, "")}/v1/system`, { headers: f.apiKey ? { "X-API-Key": f.apiKey } : {} });
      const via = f.apiKey ? "key dari Pengaturan" : "key dari proxy (server/.env)";
      setTest(`health ${h.status}, antrean ${h.queue_length}; akses API ${r.ok ? "OK" : `ditolak (${r.status})`} · ${via}`);
    } catch (e) { setTest(`gagal: ${(e as Error).message}. Cek URL, firewall (ufw allow 8000/tcp), dan jaringan yang sama.`); }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Pengaturan server</h2>
        <p className="small muted" style={{ margin: "0 0 16px" }}>Disimpan di localStorage browser ini. Pindah ke laptop lain cukup ganti URL.</p>
        <div className="stack" style={{ gap: 12 }}>
          <label className="field"><span>URL server</span><input className="input mono" value={f.baseUrl} onChange={(e) => setF({ ...f, baseUrl: e.target.value })} placeholder="kosong = lewat web app ini (disarankan)" /></label>
          <div className="tiny faint" style={{ marginTop: -6 }}>Kosongkan agar web app meneruskan request ke server di laptop yang sama (proxy). Isi hanya bila server di mesin lain, mis. http://10.31.104.83:8000.</div>
          <label className="field"><span>API key (opsional)</span><input className="input mono" type="password" value={f.apiKey} onChange={(e) => setF({ ...f, apiKey: e.target.value })} placeholder="kosong = key server disisipkan proxy" /></label>
          <label className="field"><span>Interval pembaruan (detik)</span><input className="input" type="number" min={1} max={60} value={f.pollMs / 1000} onChange={(e) => setF({ ...f, pollMs: Math.max(1, Number(e.target.value)) * 1000 })} /></label>
          <label className="row small" style={{ gap: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={f.mock} onChange={(e) => setF({ ...f, mock: e.target.checked })} />
            <span><b>Mode mock</b> <span className="muted">— data contoh lokal, tanpa server (untuk latihan demo)</span></span>
          </label>
          {test && <div className="banner mock">{test}</div>}
        </div>
        <div className="row" style={{ justifyContent: "space-between", marginTop: 20 }}>
          <button className="btn" onClick={check} disabled={f.mock}>Uji koneksi</button>
          <div className="row"><button className="btn ghost" onClick={onClose}>Batal</button>
            <button className="btn primary" onClick={() => { setSettings(f); toast(f.mock ? "Mode mock aktif" : `Terhubung ke ${serverLabel(f)}`); onClose(); }}>Simpan</button></div>
        </div>
      </div>
    </div>
  );
}

/** Banner untuk tamu yang masuk lewat link publik (?key=) atau tunnel. Bisa ditutup per sesi browser. */
function GuestBanner() {
  const { settings } = useApp();
  const [hidden, setHidden] = useState(() => { try { return sessionStorage.getItem("kas-sandbox.guest-banner") === "0"; } catch { return false; } });
  const isGuest = settings.guest || location.hostname.endsWith(".trycloudflare.com");
  if (!isGuest || hidden) return null;
  return (
    <div className="banner mock" role="note" style={{ marginBottom: 16, justifyContent: "space-between" }}>
      <span><b>Mode tamu.</b> Data bersama untuk demo - jangan masukkan data keuangan asli. Server satu GPU: request diproses bergantian (±15–50 dtk).</span>
      <button className="btn ghost sm" aria-label="Tutup" onClick={() => { setHidden(true); try { sessionStorage.setItem("kas-sandbox.guest-banner", "0"); } catch { /* abaikan */ } }}>Tutup</button>
    </div>
  );
}

function Shell() {
  const { route, go, toasts, settings } = useApp();
  const [showSettings, setShowSettings] = useState(false);
  const tabs = [
    ["sandbox", "Sandbox", FlaskConical], ["transaksi", "Transaksi", Wallet], ["riwayat", "Riwayat", History], ["sistem", "Sistem", Activity],
  ] as const;
  return (
    <>
      <header className="topbar">
        <div className="brand"><span className="brand-mark"><Receipt size={15} strokeWidth={2.4} /></span>Kas Agent<small>sandbox</small></div>
        <nav className="nav">
          {tabs.map(([k, l, I]) => (
            <button key={k} className={route.page === k ? "on" : ""} onClick={() => go({ page: k })}><I size={15} /><span>{l}</span></button>
          ))}
        </nav>
        <span className="spacer" />
        <ServerPill onClick={() => setShowSettings(true)} />
        <button className="icon-btn" aria-label="Pengaturan" onClick={() => setShowSettings(true)}><Cog size={17} /></button>
      </header>
      <main className="page">
        <GuestBanner />
        {settings.mock && route.page === "sandbox" && (
          <div className="banner mock" style={{ marginBottom: 16 }}>Mode mock: data contoh (trace nyata dari database server + skenario sintetis), tanpa jaringan. Matikan di Pengaturan untuk memakai server sungguhan.</div>
        )}
        {route.page === "sandbox" && <SandboxPage />}
        {route.page === "transaksi" && <TransactionsPage />}
        {route.page === "riwayat" && <HistoryPage />}
        {route.page === "sistem" && <SystemPage />}
      </main>
      {showSettings && <SettingsDialog onClose={() => setShowSettings(false)} />}
      <div className="toast-wrap">{toasts.map((t) => <div key={t.id} className={`toast ${t.bad ? "bad" : ""}`}>{t.text}</div>)}</div>
    </>
  );
}

export default function App() {
  return <AppProvider><Shell /></AppProvider>;
}
