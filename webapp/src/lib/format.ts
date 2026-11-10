export const rp = (n: number | null | undefined) =>
  n === null || n === undefined ? "–" : `Rp${Math.round(n).toLocaleString("id-ID")}`;

export const rpShort = (n: number) => {
  const a = Math.abs(n);
  if (a >= 1e6) return `${(n / 1e6).toLocaleString("id-ID", { maximumFractionDigits: 1 })} jt`;
  if (a >= 1e3) return `${(n / 1e3).toLocaleString("id-ID", { maximumFractionDigits: 0 })} rb`;
  return String(n);
};

export const num = (n: number | null | undefined, d = 0) =>
  n === null || n === undefined ? "–" : n.toLocaleString("id-ID", { maximumFractionDigits: d, minimumFractionDigits: 0 });

export function dur(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toLocaleString("id-ID", { maximumFractionDigits: 1 })} dtk`;
  return `${Math.floor(ms / 60000)} mnt ${Math.round((ms % 60000) / 1000)} dtk`;
}

export function clock(ts: number | null | undefined): string {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return "–";
  const s = Date.now() / 1000 - ts;
  if (s < 0) return clock(ts);
  if (s < 60) return "baru saja";
  if (s < 3600) return `${Math.floor(s / 60)} mnt lalu`;
  if (s < 86400) return `${Math.floor(s / 3600)} jam lalu`;
  return new Date(ts * 1000).toLocaleDateString("id-ID", { day: "numeric", month: "short" });
}

export const dateLabel = (d: string) =>
  new Date(`${d}T00:00:00`).toLocaleDateString("id-ID", { day: "numeric", month: "short", year: "numeric" });

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Rentang tanggal [mulai, akhir] untuk nilai `periode` (docs/api.md). */
export function periodRange(p: string): [string, string] {
  const t = new Date();
  const today = iso(t);
  switch (p) {
    case "hari_ini": return [today, today];
    case "kemarin": { const y = new Date(t); y.setDate(t.getDate() - 1); return [iso(y), iso(y)]; }
    case "minggu_ini": { const m = new Date(t); m.setDate(t.getDate() - ((t.getDay() + 6) % 7)); return [iso(m), today]; }
    case "bulan_ini": return [iso(new Date(t.getFullYear(), t.getMonth(), 1)), today];
    case "bulan_lalu": return [iso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), iso(new Date(t.getFullYear(), t.getMonth(), 0))];
    case "tahun_ini": return [`${t.getFullYear()}-01-01`, today];
    default: return ["0000-01-01", "9999-12-31"];
  }
}

export const PERIODS: [string, string][] = [
  ["hari_ini", "Hari ini"], ["kemarin", "Kemarin"], ["minggu_ini", "Minggu ini"], ["bulan_ini", "Bulan ini"],
  ["bulan_lalu", "Bulan lalu"], ["tahun_ini", "Tahun ini"], ["semua", "Semua"],
];

export const CAT_LABEL: Record<string, string> = {
  makanan_minuman: "Makanan & minuman", transportasi: "Transportasi", belanja: "Belanja", tagihan: "Tagihan",
  kesehatan: "Kesehatan", pendidikan: "Pendidikan", hiburan: "Hiburan", transfer_keluar: "Transfer keluar",
  lainnya_keluar: "Lainnya (keluar)", gaji: "Gaji", transfer_masuk: "Transfer masuk", lainnya_masuk: "Lainnya (masuk)",
};
export const catLabel = (c: string) => CAT_LABEL[c] ?? c;

/** Warna kategori: konten yang memberi warna, bukan chrome. */
export const CAT_COLOR: Record<string, string> = {
  makanan_minuman: "#f59e6b", transportasi: "#6fb4f2", belanja: "#c792ea", tagihan: "#f2c86f", kesehatan: "#6fd3b8",
  pendidikan: "#8fa6ff", hiburan: "#f08fb7", transfer_keluar: "#a3a9b8", lainnya_keluar: "#8b8f99",
  gaji: "#7ad694", transfer_masuk: "#62c8d6", lainnya_masuk: "#9bd67a",
};
export const catColor = (c: string) => CAT_COLOR[c] ?? "#8b8f99";

export const OUTCOME: Record<string, { label: string; tone: Tone }> = {
  recorded: { label: "Tercatat", tone: "ok" },
  modified: { label: "Diubah", tone: "ok" },
  answered: { label: "Dijawab", tone: "info" },
  not_transaction: { label: "Bukan transaksi", tone: "muted" },
  duplicate: { label: "Duplikat", tone: "muted" },
  possible_duplicate: { label: "Mungkin duplikat", tone: "warn" },
  needs_confirmation: { label: "Perlu konfirmasi", tone: "warn" },
  error: { label: "Gagal", tone: "bad" },
};

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Antre", tone: "muted" },
  running: { label: "Berjalan", tone: "live" },
  done: { label: "Selesai", tone: "ok" },
  awaiting_confirmation: { label: "Menunggu jawaban", tone: "warn" },
  done_with_warnings: { label: "Selesai + peringatan", tone: "warn" },
  error: { label: "Error", tone: "bad" },
};

export type Tone = "ok" | "warn" | "bad" | "info" | "muted" | "live";

// "email" tetap ada agar run lama bisa diberi label; filter hanya menawarkan input yang masih didukung.
export const INPUT_LABEL: Record<string, string> = { text: "Teks", receipt: "Struk", voice: "Suara", email: "Email" };
export const ACTIVE_INPUTS = ["text", "receipt", "voice"] as const;

export const json = (v: unknown) => JSON.stringify(v, null, 2);
