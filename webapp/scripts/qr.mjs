// Cetak QR untuk sebuah link di terminal dan simpan PNG (untuk slide).
//   node scripts/qr.mjs "<link>" [share/qr.png]
import QRCode from "qrcode";

const [link, out = new URL("../share/qr.png", import.meta.url).pathname] = process.argv.slice(2);
if (!link) { console.error("pakai: node scripts/qr.mjs <link> [file.png]"); process.exit(2); }
console.log(await QRCode.toString(link, { type: "terminal", small: true, errorCorrectionLevel: "M" }));
await QRCode.toFile(out, link, { width: 900, margin: 2, errorCorrectionLevel: "M", color: { dark: "#0d0f12", light: "#ffffff" } });
console.log(`QR PNG: ${out}`);
