"""Speech-to-text dengan faster-whisper. GPU (int8_float16) bila tersedia, fallback CPU (int8)."""
import ctypes
import glob
import logging
import os
import subprocess
import threading
import time

import numpy as np

from ..config import get_settings

log = logging.getLogger("stt")
_model = None
_model_info: dict = {}
_lock = threading.Lock()
# Bias kosakata: catatan keuangan sehari-hari berbahasa Indonesia.
INITIAL_PROMPT = ("Catatan pemasukan dan pengeluaran: beli, bayar, makan, jajan, bensin, parkir, ojek online, "
                  "gajian, transfer, ribu, juta, rupiah.")


def _preload_cuda_libs() -> None:
    """ctranslate2 me-dlopen cuBLAS/cuDNN; muat dulu dari wheel nvidia-* agar tidak perlu LD_LIBRARY_PATH."""
    try:
        import nvidia.cublas
        import nvidia.cudnn
    except ImportError:
        return
    for pkg in (nvidia.cublas, nvidia.cudnn):
        for d in pkg.__path__:
            for so in sorted(glob.glob(os.path.join(d, "lib", "lib*.so*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def _load():
    global _model, _model_info
    if _model is not None:
        return _model
    s = get_settings()
    from faster_whisper import WhisperModel

    attempts = []
    if s.whisper_device in ("auto", "cuda"):
        attempts.append(("cuda", s.whisper_compute_type_gpu))
    if s.whisper_device in ("auto", "cpu"):
        attempts.append(("cpu", s.whisper_compute_type_cpu))
    errors = []
    for device, ctype in attempts:
        try:
            if device == "cuda":
                _preload_cuda_libs()
            t0 = time.time()
            m = WhisperModel(s.whisper_model, device=device, compute_type=ctype,
                             cpu_threads=8 if device == "cpu" else 0)
            # pemanasan kecil: memaksa inisialisasi kernel CUDA sekarang, bukan saat request pertama
            list(m.transcribe(np.zeros(16000, dtype=np.float32), language=s.whisper_language)[0])
            _model = m
            _model_info = {"model": s.whisper_model, "device": device, "compute_type": ctype,
                           "load_ms": int((time.time() - t0) * 1000), "fallback_errors": errors}
            log.info("whisper siap: %s", _model_info)
            return m
        except Exception as e:  # noqa: BLE001
            errors.append(f"{device}: {e!r}")
            log.warning("gagal memuat whisper di %s: %s", device, e)
    raise RuntimeError(f"tidak bisa memuat whisper: {errors}")


def model_info() -> dict:
    return dict(_model_info) if _model_info else {"loaded": False, "model": get_settings().whisper_model}


def decode_audio(path: str) -> np.ndarray:
    """Dekode format apa pun (ogg/opus WhatsApp, m4a, mp3, wav) via ffmpeg -> float32 16 kHz mono."""
    out = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, np.int16).astype(np.float32) / 32768.0


def transcribe(path: str) -> dict:
    """Blocking; panggil via asyncio.to_thread."""
    with _lock:
        m = _load()
        t0 = time.time()
        audio = decode_audio(path)
        segs, info = m.transcribe(audio, language=get_settings().whisper_language, beam_size=5, vad_filter=True,
                                  initial_prompt=INITIAL_PROMPT)
        segments = [{"start": round(sg.start, 2), "end": round(sg.end, 2), "text": sg.text.strip(),
                     "avg_logprob": round(sg.avg_logprob, 3)} for sg in segs]
        text = " ".join(sg["text"] for sg in segments).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "audio_seconds": round(info.duration, 2),
            "segments": segments,
            "engine": model_info(),
            "duration_ms": int((time.time() - t0) * 1000),
        }
