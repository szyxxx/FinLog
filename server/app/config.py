"""Konfigurasi server, dibaca dari environment / file .env."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000
    # Daftar API key dipisah koma. Header: X-API-Key (atau ?api_key= untuk EventSource/SSE).
    api_keys: str = "dev-key-ganti-saya"
    cors_origins: str = "*"
    rate_limit_per_minute: int = 30  # per API key, hanya untuk endpoint yang membuat job

    db_path: str = str(BASE_DIR / "data" / "finance.db")
    upload_dir: str = str(BASE_DIR / "data" / "uploads")
    timezone: str = "Asia/Jakarta"

    ollama_url: str = "http://localhost:11434"
    llm_model: str = "qwen3.5:9b"
    vision_model: str = "qwen3.5:9b"
    # Thinking qwen3.5 dinyalakan (keputusan pemilik proyek): reasoning masuk trace sebagai step `reasoning`
    # dan di-stream live (event SSE `llm_delta`). Qwen menyarankan temperature ~0.6 (bukan greedy) saat thinking.
    llm_think: bool = True
    # Ekstraksi struk TANPA thinking (keputusan pemilik): thinking vision ±146 dtk/struk. None = ikut llm_think.
    vision_think: bool | None = False
    llm_temperature: float = 0.6
    llm_num_ctx: int = 8192
    llm_num_predict: int = 4096  # batas token output per panggilan (termasuk thinking) agar tidak berputar tanpa akhir
    llm_read_timeout_s: float = 300  # jeda maksimum antar-chunk dari Ollama
    llm_keep_alive: str = "30m"
    agent_max_steps: int = 6
    agent_run_timeout_s: float = 600  # batas total satu run (praproses + semua langkah LLM/tool)
    # Mesin orkestrasi agent: langgraph (graf LangGraph, app/graph_agent.py) | custom (loop lama, app/agent.py).
    # Kontrak API/trace/SSE sama untuk keduanya. Kembali ke mesin lama: AGENT_ENGINE=custom lalu restart.
    agent_engine: Literal["langgraph", "custom"] = "langgraph"
    # Checkpointer SQLite LangGraph (thread graf, interrupt konfirmasi). Kosong = <DB_PATH tanpa .db>_langgraph.db
    langgraph_checkpoint_db: str = ""

    # Jalur ekstraksi struk:
    #   ocr_llm  = OCR (CPU) -> LLM teks saja menyusun JSON -> validasi -> fallback vision bila gagal/ragu (default)
    #   ocr_only = OCR + aturan/regex, tanpa LLM untuk ekstraksi
    #   vision   = model vision membaca gambar langsung (jalur lama)
    receipt_mode: Literal["ocr_llm", "ocr_only", "vision"] = "ocr_llm"
    receipt_fallback_vision: bool = True  # mode ocr_llm: pakai vision bila validasi gagal
    # mode ocr_llm: coba parser aturan dulu; bila lolos validasi, LLM tidak dipanggil (jauh lebih cepat)
    receipt_rules_first: bool = False
    ocr_min_conf: float = 0.80  # rata-rata confidence OCR minimum agar hasil OCR dipercaya
    ocr_max_side: int = 1600  # gambar diperkecil ke sisi terpanjang ini sebelum OCR

    whisper_model: str = "small"
    whisper_device: str = "auto"  # auto | cuda | cpu
    whisper_compute_type_gpu: str = "int8_float16"
    whisper_compute_type_cpu: str = "int8"
    whisper_language: str = "id"

    # Fitur email (upload .eml + IMAP) DINONAKTIFKAN (keputusan pemilik). Kode tetap ada; set true untuk mengembalikan.
    # Saat nonaktif, POST /v1/email dan /v1/email/imap/fetch menjawab 410.
    email_enabled: bool = False

    # Konektor IMAP opsional (kosong = nonaktif)
    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    imap_search: str = "UNSEEN"
    imap_max_messages: int = 10

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def langgraph_checkpoint_path(self) -> str:
        if self.langgraph_checkpoint_db:
            return self.langgraph_checkpoint_db
        p = Path(self.db_path)
        return str(p.with_name(p.stem + "_langgraph.db"))

    @property
    def imap_enabled(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)


@lru_cache
def get_settings() -> Settings:
    return Settings()
