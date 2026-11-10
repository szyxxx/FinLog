import { useEffect, useState } from "react";
import type { Step } from "../api/types";
import { rp } from "../lib/format";
import { Badge, Json, Tabs } from "./ui";

const confColor = (c: number) => (c >= 0.95 ? "#5fd39a" : c >= 0.8 ? "#f5b454" : "#f2727f");

export function ReceiptViewer({ image, ocr, extract, validation, mockNote }: {
  image: string | null; ocr?: Step; extract?: Step; validation?: Step; mockNote?: boolean;
}) {
  const [mode, setMode] = useState<"lines" | "boxes" | "off">("lines");
  const [hl, setHl] = useState<number | null>(null);
  const [tab, setTab] = useState<"data" | "ocr" | "json">("data");
  const [imgErr, setImgErr] = useState<string | null>(null);
  useEffect(() => setImgErr(null), [image]);
  const o = ocr?.output;
  const size: [number, number] | undefined = o?.image_size ?? ocr?.input?.image_size;
  const lines: any[] = o?.lines ?? [];
  const boxes: any[] = o?.boxes ?? [];
  const data = extract?.output?.data;

  return (
    <div className="receipt-grid">
      <div>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <span className="label">Gambar + bbox OCR</span>
          {ocr && (
            <div className="seg" style={{ width: 210 }}>
              {([["lines", "Baris"], ["boxes", "Kotak"], ["off", "Mati"]] as const).map(([k, l]) => (
                <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{l}</button>
              ))}
            </div>
          )}
        </div>
        {image && !imgErr ? (
          <div className="receipt-stage">
            <img src={image} alt="Struk yang diunggah" onError={() => setImgErr("Gambar tidak bisa dimuat dari server (404 / key salah).")} />
            {ocr && size && mode !== "off" && (
              <svg viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="none" aria-hidden>
                {mode === "lines" && lines.map((l, i) => {
                  const [x0, y0, x1, y1] = l.bbox;
                  const c = confColor(l.conf);
                  return (
                    <g key={i} onMouseEnter={() => setHl(i)} onMouseLeave={() => setHl(null)}>
                      <rect x={x0 - 2} y={y0 - 2} width={x1 - x0 + 4} height={y1 - y0 + 4} rx={3}
                        fill={hl === i ? "rgba(139,156,255,.28)" : `${c}22`} stroke={hl === i ? "#6f7fe6" : c} strokeWidth={hl === i ? 2.5 : 1.5} />
                      <text x={x1 + 4} y={y0 + 10} fontSize={10} fill={c} style={{ fontFamily: "var(--font-mono)", paintOrder: "stroke", stroke: "#fff", strokeWidth: 3 }}>{i + 1}</text>
                    </g>
                  );
                })}
                {mode === "boxes" && boxes.map((b, i) => (
                  <polygon key={i} points={(b.quad ?? []).map((p: number[]) => p.join(",")).join(" ")}
                    fill={`${confColor(b.conf)}26`} stroke={confColor(b.conf)} strokeWidth={1.5}
                    opacity={hl === null || lines[hl]?.box_index?.includes(b.index) ? 1 : 0.25} />
                ))}
              </svg>
            )}
          </div>
        ) : (
          <div className="empty inset">Gambar struk tidak tersedia.<br /><span className="small">{imgErr ?? "Run ini tidak punya gambar."}</span></div>
        )}
        {ocr && (
          <div className="row wrap small muted" style={{ marginTop: 10, gap: 14 }}>
            <span>{lines.length} baris · {o?.n_boxes ?? boxes.length} kotak</span>
            <span>conf rata-rata <b style={{ color: confColor(o?.mean_conf ?? 0) }}>{(o?.mean_conf ?? 0).toFixed(3)}</b> · min {(o?.min_conf ?? 0).toFixed(3)}</span>
            {o?.skew_deg !== undefined && <span>miring {Number(o.skew_deg).toFixed(1)}°</span>}
            {o?.timings_ms?.total !== undefined && <span>{Math.round(o.timings_ms.total)} ms (CPU)</span>}
          </div>
        )}
        {mockNote && <div className="tiny faint" style={{ marginTop: 6 }}>Mode mock: gambar disalin dari server/data/uploads (run nyata) atau samples/.</div>}
      </div>

      <div style={{ minWidth: 0 }}>
        <Tabs value={tab} onChange={setTab} items={[["data", "Hasil ekstraksi"], ["ocr", `Teks OCR (${lines.length})`], ["json", "JSON"]]} />
        {tab === "data" && (data ? (
          <div className="stack" style={{ gap: 12 }}>
            <div className="row wrap" style={{ gap: 8 }}>
              <Badge tone="info">{data.sumber_ekstraksi ?? extract?.output?.mode}</Badge>
              {extract?.output?.jalur && <Badge>{extract.output.jalur.join(" → ")}</Badge>}
              {extract?.output?.validasi_lolos !== undefined && (
                <Badge tone={extract.output.validasi_lolos ? "ok" : "warn"}>{extract.output.validasi_lolos ? "validasi lolos" : "validasi gagal"}</Badge>
              )}
              {data.keyakinan !== undefined && <Badge tone={data.keyakinan >= 0.6 ? "ok" : "warn"}>keyakinan {data.keyakinan}</Badge>}
            </div>
            <dl className="kv">
              <dt>Toko</dt><dd><b>{data.toko ?? "–"}</b></dd>
              <dt>Tanggal</dt><dd>{data.tanggal ?? "–"}</dd>
              <dt>Kategori</dt><dd>{data.kategori ?? "–"}</dd>
              {data.metode_bayar && <><dt>Bayar</dt><dd>{data.metode_bayar}</dd></>}
            </dl>
            <table className="tbl">
              <thead><tr><th>Item</th><th className="r">Qty</th><th className="r">Harga</th><th className="r">Subtotal</th></tr></thead>
              <tbody>
                {(data.items ?? []).map((it: any, i: number) => (
                  <tr key={i}><td>{it.nama}</td><td className="r">{it.qty ?? 1}</td><td className="r">{it.harga_satuan ? rp(it.harga_satuan) : "–"}</td><td className="r">{rp(it.subtotal)}</td></tr>
                ))}
                {!!data.diskon && <tr><td className="muted">Diskon</td><td /><td /><td className="r">−{rp(data.diskon)}</td></tr>}
                {!!data.pajak_layanan && <tr><td className="muted">Pajak/layanan</td><td /><td /><td className="r">{rp(data.pajak_layanan)}</td></tr>}
                <tr><td><b>Total</b></td><td /><td /><td className="r"><b>{rp(data.total)}</b></td></tr>
              </tbody>
            </table>
            {validation && <ValidationChecks step={validation} />}
          </div>
        ) : <div className="empty">Belum ada hasil ekstraksi.</div>)}
        {tab === "ocr" && (
          <div className="ocr-lines">
            {lines.map((l, i) => (
              <div key={i} className={`ocr-line ${hl === i ? "hl" : ""}`} onMouseEnter={() => setHl(i)} onMouseLeave={() => setHl(null)}>
                <span className="mono" title={l.text}><span className="faint">{String(i + 1).padStart(2, " ")}.</span> {l.text}</span>
                <div className="meter"><i style={{ width: `${l.conf * 100}%`, background: confColor(l.conf) }} /></div>
                <span className="tiny mono" style={{ color: confColor(l.conf) }}>{l.conf.toFixed(2)}</span>
              </div>
            ))}
            {!lines.length && <div className="empty">Tidak ada step OCR (mode vision).</div>}
          </div>
        )}
        {tab === "json" && <Json value={data ?? extract?.output ?? null} maxHeight={420} />}
      </div>
    </div>
  );
}

export function ValidationChecks({ step }: { step: Step }) {
  const checks = step.output?.checks ?? {};
  return (
    <div className="checks">
      {Object.entries(checks).map(([name, c]: [string, any]) => {
        const { ok, ...rest } = c ?? {};
        return (
          <div key={name} className="check">
            <span style={{ color: ok ? "var(--ok)" : "var(--bad)", fontWeight: 700 }}>{ok ? "✓" : "✗"}</span>
            <div>
              <div style={{ fontWeight: 600 }}>{name.replace(/_/g, " ")}</div>
              <small>{Object.entries(rest).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join("  ")}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}
