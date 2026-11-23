import { useState } from "react";
import { Bell, CalendarClock, Check, FileText, Play, Plus, Trash2, Wallet } from "lucide-react";
import type { Bill, BudgetRow, Notif } from "../api/types";
import { useApp, useLoad } from "../lib/store";
import { CAT_LABEL, catLabel, dateLabel, rp, type Tone } from "../lib/format";
import { Badge } from "../components/ui";

const EXPENSE_CATS = ["_total", "makanan_minuman", "transportasi", "belanja", "tagihan", "kesehatan", "pendidikan", "hiburan",
  "transfer_keluar", "lainnya_keluar"];

const BUDGET_TONE: Record<BudgetRow["status"], Tone> = { aman: "ok", waspada: "warn", terlampaui: "bad", diproyeksikan_lewat: "warn" };
const LEVEL_TONE: Record<string, Tone> = { info: "info", warning: "warn", danger: "bad" };

export function AutomationPage() {
  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head">
        <div>
          <h1>Otomasi</h1>
          <p>Layanan yang berjalan sendiri di server: pengingat tagihan, peringatan anggaran, deteksi langganan, dan laporan PDF
            yang ditulis model lokal. Semua juga bisa diminta lewat chat di Sandbox.</p>
        </div>
      </div>
      <div className="two-col">
        <Notifications />
        <Schedules />
      </div>
      <Budgets />
      <Bills />
      <Reports />
    </div>
  );
}

function Notifications() {
  const { client, version, bump, toast } = useApp();
  const n = useLoad(() => client.notifications(), [client, version]);
  const list: Notif[] = n.data?.notifications ?? [];
  const readAll = async () => { try { await client.readNotifications(); bump(); } catch (e) { toast((e as Error).message, true); } };
  return (
    <section className="card pad-lg">
      <div className="card-title">
        <h3 className="row" style={{ gap: 8 }}><Bell size={16} /> Notifikasi {n.data?.unread ? <Badge tone="warn">{n.data.unread} baru</Badge> : null}</h3>
        {(n.data?.unread ?? 0) > 0 && <button className="btn ghost sm" onClick={readAll}><Check size={14} /> Tandai dibaca</button>}
      </div>
      {n.error && <div className="banner err">{n.error}</div>}
      <div className="stack" style={{ gap: 8, maxHeight: 360, overflow: "auto" }}>
        {list.map((x) => (
          <div key={x.id} className={`notif ${x.read ? "read" : ""}`}>
            <div className="row" style={{ gap: 8, justifyContent: "space-between" }}>
              <b className="small">{x.title}</b>
              <Badge tone={LEVEL_TONE[x.level] ?? "muted"}>{x.kind}</Badge>
            </div>
            {x.body && <div className="small muted" style={{ marginTop: 4 }}>{x.body}</div>}
            <div className="tiny faint" style={{ marginTop: 4 }}>{x.created_at.replace("T", " ").slice(0, 16)}
              {x.data?.url && <> · <a href={client.fileUrl(x.data.url)} target="_blank" rel="noreferrer">buka PDF</a></>}</div>
          </div>
        ))}
        {!n.loading && !list.length && <div className="empty">Belum ada notifikasi.</div>}
      </div>
    </section>
  );
}

function Schedules() {
  const { client, version, bump, toast, go } = useApp();
  const a = useLoad(() => client.automations(), [client, version]);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (name: string) => {
    setBusy(name);
    try {
      const r = await client.runAutomation(name);
      if (r.status === "error") toast(`Gagal: ${r.error}`, true);
      else if (r.run_id) { toast("Job laporan masuk antrean; buka Sandbox untuk melihat agen bekerja"); go({ page: "sandbox", run: r.run_id }); }
      else toast(`${name} selesai`);
      bump();
    } catch (e) { toast((e as Error).message, true); } finally { setBusy(null); }
  };
  return (
    <section className="card pad-lg">
      <div className="card-title">
        <h3 className="row" style={{ gap: 8 }}><CalendarClock size={16} /> Jadwal otomasi</h3>
        <span className="small faint">{a.data ? (a.data.enabled ? `aktif · ${a.data.timezone}` : "nonaktif (AUTOMATION_ENABLED=false)") : ""}</span>
      </div>
      {a.error && <div className="banner err">{a.error}</div>}
      <div className="tbl-wrap"><table className="tbl card-table sched-table">
        <thead><tr><th>Tugas</th><th>Terakhir</th><th>Berikutnya</th><th /></tr></thead>
        <tbody>
          {(a.data?.tasks ?? []).map((t) => (
            <tr key={t.name}>
              <td><b className="small">{t.label}</b>{t.pakai_llm && <> <Badge tone="info">LLM</Badge></>}<div className="tiny faint">{t.jadwal}</div></td>
              <td className="small" data-label="Terakhir">{t.terakhir_jalan ? t.terakhir_jalan.replace("T", " ").slice(5, 16) : "–"}
                {t.status_terakhir && <div><Badge tone={t.status_terakhir === "ok" ? "ok" : "bad"}>{t.status_terakhir}</Badge></div>}</td>
              <td className="small" data-label="Berikutnya">{t.jadwal_berikutnya.replace("T", " ").slice(5, 16)}</td>
              <td className="r"><button className="btn sm" disabled={busy === t.name} onClick={() => run(t.name)}><Play size={13} /> Jalankan</button></td>
            </tr>
          ))}
        </tbody>
      </table></div>
    </section>
  );
}

