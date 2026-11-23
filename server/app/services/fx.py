"""Alat eksternal: kurs mata uang online (untuk transaksi dalam mata uang asing).

Sumber utama Frankfurter (kurs referensi ECB, gratis, tanpa API key, mendukung tanggal historis); cadangan
open.er-api.com (hanya kurs terbaru). Yang dikirim keluar hanya kode mata uang dan tanggal, tidak ada nominal atau
data transaksi pengguna. Hasil di-cache per (mata uang, tanggal) di tabel `fx_cache`, jadi permintaan yang sama tidak
memanggil internet lagi dan tetap bisa dijawab saat offline.
"""
import time
from datetime import date

import httpx

from .. import db
from ..config import get_settings

# Alias yang sering diucapkan pengguna -> kode ISO 4217
ALIASES = {
    "dolar": "USD", "dollar": "USD", "usd": "USD", "$": "USD", "dolar as": "USD",
    "dolar singapura": "SGD", "sgd": "SGD", "ringgit": "MYR", "rm": "MYR", "myr": "MYR",
    "yen": "JPY", "jpy": "JPY", "euro": "EUR", "eur": "EUR", "€": "EUR",
    "won": "KRW", "krw": "KRW", "baht": "THB", "thb": "THB", "pound": "GBP", "poundsterling": "GBP", "gbp": "GBP",
    "yuan": "CNY", "rmb": "CNY", "cny": "CNY", "dolar australia": "AUD", "aud": "AUD", "riyal": "SAR", "sar": "SAR",
    "rupiah": "IDR", "idr": "IDR", "rp": "IDR", "peso": "PHP", "php": "PHP", "rupee": "INR", "inr": "INR",
    "dolar hongkong": "HKD", "hkd": "HKD", "franc": "CHF", "chf": "CHF",
}


class FxError(Exception):
    pass


def normalize_code(v: str | None) -> str:
    s = str(v or "").strip().lower()
    if s in ALIASES:
        return ALIASES[s]
    s = s.upper()
    if len(s) == 3 and s.isalpha():
        return s
    raise FxError(f"mata uang tidak dikenal: {v!r} (pakai kode 3 huruf, mis. USD, JPY, SGD)")


def _cached(base: str, quote: str, day: str) -> dict | None:
    return db.query_one("SELECT * FROM fx_cache WHERE base=? AND quote=? AND day=?", (base, quote, day))


async def get_rate(base: str, quote: str = "IDR", on: str | None = None) -> dict:
    """Kurs 1 `base` dalam `quote` pada tanggal `on` (YYYY-MM-DD, default hari ini)."""
    s = get_settings()
    base, quote = normalize_code(base), normalize_code(quote)
    today = db.now_local().date().isoformat()
    day = on or today
    if base == quote:
        return {"base": base, "quote": quote, "rate": 1.0, "rate_date": day, "provider": "-", "cache": False}
    hit = _cached(base, quote, day)
    if hit:
        return {"base": base, "quote": quote, "rate": hit["rate"], "rate_date": hit["rate_date"],
                "provider": hit["provider"], "cache": True}
    if not s.fx_enabled:
        raise FxError("alat kurs online dinonaktifkan (FX_ENABLED=false)")
    errors = []
    t0 = time.time()
    async with httpx.AsyncClient(timeout=s.fx_timeout_s, headers={"User-Agent": "finlog-local-agent"}) as c:
        try:
            path = "latest" if day >= today else day
            r = await c.get(f"{s.fx_primary_url}/{path}", params={"base": base, "symbols": quote})
            r.raise_for_status()
            j = r.json()
            rate, rate_date, provider = float(j["rates"][quote]), j.get("date") or day, "frankfurter.dev (ECB)"
        except Exception as e:  # noqa: BLE001
            errors.append(f"frankfurter: {type(e).__name__}: {e}"[:200])
            rate = None
        if rate is None:
            if day < today:
                # cadangan hanya punya kurs terbaru; tetap dipakai tetapi diberi tanda
                errors.append("sumber cadangan hanya punya kurs terbaru, bukan kurs tanggal transaksi")
            try:
                r = await c.get(f"{s.fx_fallback_url}/{base}")
                r.raise_for_status()
                j = r.json()
                if j.get("result") != "success":
                    raise FxError(j.get("error-type") or "gagal")
                rate = float(j["rates"][quote])
                rate_date = date.fromtimestamp(j.get("time_last_update_unix") or time.time()).isoformat()
                provider = "open.er-api.com"
            except Exception as e:  # noqa: BLE001
                errors.append(f"open.er-api: {type(e).__name__}: {e}"[:200])
                # terakhir: cache terdekat sebelum tanggal itu (offline)
                near = db.query_one("SELECT * FROM fx_cache WHERE base=? AND quote=? AND day<=? ORDER BY day DESC LIMIT 1",
                                    (base, quote, day))
                if near:
                    return {"base": base, "quote": quote, "rate": near["rate"], "rate_date": near["rate_date"],
                            "provider": near["provider"] + " (cache lama, offline)", "cache": True, "peringatan": errors}
                raise FxError("; ".join(errors)) from e
    db.execute("INSERT OR REPLACE INTO fx_cache(base, quote, day, rate, provider, rate_date, fetched_at) VALUES (?,?,?,?,?,?,?)",
               (base, quote, day, rate, provider, rate_date, db.iso_now()))
    out = {"base": base, "quote": quote, "rate": rate, "rate_date": rate_date, "provider": provider, "cache": False,
           "latency_ms": int((time.time() - t0) * 1000)}
    if errors:
        out["peringatan"] = errors
    return out


async def convert(amount: float, frm: str, to: str = "IDR", on: str | None = None) -> dict:
    r = await get_rate(frm, to, on)
    res = amount * r["rate"]
    return {**r, "jumlah_asli": amount, "hasil": round(res) if r["quote"] == "IDR" else round(res, 2)}
