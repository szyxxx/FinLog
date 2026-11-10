import { useMemo, useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import type { Tx } from "../api/types";
import { useApp, useLoad } from "../lib/store";
import { CAT_LABEL, PERIODS, catLabel, dateLabel, periodRange, rp, rpShort } from "../lib/format";
import { Badge, InputIcon } from "../components/ui";

export function TransactionsPage() {
  const { client, version, bump, toast, go } = useApp();
  const [periode, setPeriode] = useState("bulan_ini");
  const [jenis, setJenis] = useState("");
  const [kategori, setKategori] = useState("");
  const [kw, setKw] = useState("");
  const [edit, setEdit] = useState<Tx | "new" | null>(null);
  const [del, setDel] = useState<Tx | null>(null);

  const summary = useLoad(() => client.summary({ periode }), [client, periode, version]);
  const list = useLoad(() => client.transactions({ periode, jenis, kategori, kata_kunci: kw, limit: 300 }), [client, periode, jenis, kategori, kw, version]);
  const all = useLoad(() => client.transactions({ periode, limit: 1000 }), [client, periode, version]);

  const s = summary.data;
  const out = (s?.per_kategori ?? []).filter((k) => k.jenis === "pengeluaran").sort((a, b) => b.total - a.total);
  const inc = (s?.per_kategori ?? []).filter((k) => k.jenis === "pemasukan").sort((a, b) => b.total - a.total);
  const maxOut = Math.max(1, ...out.map((k) => k.total));
  const maxInc = Math.max(1, ...inc.map((k) => k.total));

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="page-head">
        <div><h1>Transaksi</h1><p>CRUD langsung ke <span className="mono">/v1/transactions</span> (tanpa agent) dan ringkasan dari <span className="mono">/v1/summary</span>.</p></div>
        <div className="seg" style={{ minWidth: 560 }}>
          {PERIODS.map(([k, l]) => <button key={k} className={periode === k ? "on" : ""} onClick={() => setPeriode(k)}>{l}</button>)}
        </div>
      </div>

      <section className="card pad-lg" style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 1fr 1fr", gap: 24, alignItems: "end" }}>
        <div>
          <div className="label">Saldo periode</div>
          <div className="hero-num" style={{ marginTop: 10, color: (s?.saldo ?? 0) < 0 ? "var(--bad)" : "var(--text-base)" }}>{s ? rp(s.saldo) : "–"}</div>
          <div className="small faint" style={{ marginTop: 8 }}>{s?.periode}</div>
        </div>
        <div className="stat"><span className="label">Pemasukan</span><b style={{ color: "var(--ok)" }}>{rp(s?.total_pemasukan)}</b></div>
        <div className="stat"><span className="label">Pengeluaran</span><b>{rp(s?.total_pengeluaran)}</b></div>
        <div className="stat"><span className="label">Jumlah transaksi</span><b>{all.data?.jumlah_hasil ?? "–"}</b></div>
      </section>

      <div className="two-col">
        <section className="card pad-lg">
          <div className="card-title"><h3>Pengeluaran per kategori</h3><span className="small faint">{out.length} kategori</span></div>
          <div className="bars">
            {out.map((k) => (
              <div key={k.kategori} className="bar-row" title={`${catLabel(k.kategori)}: ${rp(k.total)} (${k.jumlah_transaksi} transaksi)`}>
                <span style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{catLabel(k.kategori)}</span>
                <span className="bar-track"><i style={{ width: `${(k.total / maxOut) * 100}%`, background: "var(--accent)" }} /></span>
                <span className="r small" style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{rp(k.total)}</span>
              </div>
            ))}
            {!out.length && <div className="empty">Tidak ada pengeluaran di periode ini.</div>}
          </div>
          {inc.length > 0 && <>
            <div className="card-title" style={{ marginTop: 22 }}><h3>Pemasukan per kategori</h3></div>
            <div className="bars">
              {inc.map((k) => (
                <div key={k.kategori} className="bar-row" title={`${catLabel(k.kategori)}: ${rp(k.total)}`}>
                  <span>{catLabel(k.kategori)}</span>
                  <span className="bar-track"><i style={{ width: `${(k.total / maxInc) * 100}%`, background: "var(--ok)" }} /></span>
                  <span className="small" style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{rp(k.total)}</span>
                </div>
              ))}
            </div>
          </>}
        </section>
        <section className="card pad-lg">
          <div className="card-title"><h3>Pengeluaran harian</h3><span className="small faint">{periode === "semua" || periode === "tahun_ini" ? "30 hari terakhir" : "periode terpilih"}</span></div>
          <DailyChart txs={all.data?.transaksi ?? []} periode={periode} />
        </section>
      </div>

      <section className="card">
        <div className="row wrap" style={{ padding: "6px 6px 14px", gap: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15, marginRight: 8 }}>Daftar</h3>
          <input className="input" style={{ width: 240 }} placeholder="Cari deskripsi / merchant" value={kw} onChange={(e) => setKw(e.target.value)} />
          <select className="input" style={{ width: 160 }} value={jenis} onChange={(e) => setJenis(e.target.value)}>
            <option value="">Semua jenis</option><option value="pengeluaran">Pengeluaran</option><option value="pemasukan">Pemasukan</option>
          </select>
          <select className="input" style={{ width: 200 }} value={kategori} onChange={(e) => setKategori(e.target.value)}>
            <option value="">Semua kategori</option>
            {Object.entries(CAT_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <span className="grow" />
          <span className="small muted">{list.data ? `${list.data.jumlah_hasil} hasil · total ${rp(list.data.total_nominal)}` : ""}</span>
          <button className="btn primary" onClick={() => setEdit("new")}><Plus size={15} /> Tambah manual</button>
        </div>
        {list.error && <div className="banner err">{list.error}</div>}
        <table className="tbl">
          <thead><tr><th>#</th><th>Tanggal</th><th>Deskripsi</th><th>Kategori</th><th>Sumber</th><th className="r">Jumlah</th><th /></tr></thead>
          <tbody>
            {(list.data?.transaksi ?? []).map((t) => (
              <tr key={t.id}>
                <td className="faint mono">{t.id}</td>
                <td style={{ whiteSpace: "nowrap" }}>{dateLabel(t.date)}</td>
                <td><div style={{ fontWeight: 550 }}>{t.description || "–"}</div>{(t.merchant || t.items?.length) && <div className="tiny faint">{t.merchant}{t.items?.length ? ` · ${t.items.length} item` : ""}</div>}</td>
                <td><span className="row" style={{ gap: 0 }}><span className="sw" style={{ background: t.kind === "pemasukan" ? "var(--ok)" : "var(--accent)" }} />{catLabel(t.category)}</span></td>
                <td>{t.run_id ? <button className="btn ghost sm" title={`Buka trace ${t.run_id}`} onClick={() => go({ page: "sandbox", run: t.run_id! })}><InputIcon type={t.source} size={13} />{t.source}</button> : <Badge>{t.source}</Badge>}</td>
                <td className={`r ${t.kind === "pemasukan" ? "amt-in" : "amt-out"}`}>{t.kind === "pemasukan" ? "+" : "−"}{rp(t.amount)}</td>
                <td className="r" style={{ whiteSpace: "nowrap" }}>
                  <button className="btn ghost sm" aria-label="Ubah" onClick={() => setEdit(t)}><Pencil size={14} /></button>
                  <button className="btn ghost sm danger" aria-label="Hapus" onClick={() => setDel(t)}><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {list.data && !list.data.transaksi.length && <div className="empty">Tidak ada transaksi yang cocok.</div>}
      </section>

      {edit && <TxDialog tx={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={(msg) => { setEdit(null); bump(); toast(msg); }} />}
      {del && (
        <div className="modal-back" onClick={() => setDel(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Hapus transaksi #{del.id}?</h2>
            <p className="muted">{del.description} · {rp(del.amount)} · {dateLabel(del.date)}. Server melakukan soft delete.</p>
            <div className="row" style={{ justifyContent: "flex-end", marginTop: 16 }}>
              <button className="btn ghost" onClick={() => setDel(null)}>Batal</button>
              <button className="btn" style={{ color: "var(--bad)" }} onClick={async () => {
                try { await client.deleteTx(del.id); toast(`Transaksi #${del.id} dihapus`); bump(); } catch (e) { toast((e as Error).message, true); }
                setDel(null);
              }}><Trash2 size={14} /> Hapus</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DailyChart({ txs, periode }: { txs: Tx[]; periode: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const days = useMemo(() => {
    let [from, to] = periodRange(periode);
    const today = new Date();
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    if (periode === "semua" || periode === "tahun_ini") { const d = new Date(today); d.setDate(d.getDate() - 29); from = iso(d); to = iso(today); }
    const out: { date: string; total: number }[] = [];
    const cur = new Date(`${from}T00:00:00`);
    const end = new Date(`${to}T00:00:00`);
    while (cur <= end && out.length < 62) {
      const ds = iso(cur);
      out.push({ date: ds, total: txs.filter((t) => t.date === ds && t.kind === "pengeluaran").reduce((a, t) => a + t.amount, 0) });
      cur.setDate(cur.getDate() + 1);
    }
    return out;
  }, [txs, periode]);
  const max = Math.max(1, ...days.map((d) => d.total));
  const W = 600, H = 220, pad = 44;
  const bw = (W - pad) / Math.max(1, days.length);
  const ticks = [0, max / 2, max];
  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H + 22}`} width="100%" role="img" aria-label="Pengeluaran harian">
        {ticks.map((v, i) => {
          const y = H - (v / max) * (H - 10);
          return <g key={i}><line x1={pad} x2={W} y1={y} y2={y} stroke="rgba(255,255,255,.06)" /><text x={pad - 6} y={y + 3} textAnchor="end" fontSize={10} fill="#6c7380">{rpShort(v)}</text></g>;
        })}
        {days.map((d, i) => {
          const h = (d.total / max) * (H - 10);
          const w = Math.max(2, Math.min(18, bw - 2));
          const x = pad + i * bw + (bw - w) / 2;
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={pad + i * bw} y={0} width={bw} height={H} fill="transparent" />
              {d.total > 0 && <path d={`M${x},${H} v${-Math.max(h - 4, 0)} q0,-4 4,-4 h${Math.max(w - 8, 0)} q4,0 4,4 v${Math.max(h - 4, 0)} z`} fill={hover === i ? "var(--accent-hover)" : "var(--accent)"} opacity={hover === null || hover === i ? 1 : 0.55} />}
              {(i === 0 || i === days.length - 1 || (days.length > 10 && i % 7 === 0)) && <text x={x + w / 2} y={H + 16} textAnchor="middle" fontSize={10} fill="#6c7380">{d.date.slice(8)}/{d.date.slice(5, 7)}</text>}
            </g>
          );
        })}
        <line x1={pad} x2={W} y1={H} y2={H} stroke="rgba(255,255,255,.12)" />
      </svg>
      {hover !== null && (
        <div className="toast" style={{ position: "absolute", top: 0, right: 0, padding: "6px 10px" }}>
          <div className="tiny faint">{dateLabel(days[hover].date)}</div><b>{rp(days[hover].total)}</b>
        </div>
      )}
    </div>
  );
}

function TxDialog({ tx, onClose, onSaved }: { tx: Tx | null; onClose: () => void; onSaved: (msg: string) => void }) {
  const { client, toast } = useApp();
  const [f, setF] = useState({
    jenis: tx?.kind ?? "pengeluaran", jumlah: String(tx?.amount ?? ""), kategori: tx?.category ?? "makanan_minuman",
    deskripsi: tx?.description ?? "", merchant: tx?.merchant ?? "", tanggal: tx?.date ?? new Date().toISOString().slice(0, 10),
  });
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: Record<string, unknown> = { ...f, jumlah: Number(f.jumlah) };
      if (tx) {
        // PATCH hanya field yang berubah; jenis dan kategori selalu dikirim berpasangan (server memvalidasi kecocokannya).
        const orig: Record<string, unknown> = { jenis: tx.kind, jumlah: tx.amount, kategori: tx.category, deskripsi: tx.description ?? "", merchant: tx.merchant ?? "", tanggal: tx.date };
        const diff: Record<string, unknown> = Object.fromEntries(Object.entries(body).filter(([k, v]) => v !== orig[k]));
        if ("jenis" in diff || "kategori" in diff) { diff.jenis = f.jenis; diff.kategori = f.kategori; }
        if (!Object.keys(diff).length) { onClose(); return; }
        await client.updateTx(tx.id, diff);
        onSaved(`Transaksi #${tx.id} diperbarui (${Object.keys(diff).join(", ")})`);
      } else {
        const t = await client.createTx(body);
        onSaved(`Transaksi #${t.id} ditambahkan`);
      }
    } catch (e) {
      // Tampilkan pesan server di dalam form (mis. 400 kategori tidak cocok), bukan gagal diam.
      setErr((e as Error).message);
      toast((e as Error).message, true);
    } finally { setBusy(false); }
  };
  const INCOME = ["gaji", "transfer_masuk", "lainnya_masuk"];
  const cats = Object.entries(CAT_LABEL).filter(([k]) => (f.jenis === "pemasukan" ? INCOME.includes(k) : !INCOME.includes(k)));
  // Data lama bisa punya kategori yang tidak cocok dengan jenisnya: paksa pilih ulang.
  const catOk = cats.some(([k]) => k === f.kategori);
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{tx ? `Ubah transaksi #${tx.id}` : "Tambah transaksi manual"}</h2>
        <p className="small muted" style={{ margin: "0 0 16px" }}>{tx ? "PATCH /v1/transactions/{id} — hanya field yang berubah dikirim." : "POST /v1/transactions (source = manual)."}</p>
        <div className="stack" style={{ gap: 12 }}>
          <div className="seg">
            {(["pengeluaran", "pemasukan"] as const).map((j) => <button key={j} className={f.jenis === j ? "on" : ""} onClick={() => setF({ ...f, jenis: j, kategori: j === "pemasukan" ? "gaji" : "makanan_minuman" })}>{j}</button>)}
          </div>
          <div className="two-col" style={{ gap: 12 }}>
            <label className="field"><span>Jumlah (Rp)</span><input className="input" inputMode="numeric" value={f.jumlah} onChange={set("jumlah")} /></label>
            <label className="field"><span>Tanggal</span><input className="input" type="date" value={f.tanggal} onChange={set("tanggal")} /></label>
          </div>
          <label className="field"><span>Kategori ({f.jenis})</span><select className="input" value={catOk ? f.kategori : ""} onChange={set("kategori")}>
            {!catOk && <option value="" disabled>Pilih kategori {f.jenis}…</option>}
            {cats.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></label>
          <label className="field"><span>Deskripsi</span><input className="input" value={f.deskripsi} onChange={set("deskripsi")} /></label>
          <label className="field"><span>Merchant</span><input className="input" value={f.merchant} onChange={set("merchant")} /></label>
        </div>
        {err && <div className="banner err" role="alert" style={{ marginTop: 14 }}>Server menolak: {err}</div>}
        <div className="row" style={{ justifyContent: "flex-end", marginTop: 20 }}>
          <button className="btn ghost" onClick={onClose}>Batal</button>
          <button className="btn primary" disabled={busy || !(Number(f.jumlah) > 0) || !catOk} onClick={save}>{busy && <span className="spin" />} Simpan</button>
        </div>
      </div>
    </div>
  );
}