function Budgets() {
  const { client, version, bump, toast } = useApp();
  const b = useLoad(() => client.budgets(), [client, version]);
  const [form, setForm] = useState({ kategori: "makanan_minuman", batas: "", ambang: "80" });
  const save = async () => {
    const batas = Number(form.batas.replace(/\D/g, ""));
    if (!batas) { toast("Isi batas anggaran (rupiah)", true); return; }
    try { await client.setBudget(form.kategori, batas, Number(form.ambang) || 80); setForm({ ...form, batas: "" }); bump(); toast("Anggaran disimpan"); }
    catch (e) { toast((e as Error).message, true); }
  };
  const del = async (k: string) => { try { await client.deleteBudget(k); bump(); } catch (e) { toast((e as Error).message, true); } };
  return (
    <section className="card pad-lg">
      <div className="card-title">
        <h3 className="row" style={{ gap: 8 }}><Wallet size={16} /> Anggaran bulan ini</h3>
        <span className="small faint">{b.data ? `${b.data.bulan} · hari ke-${b.data.hari_ke} dari ${b.data.jumlah_hari}` : ""}</span>
      </div>
      {b.error && <div className="banner err">{b.error}</div>}
      <div className="stack" style={{ gap: 14 }}>
        {(b.data?.anggaran ?? []).map((x) => {
          const pct = Math.min(100, x.persen);
          const proj = Math.min(100, (x.proyeksi_akhir_bulan / x.batas) * 100);
          const color = x.status === "terlampaui" ? "var(--bad)" : x.status === "aman" ? "var(--ok)" : "var(--warn)";
          return (
            <div key={x.kategori} className="budget-row">
              <div className="row" style={{ justifyContent: "space-between", gap: 8 }}>
                <span className="row" style={{ gap: 8 }}><b className="small">{catLabel(x.kategori)}</b><Badge tone={BUDGET_TONE[x.status]}>{x.status.replace("_", " ")}</Badge></span>
                <span className="small" style={{ fontVariantNumeric: "tabular-nums" }}>{rp(x.terpakai)} / {rp(x.batas)} · <b>{x.persen.toLocaleString("id-ID")}%</b>
                  <button className="btn ghost sm" aria-label={`Hapus anggaran ${catLabel(x.kategori)}`} onClick={() => del(x.kategori)}><Trash2 size={13} /></button></span>
              </div>
              <div className="budget-track" title={`Proyeksi akhir bulan ${rp(x.proyeksi_akhir_bulan)}`}>
                <i className="proj" style={{ width: `${proj}%` }} />
                <i style={{ width: `${pct}%`, background: color }} />
                <span className="mark" style={{ left: `${x.ambang_persen}%` }} />
              </div>
              <div className="tiny faint">Sisa {rp(x.sisa)} · aman {rp(x.sisa_per_hari)}/hari · proyeksi akhir bulan {rp(x.proyeksi_akhir_bulan)} · peringatan di {x.ambang_persen}%</div>
            </div>
          );
        })}
        {!b.loading && !b.data?.anggaran.length && <div className="empty">Belum ada anggaran. Atur di bawah atau ketik “atur anggaran makan 1,5 juta” di Sandbox.</div>}
      </div>
      <div className="row wrap" style={{ gap: 8, marginTop: 16, alignItems: "flex-end" }}>
        <label className="field"><span>Kategori</span>
          <select className="input" value={form.kategori} onChange={(e) => setForm({ ...form, kategori: e.target.value })}>
            {EXPENSE_CATS.map((c) => <option key={c} value={c}>{CAT_LABEL[c] ?? c}</option>)}
          </select></label>
        <label className="field"><span>Batas per bulan (Rp)</span><input className="input" inputMode="numeric" value={form.batas} placeholder="1500000" onChange={(e) => setForm({ ...form, batas: e.target.value })} /></label>
        <label className="field" style={{ width: 120 }}><span>Peringatan (%)</span><input className="input" inputMode="numeric" value={form.ambang} onChange={(e) => setForm({ ...form, ambang: e.target.value })} /></label>
        <button className="btn primary" onClick={save}><Plus size={14} /> Simpan anggaran</button>
      </div>
    </section>
  );
}

