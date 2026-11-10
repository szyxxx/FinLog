#!/usr/bin/env bash
# Bagikan sandbox ke teman sekelas lewat link https publik (Cloudflare quick tunnel) + QR.
# Web app + AI server tetap jalan di laptop ini; web app meneruskan /v1 dan /health ke server (proxy Vite).
#
#   scripts/share.sh
#
# Tamu tidak perlu API key: proxy vite preview menyisipkan X-API-Key (key pertama API_KEYS di ../server/.env,
# atau env API_KEY) ke setiap request yang diteruskan. Key tidak pernah sampai ke browser, link, QR, atau log ini.
# Env opsional: PORT (preview, default 5190), API_TARGET (default http://localhost:8000),
#               CLOUDFLARED (path binary), SKIP_BUILD=1.
# Ctrl+C mematikan tunnel dan preview.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-5190}"
export API_TARGET="${API_TARGET:-http://localhost:8000}"
CLOUDFLARED="${CLOUDFLARED:-$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")}"
mkdir -p share
LOG_PREVIEW=share/preview.log
LOG_TUNNEL=share/tunnel.log

[ -x "$CLOUDFLARED" ] || { echo "cloudflared tidak ditemukan ($CLOUDFLARED). Lihat README: Bagikan ke teman sekelas."; exit 1; }

if curl -fsS -m 3 "$API_TARGET/health" >/dev/null 2>&1; then
  echo "Server AI: OK ($API_TARGET)"
else
  echo "PERINGATAN: server AI tidak menjawab di $API_TARGET/health. Jalankan server/run.sh dulu (link tetap dibuat)."
fi

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "Build web app…"
  npm run build >/dev/null
fi

PIDS=()
CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return
  CLEANED=1
  trap - INT TERM HUP EXIT
  echo
  echo "Mematikan tunnel dan preview…"
  for p in "${PIDS[@]}"; do
    pkill -TERM -P "$p" 2>/dev/null || true   # anak proses (bila ada)
    kill -TERM "$p" 2>/dev/null || true
  done
  sleep 1
  for p in "${PIDS[@]}"; do kill -KILL "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "Selesai. Link publik sudah mati."
}
# Setelah membersihkan, keluar: trap sinyal tanpa `exit` membuat bash melanjutkan skrip.
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 129' HUP
trap cleanup EXIT

# Jalankan binary vite langsung (bukan lewat npx) agar PID-nya bisa dimatikan dengan pasti.
node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port "$PORT" --strictPort >"$LOG_PREVIEW" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 40); do curl -fsS -m 1 "http://127.0.0.1:$PORT/" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS -m 2 "http://127.0.0.1:$PORT/" >/dev/null || { echo "vite preview gagal start, lihat $LOG_PREVIEW"; exit 1; }
echo "Preview: http://127.0.0.1:$PORT  (proxy → $API_TARGET)"

"$CLOUDFLARED" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG_TUNNEL" 2>&1 &
PIDS+=($!)
URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG_TUNNEL" | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "Tunnel gagal (tidak ada URL), lihat $LOG_TUNNEL"; exit 1; }
# Tunggu DNS/edge siap.
for _ in $(seq 1 30); do curl -fsS -m 5 "$URL/" >/dev/null 2>&1 && break; sleep 2; done

LINK="$URL/"
echo "$LINK" > share/link.txt
echo
echo "================================================================"
echo " Link tamu: $LINK"
echo "================================================================"
node scripts/qr.mjs "$LINK" share/qr.png
echo "Tunnel aktif. Tekan Ctrl+C untuk mematikan link publik."
wait -n || true  # berhenti bila tunnel atau preview mati
