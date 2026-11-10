// Uji end-to-end MODE LIVE (server sungguhan + LLM) lewat UI, sekaligus screenshot ke screenshots/live-*.
// Prasyarat: server hidup (server/run.sh), dev server hidup (npm run dev).
//   node scripts/e2e-live.mjs
// Env opsional: BASE (web app, default http://localhost:5180), SERVER (default http://localhost:8000),
//   KEY (default: nilai pertama API_KEYS di ../server/.env).
import { chromium } from "playwright";
import { readFileSync, existsSync, readdirSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SAMPLES = join(ROOT, "../server/samples");
const BASE = process.env.BASE ?? "http://localhost:5180";
const SERVER = process.env.SERVER ?? "http://localhost:8000";
const KEY = process.env.KEY ?? readFileSync(join(ROOT, "../server/.env"), "utf8").match(/^API_KEYS=([^,\n]+)/m)?.[1]?.trim();
const OUT = join(ROOT, "screenshots");
mkdirSync(OUT, { recursive: true });
const log = [];
const note = (step, ok, detail = "") => { log.push({ step, ok, detail }); console.log(`${ok ? "PASS" : "FAIL"}  ${step}  ${detail}`); };

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const root = join(homedir(), ".cache/ms-playwright");
  for (const d of existsSync(root) ? readdirSync(root).filter((x) => x.startsWith("chromium")).sort().reverse() : []) {
    for (const p of ["chrome-linux/headless_shell", "chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux/chrome"]) {
      if (existsSync(join(root, d, p))) return join(root, d, p);
    }
  }
}

const api = async (path, init = {}) => {
  const r = await fetch(SERVER + path, { ...init, headers: { "X-API-Key": KEY, "Content-Type": "application/json", ...(init.headers ?? {}) } });
  return { status: r.status, body: await r.json().catch(() => null) };
};

const browser = await chromium.launch({ executablePath: findChrome() });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
await ctx.addInitScript(([url, key]) => {
  localStorage.setItem("kas-sandbox.settings", JSON.stringify({ baseUrl: url, apiKey: key, mock: false, pollMs: 2000 }));
}, [SERVER, KEY]);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

const shot = async (name, full = false) => { await page.screenshot({ path: join(OUT, `live-${name}.png`), fullPage: full }); console.log("  📸 live-" + name); };
const runId = () => new URL(page.url()).hash.match(/run=([\w]+)/)?.[1];
const statusText = () => page.locator(".run-hero .badge").first().textContent().catch(() => "");

/** Tunggu run di layar selesai (status bukan Antre/Berjalan dan tidak ada baris pending). */
async function waitDone(ms = 240000) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    const st = (await statusText())?.trim();
    const pending = await page.locator(".tl-row.pending").count();
    if (st && !["Antre", "Berjalan", ""].includes(st) && !pending) return { status: st, secs: (Date.now() - t0) / 1000 };
    await page.waitForTimeout(1000);
  }
  return { status: "TIMEOUT", secs: ms / 1000 };
}