function Bills() {
  const { client, version, bump, toast } = useApp();
  const bl = useLoad(() => client.bills(), [client, version]);
  const cand = useLoad(() => client.detectSubscriptions().then((r) => ({ ...r, kandidat: r.kandidat.filter((c) => c.keyakinan >= 0.6) })), [client, version]);
  const [form, setForm] = useState({ nama: "", jumlah: "", jatuh_tempo: "", frekuensi: "bulanan", catat_otomatis: false });
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); bump(); toast(ok); } catch (e) { toast((e as Error).message, true); }
  };
  const add = () => {
    const jumlah = Number(form.jumlah.replace(/\D/g, ""));
    if (!form.nama || !jumlah || !form.jatuh_tempo) { toast("Isi nama, nominal, dan jatuh tempo", true); return; }
    void act(async () => { await client.addBill({ ...form, jumlah }); setForm({ nama: "", jumlah: "", jatuh_tempo: "", frekuensi: "bulanan", catat_otomatis: false }); }, "Tagihan didaftarkan");
  };
  const due = (b: Bill) => {
    const d = b.hari_lagi ?? 0;
    if (d < 0) return <Badge tone="bad">terlambat {-d} hari</Badge>;
    if (d === 0) return <Badge tone="warn">hari ini</Badge>;
    return <Badge tone={d <= 3 ? "warn" : "muted"}>{d} hari lagi</Badge>;
  };
  return (
    <section className="card pad-lg">
      <div className="card-title">
        <h3 className="row" style={{ gap: 8 }}><CalendarClock size={16} /> Tagihan rutin</h3>
        <span className="small faint">{bl.data ? `±${rp(bl.data.perkiraan_total_per_bulan)}/bulan` : ""}</span>
      </div>
      {bl.error && <div className="banner err">{bl.error}</div>}
      <div className="tbl-wrap"><table className="tbl card-table bill-table">
        <thead><tr><th>Tagihan</th><th>Frekuensi</th><th>Jatuh tempo</th><th className="r">Nominal</th><th /></tr></thead>
        <tbody>
          {(bl.data?.tagihan ?? []).map((b) => (
            <tr key={b.id}>
              <td><b className="small">{b.name}</b> <span className="tiny faint">{catLabel(b.category)}</span>{b.auto_record && <> <Badge tone="info">catat otomatis</Badge></>}</td>
              <td className="small">{b.frequency}</td>
              <td className="small">{dateLabel(b.next_due)} {due(b)}</td>
              <td className="r">{rp(b.amount)}</td>
              <td className="r" style={{ whiteSpace: "nowrap" }}>
                <button className="btn sm" onClick={() => act(() => client.payBill(b.id), `${b.name} dicatat dibayar`)}>Bayar</button>{" "}
                <button className="btn ghost sm" aria-label={`Hapus ${b.name}`} onClick={() => act(() => client.deleteBill(b.id), "Tagihan dinonaktifkan")}><Trash2 size={13} /></button>
              </td>
            </tr>
          ))}
          {!bl.loading && !bl.data?.tagihan.length && <tr><td colSpan={5}><div className="empty">Belum ada tagihan rutin.</div></td></tr>}
        </tbody>
      </table></div>
      <div className="row wrap" style={{ gap: 8, marginTop: 16, alignItems: "flex-end" }}>
        <label className="field"><span>Nama</span><input className="input" value={form.nama} placeholder="Listrik PLN" onChange={(e) => setForm({ ...form, nama: e.target.value })} /></label>
        <label className="field" style={{ width: 140 }}><span>Nominal (Rp)</span><input className="input" inputMode="numeric" value={form.jumlah} placeholder="200000" onChange={(e) => setForm({ ...form, jumlah: e.target.value })} /></label>
        <label className="field" style={{ width: 160 }}><span>Jatuh tempo</span><input className="input" value={form.jatuh_tempo} placeholder="20 atau 2026-12-20" onChange={(e) => setForm({ ...form, jatuh_tempo: e.target.value })} /></label>
        <label className="field"><span>Frekuensi</span><select className="input" value={form.frekuensi} onChange={(e) => setForm({ ...form, frekuensi: e.target.value })}>
          <option value="bulanan">bulanan</option><option value="mingguan">mingguan</option><option value="tahunan">tahunan</option></select></label>
        <label className="row small" style={{ gap: 6, paddingBottom: 8 }}><input type="checkbox" checked={form.catat_otomatis} onChange={(e) => setForm({ ...form, catat_otomatis: e.target.checked })} /> catat otomatis</label>
        <button className="btn primary" onClick={add}><Plus size={14} /> Daftarkan</button>
      </div>
      {(cand.data?.kandidat.length ?? 0) > 0 && (
        <div style={{ marginTop: 20 }}>
          <div className="label" style={{ marginBottom: 8 }}>Pengeluaran berulang yang belum didaftarkan (deteksi otomatis)</div>
          <div className="stack" style={{ gap: 8 }}>
            {cand.data!.kandidat.map((c) => (
              <div key={c.kunci} className="row wrap notif" style={{ justifyContent: "space-between", gap: 8 }}>
                <span className="small"><b>{c.nama}</b> · ±{rp(c.nominal_tipikal)} {c.frekuensi} · {c.kemunculan}x ({c.tanggal.join(", ")}) · keyakinan {Math.round(c.keyakinan * 100)}%</span>
                <button className="btn sm" onClick={() => act(() => client.addBill({ nama: c.nama, jumlah: c.nominal_tipikal, jatuh_tempo: c.perkiraan_berikutnya, frekuensi: c.frekuensi, kategori: c.kategori }), `${c.nama} didaftarkan`)}>
                  <Plus size={13} /> Daftarkan</button>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function Reports() {
  const { client, version, toast, go } = useApp();
  const r = useLoad(() => client.reports(), [client, version]);
  const [busy, setBusy] = useState(false);
  const request = async (periode: string) => {
    setBusy(true);
    try {
      const res = await client.requestReport(periode);
      toast("Agen mulai menyusun laporan (fakta → narasi → PDF)");
      go({ page: "sandbox", run: res.job.run_id });
    } catch (e) { toast((e as Error).message, true); } finally { setBusy(false); }
  };
  return (
    <section className="card pad-lg">
      <div className="card-title" style={{ flexWrap: "wrap" }}>
        <h3 className="row" style={{ gap: 8 }}><FileText size={16} /> Laporan PDF</h3>
        <div className="row wrap" style={{ gap: 6 }}>
          {[["minggu_lalu", "Minggu lalu"], ["bulan_lalu", "Bulan lalu"], ["bulan_ini", "Bulan ini"]].map(([k, l]) => (
            <button key={k} className="btn sm" disabled={busy} onClick={() => request(k)}>Buat: {l}</button>
          ))}
        </div>
      </div>
      {r.error && <div className="banner err">{r.error}</div>}
      <div className="tbl-wrap"><table className="tbl card-table report-table">
        <thead><tr><th>Periode</th><th>Jenis</th><th>Narasi</th><th className="r">Pengeluaran</th><th className="r">Saldo</th><th /></tr></thead>
        <tbody>
          {(r.data?.reports ?? []).map((x) => (
            <tr key={x.id}>
              <td><b className="small">{x.period_label}</b><div className="tiny faint">{x.date_from} s/d {x.date_to} · dibuat {x.created_at.replace("T", " ").slice(0, 16)}</div></td>
              <td className="small">{x.kind}</td>
              <td><Badge tone={x.narrative_source === "agent" ? "ok" : "muted"}>{x.narrative_source === "agent" ? "model lokal" : "templat"}</Badge></td>
              <td className="r" data-label="Keluar">{rp(x.total.pengeluaran)}</td>
              <td className="r" data-label="Saldo">{rp(x.total.saldo)}</td>
              <td className="r" style={{ whiteSpace: "nowrap" }}>
                <a className="btn sm" href={client.fileUrl(x.url)} target="_blank" rel="noreferrer"><FileText size={13} /> PDF</a>
                {x.run_id && <> <button className="btn ghost sm" onClick={() => go({ page: "sandbox", run: x.run_id! })}>trace</button></>}
              </td>
            </tr>
          ))}
          {!r.loading && !r.data?.reports.length && <tr><td colSpan={6}><div className="empty">Belum ada laporan. Laporan mingguan dibuat tiap Senin, bulanan tiap tanggal 1.</div></td></tr>}
        </tbody>
      </table></div>
    </section>
  );
}
