// Render diagrams/diagram.html?d=<nama> menjadi docs/img/diagram-<nama>.png (skala 3x).
// Memakai puppeteer dari docs/.build (dipasang oleh build_docx.sh bersama mermaid-cli).
import { createRequire } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const docs = path.resolve(here, "..");
const require = createRequire(path.join(docs, ".build", "package.json"));
const puppeteer = require("puppeteer");

const src = readFileSync(path.join(here, "diagrams.js"), "utf8");
const names = [...src.matchAll(/^ {2}"?([\w-]+)"?: \{$/gm)].map(m => m[1]);
const pj = path.join(docs, ".build", "puppeteer.json");
const launch = existsSync(pj) ? JSON.parse(readFileSync(pj, "utf8")) : {};
const browser = await puppeteer.launch({ headless: true, ...launch });
const page = await browser.newPage();
await page.setViewport({ width: 1000, height: 1000, deviceScaleFactor: 3 });
for (const n of names) {
  const url = pathToFileURL(path.join(here, "diagram.html")).href + "?d=" + encodeURIComponent(n);
  await page.goto(url, { waitUntil: "load" });
  await page.waitForSelector("body[data-ready='1']");
  await page.evaluate(() => document.fonts.ready);
  const out = path.join(docs, "img", `diagram-${n}.png`);
  await (await page.$("#d")).screenshot({ path: out });
  console.log("diagram:", path.relative(docs, out));
}
await browser.close();
