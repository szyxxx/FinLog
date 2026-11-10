"""Parsing email (.eml / IMAP) menjadi teks bersih untuk agent."""
import email
import hashlib
import html
import imaplib
import re
from email import policy
from email.utils import parsedate_to_datetime

from ..config import get_settings

MAX_BODY_CHARS = 4000


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>|</h\d>", "\n", s)
    s = re.sub(r"(?i)</td>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def parse_eml(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    text_part = msg.get_body(preferencelist=("plain",))
    html_part = msg.get_body(preferencelist=("html",))
    if text_part is not None:
        body, body_type = text_part.get_content(), "text/plain"
    elif html_part is not None:
        body, body_type = _html_to_text(html_part.get_content()), "text/html"
    else:
        body, body_type = "", "none"
    attachments = [{"filename": p.get_filename(), "content_type": p.get_content_type()}
                   for p in msg.iter_attachments()]
    date_iso = None
    if msg["Date"]:
        try:
            date_iso = parsedate_to_datetime(msg["Date"]).isoformat()
        except (TypeError, ValueError):
            date_iso = str(msg["Date"])
    body = body.strip()
    return {
        "from": str(msg["From"] or ""),
        "to": str(msg["To"] or ""),
        "subject": str(msg["Subject"] or ""),
        "date": date_iso,
        "message_id": str(msg["Message-ID"] or ""),
        "body_type": body_type,
        "body": body[:MAX_BODY_CHARS],
        "body_truncated": len(body) > MAX_BODY_CHARS,
        "attachments": attachments,
    }


def message_key(raw: bytes, parsed: dict | None = None) -> str:
    """Kunci anti-dobel email: Message-ID bila ada, kalau tidak hash isi mentah."""
    mid = (parsed or parse_eml(raw)).get("message_id", "").strip().strip("<>").lower()
    return f"email:msgid:{mid}" if mid else "email:sha256:" + hashlib.sha256(raw).hexdigest()


def fetch_imap() -> list[bytes]:
    """Ambil email mentah dari IMAP (konfigurasi .env). Tidak menandai email; pakai BODY.PEEK."""
    s = get_settings()
    if not s.imap_enabled:
        raise RuntimeError("IMAP belum dikonfigurasi (IMAP_HOST, IMAP_USER, IMAP_PASSWORD di .env)")
    out: list[bytes] = []
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port) as M:
        M.login(s.imap_user, s.imap_password)
        M.select(s.imap_folder, readonly=True)
        typ, data = M.search(None, s.imap_search)
        ids = data[0].split()[-s.imap_max_messages:] if data and data[0] else []
        for i in ids:
            typ, msg_data = M.fetch(i, "(BODY.PEEK[])")
            for part in msg_data:
                if isinstance(part, tuple):
                    out.append(part[1])
    return out
