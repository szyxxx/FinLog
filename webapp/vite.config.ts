import { readFileSync } from "node:fs";
import type { IncomingMessage } from "node:http";
import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

// Web app meneruskan /v1/* dan /health ke AI server, sehingga cukup SATU link (mis. tunnel https)
// dan tamu tidak perlu mengisi URL server maupun API key. Target: env API_TARGET (default port 8000).
const target = process.env.API_TARGET ?? "http://localhost:8000";

/**
 * API key disisipkan oleh proxy (Node, sisi server), TIDAK pernah dikirim ke browser.
 * Sumber: env API_KEY, atau key PERTAMA di API_KEYS pada ../server/.env (dibaca sekali saat start).
 * File ini tidak ikut dibundel ke dist/.
 */
function serverKey(): string {
  if (process.env.API_KEY) return process.env.API_KEY.trim();
  try {
    const env = readFileSync(new URL("../server/.env", import.meta.url), "utf8");
    const line = env.split(/\r?\n/).find((l) => /^\s*API_KEYS\s*=/.test(l));
    return line?.split("=").slice(1).join("=").split(",")[0].trim().replace(/^["']|["']$/g, "") ?? "";
  } catch {
    return "";
  }
}
const key = serverKey();
console.log(key
  ? `[proxy] /v1 & /health → ${target}, X-API-Key disisipkan dari ${process.env.API_KEY ? "env API_KEY" : "server/.env"}`
  : `[proxy] /v1 & /health → ${target}, TANPA API key (server/.env tidak terbaca); isi key di Pengaturan web app`);

/** Sisipkan X-API-Key bila browser tidak mengirim sendiri (key di Pengaturan tetap dihormati). */
function withKey(extra?: ProxyOptions["configure"]): ProxyOptions["configure"] {
  return (proxy, options) => {
    proxy.on("proxyReq", (proxyReq, req: IncomingMessage) => {
      if (key && !req.headers["x-api-key"]) proxyReq.setHeader("X-API-Key", key);
    });
    extra?.(proxy, options);
  };
}

const api: ProxyOptions = {
  target,
  changeOrigin: true,
  configure: withKey((proxy) => {
    // SSE (/v1/runs/{id}/stream, /v1/events): jangan di-buffer / dikompres oleh proxy atau tunnel.
    proxy.on("proxyRes", (res) => {
      if (String(res.headers["content-type"] ?? "").includes("text/event-stream")) {
        res.headers["cache-control"] = "no-cache, no-transform";
        res.headers["x-accel-buffering"] = "no";
      }
    });
  }),
};
const proxy = { "/v1": api, "/health": { target, changeOrigin: true, configure: withKey() } };
// Quick tunnel Cloudflare memakai host acak *.trycloudflare.com.
const allowedHosts = [".trycloudflare.com"];

// base "./" supaya hasil build (dist/) bisa dibuka dari folder mana pun.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5180, host: true, proxy, allowedHosts },
  preview: { port: 5180, host: true, proxy, allowedHosts },
});
