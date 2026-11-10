"""Membuat data contoh untuk uji: gambar struk (PIL), voice note (espeak-ng lokal), dan beberapa file .eml.

Semua nama bank/toko/e-wallet FIKTIF. Jalankan: uv run python scripts/make_samples.py
"""
import random
import sys
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from tts_espeak import synth  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "samples"
TZ = ZoneInfo("Asia/Jakarta")
NOW = datetime.now(TZ).replace(microsecond=0)
YESTERDAY = NOW - timedelta(days=1)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def receipt(variant: int = 1) -> Path:
    f = ImageFont.truetype(FONT, 20)
    fb = ImageFont.truetype(FONT_B, 22)
    d = YESTERDAY.strftime("%d-%m-%Y")
    lines = [
        ("b", "   SERBA ADA MART"),
        ("n", "  Jl. Merpati No. 12, Ciputat"),
        ("n", "  NPWP 01.234.567.8-999.000"),
        ("n", f"{d} 19:42   Kasir: RINA"),
        ("n", "--------------------------------"),
        ("n", "AIR MINERAL 600ML"),
        ("n", "   2 x 3.500             7.000"),
        ("n", "ROTI TAWAR GANDUM       12.500"),
        ("n", "MIE GORENG INSTAN"),
        ("n", "   3 x 3.100             9.300"),
        ("n", "SABUN MANDI CAIR 250ML  18.900"),
        ("n", "--------------------------------"),
        ("b", "TOTAL                   47.700"),
        ("n", "TUNAI                  100.000"),
        ("n", "KEMBALI                 52.300"),
        ("n", "--------------------------------"),
        ("n", "   Terima kasih, selamat"),
        ("n", "     berbelanja kembali"),
    ]
    w, h = 460, 40 + 32 * len(lines)
    img = Image.new("RGB", (w, h), (250, 248, 240))
    dr = ImageDraw.Draw(img)
    rnd = random.Random(7 * variant)
    for _ in range(1500):  # sedikit noise kertas
        x, y = rnd.randrange(w), rnd.randrange(h)
        c = rnd.randrange(215, 245)
        dr.point((x, y), fill=(c, c, c - 5))
    for i, (style, text) in enumerate(lines):
        dr.text((16, 20 + i * 32), text, fill=(30, 30, 30), font=fb if style == "b" else f)
    img = img.rotate(1.2 if variant == 1 else -2.0, expand=True, fillcolor=(200, 200, 200)).filter(ImageFilter.GaussianBlur(0.4))
    # variant 2 = "foto kedua" dari struk yang sama (file berbeda, isi sama) untuk uji anti-dobel semantik
    p = OUT / ("struk_serba_ada.png" if variant == 1 else "struk_serba_ada_foto2.jpg")
    img.save(p)
    return p


def voice() -> Path:
    p = OUT / "voice_bakso.wav"
    synth("tadi siang aku makan bakso habis dua puluh lima ribu rupiah", str(p), "id", 140)
    return p


def eml(name: str, sender: str, subject: str, when: datetime, text: str | None = None, html: str | None = None) -> Path:
    m = EmailMessage()
    m["From"] = sender
    m["To"] = "pemilik@contoh.local"
    m["Subject"] = subject
    m["Date"] = format_datetime(when)
    m["Message-ID"] = f"<{name}.{int(when.timestamp())}@contoh.local>"
    if text:
        m.set_content(text)
    if html:
        if text:
            m.add_alternative(html, subtype="html")
        else:
            m.set_content(html, subtype="html")
    p = OUT / f"{name}.eml"
    p.write_bytes(bytes(m))
    return p


def emails() -> list[Path]:
    t = NOW.strftime("%d %B %Y")
    out = [
        eml(
            "email_transfer_masuk", "Bank Sejahtera Notifikasi <notifikasi@banksejahtera.contoh>",
            "Dana Masuk ke Rekening Anda", NOW - timedelta(hours=2),
            text=(
                "Yth. Nasabah,\n\nKami informasikan telah terjadi transaksi kredit pada rekening Anda:\n\n"
                f"Tanggal      : {t} 09:15:22 WIB\n"
                "Jenis        : Transfer Masuk (BI-FAST)\n"
                "Dari         : ANDI PRATAMA\n"
                "Ke Rekening  : 123-***-7890\n"
                "Nominal      : Rp 750.000,00\n"
                "Berita       : bayar patungan kos\n\n"
                "Terima kasih telah menggunakan layanan Bank Sejahtera.\n"
                "Email ini dikirim otomatis, mohon tidak membalas."
            ),
        ),
        eml(
            "email_ewallet_bayar", "DompetKu <no-reply@dompetku.contoh>",
            "Pembayaran Berhasil - Kopi Senja", YESTERDAY.replace(hour=16, minute=5),
            html=(
                "<html><body><h2>Pembayaran Berhasil</h2>"
                "<p>Hai, pembayaranmu dengan saldo DompetKu sudah berhasil.</p>"
                "<table><tr><td>Merchant</td><td>Kopi Senja Ciputat</td></tr>"
                "<tr><td>Metode</td><td>QRIS</td></tr>"
                f"<tr><td>Waktu</td><td>{YESTERDAY.strftime('%d/%m/%Y')} 16:05</td></tr>"
                "<tr><td>Total Bayar</td><td><b>Rp42.000</b></td></tr>"
                "<tr><td>ID Transaksi</td><td>DK2611080001234</td></tr></table>"
                "<p>Saldo tersisa: Rp158.500</p></body></html>"
            ),
        ),
        eml(
            "email_invoice_belanja", "TokoKita Marketplace <invoice@tokokita.contoh>",
            "Invoice INV/20261109/TK/88231 - Pesanan Dibayar", NOW - timedelta(hours=1),
            text=(
                "Terima kasih telah berbelanja di TokoKita!\n\n"
                "No. Invoice : INV/20261109/TK/88231\n"
                f"Tanggal     : {t}\n"
                "Status      : LUNAS (dibayar via Virtual Account)\n\n"
                "Rincian pesanan:\n"
                "1 x Mouse Wireless Ergonomis       Rp 129.000\n"
                "1 x Mousepad XL                    Rp  45.000\n"
                "Ongkos kirim                       Rp  12.000\n"
                "Diskon ongkir                     -Rp  10.000\n"
                "Biaya layanan                      Rp   1.000\n"
                "------------------------------------------\n"
                "Total Pembayaran                   Rp 177.000\n\n"
                "Pesanan akan segera dikirim oleh penjual."
            ),
        ),
        eml(
            "email_promo", "TokoKita Marketplace <promo@tokokita.contoh>",
            "FLASH SALE 11.11 diskon s/d 90%!", NOW - timedelta(hours=3),
            text="Jangan lewatkan Flash Sale 11.11! Diskon hingga 90% dan gratis ongkir. Belanja sekarang di aplikasi TokoKita.",
        ),
    ]
    return out


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for p in [receipt(), receipt(2), voice(), *emails()]:
        print(p, p.stat().st_size, "bytes")
