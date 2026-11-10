// Screenshot web app untuk docs/srs-cara-kerja.md, dari MODE MOCK (?mock=1, data contoh, tanpa AI server).
// Butuh dev server web app hidup:  (cd webapp && npm run dev)   lalu   node docs/screenshots-srs.mjs
// Hasil: docs/img/srs-shot-*.png. Bilah atas (nama aplikasi) dipotong, banner "Mode mock" disembunyikan.
// Tidak mengubah apa pun di webapp/: Playwright dipakai dari webapp/node_modules.
import { createRequire } from "node:module";
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "..", "webapp", "package.json"));
const { chromium } = require("playwright");
const BASE = process.env.BASE ?? "http://localhost:5180";
const OUT = path.join(here, "img");

function findChrome() {
  if (process.env.CHROME) return process.env.CHROME;
  const root = path.join(homedir(), ".cache/ms-playwright");
  if (!existsSync(root)) return undefined;
  for (const d of readdirSync(root).filter((x) => x.startsWith("chromium")).sort().reverse())
    for (const p of ["chrome-linux/headless_shell", "chrome-headless-shell-linux64/chrome-headless-shell", "chrome-linux/chrome"])
      if (existsSync(path.join(root, d, p))) return path.join(root, d, p);
  return undefined;
}

// [nama, hash route, fungsi yang mengembalikan kotak potong {x, y, width, height} dalam koordinat halaman]
const card = (sel) => `document.querySelector(${JSON.stringify(sel)}).closest("section").getBoundingClientRect()`;
const SHOTS = [
  // input + jawaban (tanpa kartu transaksi: data mock sudah memuat ralat 35rb sesudahnya, jadi kartu tidak cocok dengan jawaban)
  ["srs-shot-teks", "#/sandbox?run=run_a2f262b077b8", `(() => { const a = document.querySelector(".topbar").getBoundingClientRect(); const b = document.querySelector(".answer").getBoundingClientRect();
     return { x: 0, y: a.bottom, width: innerWidth, height: b.bottom - a.bottom + 22 }; })()`],
  // panel struk: foto + kotak OCR + hasil ekstraksi + cek validasi
  ["srs-shot-struk", "#/sandbox?run=run_680788593f5b", `(() => { const r = ${card(".receipt-viewer, .rv, img")};
     return { x: r.left - 4, y: r.top - 4, width: r.width + 8, height: Math.min(r.height + 8, 900) }; })()`],
  // pertanyaan konfirmasi dari agen (kolom kanan)
  ["srs-shot-konfirmasi", "#/sandbox?run=run_99ce24acb87e", `(() => { const a = ${card(".run-hero")}; const c = [...document.querySelectorAll("section")].find((e) => /bertanya/i.test(e.textContent) && !e.querySelector(".run-hero"));
     const b = c.getBoundingClientRect(); return { x: a.left - 4, y: a.top - 4, width: a.width + 8, height: b.bottom - a.top + 8 }; })()`],
];
const W = Number(process.env.W ?? 1280);
// Sembunyikan banner mock, ID teknis (run_/conv_), alamat endpoint, dan path berkas yang tampil di UI.
const HIDE = `(() => {
  document.querySelectorAll(".run-hero > div:last-child").forEach((e) => (e.style.visibility = "hidden"));
  for (const e of document.querySelectorAll("body *")) {
    if (e.children.length === 0 && /run_[0-9a-f]|conv_|\\/v1\\/|server\\/data/.test(e.textContent))
      (e.closest("button, .tiny") || e).style.visibility = "hidden";
  }
})()`;

const browser = await chromium.launch({ executablePath: findChrome() });
const ctx = await browser.newContext({ viewport: { width: W, height: 1000 }, deviceScaleFactor: 1.5 });
const page = await ctx.newPage();
for (const [name, hash, clipJs] of SHOTS) {
  await page.goto(`${BASE}/?mock=1${hash}`);
  await page.waitForLoadState("networkidle");
  await page.addStyleTag({ content: [
    ".banner.mock{display:none!important}",
    // rincian cek dan label jalur berisi nama variabel teknis: disembunyikan, judul cek tetap tampil
    ".check small{display:none!important}",
    ".receipt-grid .stack > .row.wrap > :nth-child(-n+2){display:none!important}",
  ].join("\n") });
  await page.waitForTimeout(1200);
  await page.evaluate(HIDE);
  const r = await page.evaluate(clipJs);
  const sy = await page.evaluate(() => scrollY);
  const clip = { x: Math.max(0, r.x), y: r.y + sy, width: r.width, height: r.height };
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true, clip });
  console.log("screenshot:", `img/${name}.png`, clip);
}
await browser.close();
