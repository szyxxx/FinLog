# BELUM DIUJI (Docker belum terpasang di laptop server). Ollama tetap berjalan di host.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libespeak-ng1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.12
COPY app ./app
COPY scripts ./scripts

ENV HOST=0.0.0.0 PORT=8000 OLLAMA_URL=http://host.docker.internal:11434 \
    DB_PATH=/data/finance.db UPLOAD_DIR=/data/uploads HF_HOME=/data/hf-cache
VOLUME ["/data"]
EXPOSE 8000
CMD ["/app/.venv/bin/python", "-m", "app.main"]
