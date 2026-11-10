// Screenshot Playwright (lebar 1440) dari mode mock. Jalankan saat dev server hidup:
//   npm run dev   (terminal lain)    lalu    npm run screenshots
// Opsional: BASE=http://localhost:5180  CHROME=/path/ke/chromium
import { chromium } from "playwright";
import { readFileSync, existsSync, readdirSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const BASE = process.env.BASE ?? "http://localhost:5180";
const OUT = new URL("../screenshots/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });
const real = JSON.parse(readFileSync(new URL("../src/mock/real.json", import.meta.url)));

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const root = join(homedir(), ".cache/ms-playwright");
  if (!existsSync(root)) return undefined;
  for (const d of readdirSync(root).filter((x) => x.startsWith("chromium")).sort().reverse()) {
    for (const p of ["chrome-linux/headless_shell", "chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux/chrome"]) {
      if (existsSync(join(root, d, p))) return join(root, d, p);
    }
  }
  return undefined;
}

const find = (f) => real.runs.find(f)?.id;
const textRun = find((r) => r.input_type === "text" && r.outcome === "recorded" && r.steps.length <= 8);
const receiptRun = find((r) => r.input_type === "receipt" && r.outcome === "recorded");
const confirmRun = find((r) => r.outcome === "needs_confirmation") ?? find((r) => r.status === "awaiting_confirmation");
const llmSeq = real.runs.find((r) => r.id === textRun)?.steps.find((s) => s.type === "llm")?.seq ?? 3;

const browser = await chromium.launch({ executablePath: findChrome() });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, colorScheme: "dark" });
await ctx.addInitScript(() => {
  localStorage.setItem("kas-sandbox.settings", JSON.stringify({ baseUrl: "http://localhost:8000", apiKey: "", mock: true, pollMs: 1000 }));
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

async function shot(name, hash, { full = false, wait = 600, before } = {}) {
  await page.goto(`${BASE}/${hash}`);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(wait);
  if (before) await before();
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: full });
  console.log("✓", name);
}

await shot("01-sandbox-utama", `#/sandbox?run=${textRun}`);
await shot("02-trace-terbuka", `#/sandbox?run=${textRun}&step=${llmSeq}`, {
  full: true,
  before: async () => { await page.getByRole("tab", { name: /Prompt/ }).first().click(); await page.waitForTimeout(200); },
});
await shot("03-struk-bbox", `#/sandbox?run=${receiptRun}`, { full: true, wait: 900 });
await shot("04-transaksi", "#/transaksi", { full: true });
await shot("05-sistem", "#/sistem", { full: true, wait: 6500 });
await shot("06-riwayat-antrean", "#/riwayat", { full: false });
await shot("07-konfirmasi", `#/sandbox?run=${confirmRun}`);
await shot("10-struk-fallback-vision", "#/sandbox?run=run_mock_fallback", { full: true, wait: 900 });

// Run live: kirim chat, tangkap saat reasoning mengalir (llm_delta).
await page.goto(`${BASE}/#/sandbox`);
await page.waitForLoadState("networkidle");
await page.getByPlaceholder(/ngopi di warkop/).fill("beli makan siang nasi padang 28rb");
await page.getByRole("button", { name: "Kirim", exact: true }).click();
await page.waitForSelector(".live-text.caret", { timeout: 15000 });
await page.waitForTimeout(1300);
await page.screenshot({ path: `${OUT}08-live-reasoning.png` });
console.log("✓ 08-live-reasoning");
await page.waitForSelector(".answer", { timeout: 30000 });
await page.waitForTimeout(500);
await page.screenshot({ path: `${OUT}09-live-selesai.png`, fullPage: true });
console.log("✓ 09-live-selesai");

await browser.close();
if (errors.length) { console.log("Error di console browser:\n" + errors.join("\n")); process.exitCode = 1; }
