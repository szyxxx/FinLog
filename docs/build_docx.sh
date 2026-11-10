#!/usr/bin/env bash
# Ekspor ulang docs/use-case.md -> docs/use-case.docx (diagram dari docs/diagrams/ dan Mermaid dirender jadi PNG).
# Versi lengkap lama disimpan apa adanya di use-case-lengkap.md / use-case-lengkap.docx.
# Tanpa sudo: dependensi dipasang lokal (npm di docs/.build, Python lewat uv).
#
# Pemakaian:  docs/build_docx.sh            # build + isi nomor halaman daftar isi (bila LibreOffice ada)
#             docs/build_docx.sh --pdf      # sekaligus hasilkan docs/.build/preview/use-case.pdf + PNG halaman
#             docs/build_docx.sh --doc srs-cara-kerja [--pdf]   # dokumen lain: docs/<nama>.md -> docs/<nama>.docx
# Opsional :  SOFFICE=/path/ke/soffice  CHROME=/path/ke/chrome-headless-shell
set -euo pipefail
cd "$(dirname "$0")"
DOC=use-case; PDF=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pdf) PDF=1 ;;
    --doc) DOC="$2"; shift ;;
    *) echo "Opsi tidak dikenal: $1" >&2; exit 2 ;;
  esac
  shift
done
BUILD=.build
mkdir -p "$BUILD"

# 1) mermaid-cli lokal
if [ ! -x "$BUILD/node_modules/.bin/mmdc" ]; then
  [ -f "$BUILD/package.json" ] || echo '{"name":"docbuild","private":true}' > "$BUILD/package.json"
  (cd "$BUILD" && PUPPETEER_SKIP_DOWNLOAD=1 npm install --no-audit --no-fund @mermaid-js/mermaid-cli)
fi

# 2) browser headless untuk mermaid-cli (pakai Chromium Playwright bila ada)
CHROME="${CHROME:-$(ls -d "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell 2>/dev/null | tail -1 || true)}"
if [ -z "$CHROME" ]; then
  CHROME="$(ls -d "$HOME"/.cache/puppeteer/chrome-headless-shell/*/*/chrome-headless-shell 2>/dev/null | tail -1 || true)"
fi
if [ -n "$CHROME" ]; then
  printf '{"executablePath":"%s","args":["--no-sandbox","--disable-gpu"]}\n' "$CHROME" > "$BUILD/puppeteer.json"
elif [ ! -f "$BUILD/puppeteer.json" ]; then
  echo "Peringatan: browser headless tidak ditemukan. Set CHROME=... atau pasang via 'npx puppeteer browsers install chrome-headless-shell'." >&2
fi

# 3) diagram awam (HTML -> PNG di img/diagram-*.png)
node diagrams/render.mjs

# 4) bangun docx
uv run --quiet --with python-docx --with pillow python build_docx.py --doc "$DOC"

# 5) opsional: pratinjau PDF/PNG
if [ "$PDF" = 1 ]; then
  SO="${SOFFICE:-$(command -v soffice || true)}"
  [ -z "$SO" ] && SO="$HOME/tools/squashfs-root/opt/libreoffice26.8/program/soffice"
  mkdir -p "$BUILD/preview" && rm -f "$BUILD"/preview/*.png
  PROF="$(mktemp -d)"
  "$SO" -env:UserInstallation="file://$PROF" --headless --convert-to pdf --outdir "$BUILD/preview" "$DOC.docx" >/dev/null 2>&1
  rm -rf "$PROF"
  pdftoppm -r 70 -png "$BUILD/preview/$DOC.pdf" "$BUILD/preview/p"
  echo "PDF: $BUILD/preview/$DOC.pdf ($(pdfinfo "$BUILD/preview/$DOC.pdf" | awk '/Pages/{print $2}') halaman)"
fi
