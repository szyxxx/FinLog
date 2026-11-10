import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ApiClient } from "../api/types";
import { HttpClient } from "../api/http";
import { MockClient } from "../mock/client";

export interface Settings {
  /** Kosong = origin yang sama (web app meneruskan /v1 dan /health ke server lewat proxy Vite). */
  baseUrl: string;
  apiKey: string;
  mock: boolean;
  pollMs: number;
  /** true bila key datang dari link undangan (?key=...). */
  guest?: boolean;
}

const KEY = "kas-sandbox.settings";
const DEFAULTS: Settings = { baseUrl: "", apiKey: "", mock: false, pollMs: 5000 };

function save(s: Settings) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* storage diblokir */ }
}

function load(): Settings {
  let s: Settings = { ...DEFAULTS };
  try { s = { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) ?? "{}") }; } catch { /* storage rusak/diblokir */ }
  const params = new URLSearchParams(location.search);
  // Link undangan: ?key=... dibaca sekali, disimpan, lalu dihapus dari address bar.
  const k = params.get("key");
  if (k) {
    s = { ...s, apiKey: k, baseUrl: "", mock: false, guest: true };
    save(s);
    params.delete("key");
    const rest = params.toString();
    history.replaceState(null, "", `${location.pathname}${rest ? `?${rest}` : ""}${location.hash}`);
  }
  // ?mock=0 / ?mock=1 di URL menimpa (praktis untuk demo & screenshot).
  const m = params.get("mock");
  if (m !== null) s.mock = m !== "0";
  return s;
}

/** Label server untuk UI. */
export const serverLabel = (s: Settings) => (s.baseUrl ? s.baseUrl.replace(/^https?:\/\//, "") : `${location.host} (proxy)`);

export type Route = { page: "sandbox" | "transaksi" | "riwayat" | "sistem"; run?: string; step?: number };

function parseHash(): Route {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query = ""] = h.split("?");
  const q = new URLSearchParams(query);
  const page = (["sandbox", "transaksi", "riwayat", "sistem"].includes(path) ? path : "sandbox") as Route["page"];
  return { page, run: q.get("run") ?? undefined, step: q.get("step") ? Number(q.get("step")) : undefined };
}

export function toHash(r: Route) {
  const q = new URLSearchParams();
  if (r.run) q.set("run", r.run);
  if (r.step) q.set("step", String(r.step));
  const s = q.toString();
  return `#/${r.page}${s ? `?${s}` : ""}`;
}

interface Toast { id: number; text: string; bad?: boolean }

interface Ctx {
  settings: Settings;
  setSettings: (s: Settings) => void;
  client: ApiClient;
  route: Route;
  go: (r: Route) => void;
  toast: (text: string, bad?: boolean) => void;
  toasts: Toast[];
  /** Naik setiap ada perubahan data (run selesai, transaksi diubah) agar panel lain memuat ulang. */
  version: number;
  bump: () => void;
}

const C = createContext<Ctx | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [settings, setS] = useState<Settings>(load);
  const [route, setRoute] = useState<Route>(parseHash);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [version, setVersion] = useState(0);

  const client = useMemo<ApiClient>(
    () => (settings.mock ? new MockClient() : new HttpClient(settings.baseUrl, settings.apiKey)),
    [settings.mock, settings.baseUrl, settings.apiKey],
  );

  useEffect(() => {
    const f = () => setRoute(parseHash());
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);

  const setSettings = useCallback((s: Settings) => {
    setS(s);
    save(s);
  }, []);
  const go = useCallback((r: Route) => { location.hash = toHash(r); }, []);
  const toast = useCallback((text: string, bad?: boolean) => {
    const id = Math.random();
    setToasts((t) => [...t, { id, text, bad }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), bad ? 7000 : 3500);
  }, []);
  const bump = useCallback(() => setVersion((v) => v + 1), []);

  // Stream global: perbarui panel lain saat ada run selesai / status job berubah.
  useEffect(() => {
    return client.streamAll((e) => {
      if (e.event === "run_finished" || e.event === "job_status") bump();
    });
  }, [client, bump]);

  return <C.Provider value={{ settings, setSettings, client, route, go, toast, toasts, version, bump }}>{children}</C.Provider>;
}

export function useApp() {
  const c = useContext(C);
  if (!c) throw new Error("AppProvider hilang");
  return c;
}

/** Muat data async dengan status loading/error; dimuat ulang saat deps berubah. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError((e as Error).message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading, setData };
}
