import { FileText, LineChart } from "lucide-react";
import type { Step } from "../api/types";
import { useApp } from "../lib/store";

/** Grafik buatan sub-agen analis (tool analisis_data) dan laporan PDF (tool buat_laporan / guard laporan_cadangan). */
export function runArtifacts(steps: Step[]) {
  const charts: { id: string; judul: string; url: string }[] = [];
  const reports: { id: string; url: string; label: string; sumber: string }[] = [];
  for (const s of steps) {
    const o = s.output ?? {};
    if (s.type === "tool" && s.name === "analisis_data" && o.ok) charts.push(...(o.grafik ?? []));
    const lap = (s.type === "tool" && s.name === "buat_laporan" && o.ok && o.laporan) || (s.type === "guard" && s.name === "laporan_cadangan" && o.laporan);
    if (lap) reports.push({ id: lap.id, url: lap.url, label: lap.periode?.label ?? lap.id, sumber: lap.narasi_sumber });
  }
  return { charts, reports };
}

export function ChartGallery({ charts }: { charts: { id: string; judul: string; url: string }[] }) {
  const { client } = useApp();
  if (!charts.length) return null;
  return (
    <div className="chart-gallery">
      {charts.map((c) => (
        <a key={c.id} href={client.fileUrl(c.url)} target="_blank" rel="noreferrer" className="chart-card" title="Buka ukuran penuh">
          <img src={client.fileUrl(c.url)} alt={c.judul} loading="lazy" />
          <span className="tiny muted row" style={{ gap: 6 }}><LineChart size={12} />{c.judul}</span>
        </a>
      ))}
    </div>
  );
}

export function ReportLinks({ reports }: { reports: { id: string; url: string; label: string; sumber: string }[] }) {
  const { client } = useApp();
  if (!reports.length) return null;
  return (
    <div className="row wrap" style={{ gap: 8 }}>
      {reports.map((r) => (
        <a key={r.id} className="btn sm" href={client.fileUrl(r.url)} target="_blank" rel="noreferrer">
          <FileText size={14} /> Laporan {r.label} (PDF{r.sumber === "template" ? ", narasi templat" : ""})
        </a>
      ))}
    </div>
  );
}
