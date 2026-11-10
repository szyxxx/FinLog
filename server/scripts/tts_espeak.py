"""TTS lokal minimal via libespeak-ng (ctypes) -> WAV 16-bit mono. Dipakai untuk membuat audio uji.

Pemakaian: python scripts/tts_espeak.py "teks" out.wav [voice=id]
"""
import ctypes
import sys
import wave

AUDIO_OUTPUT_SYNCHRONOUS = 2
espeakCHARS_UTF8 = 1

SynthCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)


def synth(text: str, out_path: str, voice: str = "id", rate: int = 150) -> None:
    lib = ctypes.CDLL("libespeak-ng.so.1")
    sample_rate = lib.espeak_Initialize(AUDIO_OUTPUT_SYNCHRONOUS, 0, None, 0)
    if sample_rate <= 0:
        raise RuntimeError("espeak_Initialize gagal")
    chunks: list[bytes] = []

    @SynthCallback
    def cb(wav, numsamples, events):
        if numsamples > 0:
            chunks.append(ctypes.string_at(wav, numsamples * 2))
        return 0

    lib.espeak_SetSynthCallback(cb)
    lib.espeak_SetVoiceByName(voice.encode())
    lib.espeak_SetParameter(1, rate, 0)  # espeakRATE
    data = text.encode("utf-8")
    buf = ctypes.create_string_buffer(data)
    lib.espeak_Synth(buf, len(data) + 1, 0, 0, 0, espeakCHARS_UTF8, None, None)
    lib.espeak_Synchronize()
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"".join(chunks))


if __name__ == "__main__":
    synth(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "id")
