#!/usr/bin/env bash
# Jalankan server (bind 0.0.0.0:8000 sesuai .env). Pastikan Ollama aktif: `ollama serve` / systemd.
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo "Dibuat .env dari .env.example — ganti API_KEYS!"; }
exec uv run python -m app.main