async function openSandbox() {
  await page.goto(`${BASE}/#/sandbox`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
}
async function tab(name) { await page.locator(".seg button", { hasText: name }).first().click(); }

// ------------------------------------------------------------------ 0. sistem awal
await page.goto(`${BASE}/#/sistem`);
await page.waitForTimeout(5000);
note("panel sistem memuat /health + /v1/metrics + /v1/system", (await page.locator(".banner.err").count()) === 0,
  (await page.locator(".gauge-num").first().textContent()) ?? "");

// ------------------------------------------------------------------ 1. teks + SSE live reasoning
await openSandbox();
await page.getByPlaceholder(/ngopi di warkop/).fill("beli bensin 30rb tadi pagi");
await page.getByRole("button", { name: "Kirim", exact: true }).click();
let sawCaret = false;
try { await page.waitForSelector(".live-text.caret", { timeout: 60000 }); sawCaret = true; } catch { /* tidak tampil */ }
await page.waitForTimeout(2500);
const thinkingLen = (await page.locator(".live-text").first().textContent().catch(() => ""))?.length ?? 0;
note("SSE: reasoning live (llm_delta) tampil saat run berjalan", sawCaret && thinkingLen > 20, `${thinkingLen} karakter terlihat`);
await shot("02-reasoning-live");
let r = await waitDone();
const textRun = runId();
note("teks: run selesai", r.status === "Selesai", `${r.status} dalam ${r.secs}s, ${textRun}`);
const textSum = (await api(`/v1/runs/${textRun}`)).body;
note("teks: outcome recorded + transaksi dibuat", textSum?.outcome === "recorded" && textSum?.transactions?.length > 0,
  `outcome=${textSum?.outcome}, tx=${textSum?.transactions?.map((t) => `#${t.id} ${t.amount}`).join(",")}`);
const llmRow = page.locator(".tl-row", { hasText: "LLM" }).first();
await llmRow.click();
await page.getByRole("tab", { name: /Prompt/ }).first().click();
await page.waitForTimeout(300);
await shot("03-teks-trace", true);

// ------------------------------------------------------------------ 2. struk baru (r5 miring/buram)
await openSandbox();
await tab("Struk");
await page.locator('input[type=file][accept^="image"]').setInputFiles(join(SAMPLES, "bench/r5_apotek_miring_buram.png"));
await page.waitForTimeout(300);
await page.getByRole("button", { name: /Kirim struk/ }).click();
await page.waitForTimeout(1500);
r = await waitDone(300000);
const rcptRun = runId();
const rcpt = (await api(`/v1/runs/${rcptRun}`)).body;
const ocrStep = rcpt?.steps?.find((s) => s.type === "ocr");
note("struk: run selesai", ["Selesai", "Menunggu jawaban"].includes(r.status), `${r.status} ${r.secs}s outcome=${rcpt?.outcome} ${rcptRun}`);
note("struk: step ocr + bbox digambar", !!ocrStep && (await page.locator(".receipt-stage svg rect").count()) > 3,
  `${ocrStep?.output?.lines?.length} baris, jalur=${rcpt?.steps?.find((s) => s.name === "ekstrak_struk")?.output?.jalur?.join("→")}`);
await page.locator(".receipt-stage").scrollIntoViewIfNeeded();
await shot("04-struk-baru", true);

// struk yang sama lagi → duplicate
await openSandbox();
await tab("Struk");
await page.locator('input[type=file][accept^="image"]').setInputFiles(join(SAMPLES, "bench/r5_apotek_miring_buram.png"));
await page.getByRole("button", { name: /Kirim struk/ }).click();
await page.waitForTimeout(1500);
r = await waitDone(60000);
const dupRun = (await api(`/v1/runs/${runId()}`)).body;
note("struk sama diunggah ulang → duplicate tanpa LLM", dupRun?.outcome === "duplicate" && dupRun?.llm_calls === 0, `${dupRun?.outcome}, ${dupRun?.duration_ms} ms`);

// struk lama dari database → gambar dari /v1/runs/{id}/image
const oldReceipt = (await api("/v1/runs?input_type=receipt&outcome=recorded&limit=20")).body?.runs?.find((x) => x.id !== rcptRun);
if (oldReceipt) {
  await page.goto(`${BASE}/#/sandbox?run=${oldReceipt.id}`);
  await page.waitForTimeout(2500);
  const nat = await page.locator(".receipt-stage img").evaluate((img) => img.naturalWidth).catch(() => 0);
  note("struk lama: gambar dari /v1/runs/{id}/image + overlay", nat > 0 && (await page.locator(".receipt-stage svg rect").count()) > 3, `${oldReceipt.id}, naturalWidth=${nat}`);
  await shot("05-struk-lama-image", true);
}

// ------------------------------------------------------------------ 3. suara (file contoh)
await openSandbox();
await tab("Suara");
await page.locator('input[type=file][accept^="audio"]').setInputFiles(join(SAMPLES, "voice_bakso.wav"));
await page.waitForTimeout(300);
await page.getByRole("button", { name: /Kirim voice note/ }).click();
await page.waitForTimeout(1500);
r = await waitDone();
const voiceRun = (await api(`/v1/runs/${runId()}`)).body;
const tr = voiceRun?.steps?.find((s) => s.name === "transkripsi_suara")?.output;
note("suara: transkripsi + agent", r.status === "Selesai" && !!tr?.text, `"${tr?.text}" outcome=${voiceRun?.outcome} ${r.secs}s`);
const audioOk = await page.locator("audio").first().evaluate((a) => new Promise((res) => {
  a.preload = "auto"; a.load();
  a.onloadedmetadata = () => res(a.duration); a.onerror = () => res(-1); setTimeout(() => res(-2), 8000);
})).catch(() => -3);
note("suara: audio asli dari /v1/runs/{id}/audio bisa diputar", audioOk > 0, `durasi ${audioOk}`);
await shot("06-suara", true);

// ------------------------------------------------------------------ 5. alur konfirmasi
await openSandbox();
await tab("Chat");
await page.getByPlaceholder(/ngopi di warkop/).fill("tadi bayar 50");
await page.getByRole("button", { name: "Kirim", exact: true }).click();
await page.waitForTimeout(1500);
r = await waitDone();
const askRun = runId();
const ask = (await api(`/v1/runs/${askRun}`)).body;
note("konfirmasi: run awaiting_confirmation", ask?.status === "awaiting_confirmation", `status=${ask?.status} outcome=${ask?.outcome}: ${ask?.final_answer?.slice(0, 100)}`);
await shot("08-konfirmasi-tanya");
if (ask?.status === "awaiting_confirmation") {
  await page.getByPlaceholder(/50 ribu, parkir/).fill("50 ribu, bayar parkir motor");
  await page.getByRole("button", { name: /Jawab/ }).click();
  await page.waitForTimeout(1500);
  r = await waitDone();
  const ans = (await api(`/v1/runs/${runId()}`)).body;
  note("konfirmasi: jawaban memakai conversation_id sama + answers_run_id", ans?.conversation_id === ask.conversation_id && ans?.answers_run_id === askRun,
    `outcome=${ans?.outcome}, answers_run_id=${ans?.answers_run_id}, tx=${ans?.transactions?.map((t) => t.amount).join(",")}`);
  const ask2 = (await api(`/v1/runs/${askRun}`)).body;
  note("konfirmasi: run pertanyaan punya answered_by_run_id", ask2?.answered_by_run_id === ans?.id, `${ask2?.answered_by_run_id}`);
  await shot("09-konfirmasi-dijawab", true);
  await page.goto(`${BASE}/#/sandbox?run=${askRun}`);
  await page.waitForTimeout(2000);
  note("konfirmasi: UI menandai pertanyaan sudah dijawab", (await page.getByText(/sudah dijawab →/).count()) > 0);
}

// ------------------------------------------------------------------ 6. ubah & hapus transaksi lewat UI
const txId = textSum?.transactions?.[0]?.id;
await page.goto(`${BASE}/#/transaksi`);
await page.waitForTimeout(2000);
await page.locator(".seg button", { hasText: "Semua" }).click();
await page.waitForTimeout(1500);
if (txId) {
  const row = page.locator("tbody tr", { has: page.locator("td", { hasText: new RegExp(`^${txId}$`) }) }).first();
  await row.getByRole("button", { name: "Ubah" }).click();
  await page.locator(".modal input[inputmode=numeric]").fill("32000");
  await page.locator(".modal input").nth(2).fill("bensin pertalite (diubah dari sandbox)");
  await page.getByRole("button", { name: "Simpan" }).click();
  await page.waitForTimeout(1500);
  const after = (await api(`/v1/transactions/${txId}`)).body;
  note("ubah transaksi (PATCH)", after?.amount === 32000 && /sandbox/.test(after?.description ?? ""), `#${txId} → ${after?.amount} "${after?.description}"`);
  await shot("10-transaksi", true);
}
const victim = (await api("/v1/transactions?periode=semua&limit=200")).body?.transaksi?.find((t) => t.source === "voice") ;
if (victim) {
  const row = page.locator("tbody tr", { has: page.locator("td", { hasText: new RegExp(`^${victim.id}$`) }) }).first();
  await row.getByRole("button", { name: "Hapus" }).click();
  await page.locator(".modal").getByRole("button", { name: /Hapus/ }).click();
  await page.waitForTimeout(1500);
  const gone = await api(`/v1/transactions/${victim.id}`);
  const inList = (await api("/v1/transactions?periode=semua&limit=500")).body?.transaksi?.some((t) => t.id === victim.id);
  note("hapus transaksi (soft delete)", !inList, `#${victim.id}: GET → ${gone.status}, masih di daftar=${inList}`);
}

// ------------------------------------------------------------------ 7. riwayat & sistem akhir
await page.goto(`${BASE}/#/riwayat`);
await page.waitForTimeout(2500);
await shot("11-riwayat");
await page.goto(`${BASE}/#/sistem`);
await page.waitForTimeout(9000);
await shot("01-sistem", true);
await page.goto(`${BASE}/#/sandbox?run=${textRun}`);
await page.waitForTimeout(2000);
await shot("00-sandbox-utama");

await browser.close();
note("tidak ada error JS di console", errors.length === 0, errors.slice(0, 5).join(" | "));
writeFileSync(join(OUT, "live-e2e-hasil.json"), JSON.stringify({ when: new Date().toISOString(), log, errors }, null, 2));
console.log(`\n${log.filter((l) => l.ok).length}/${log.length} PASS`);
