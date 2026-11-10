"""Utilitas kecil lintas modul."""
import re

# Path absolut (≥2 segmen) yang tidak didahului huruf/angka/titik dua (agar URL seperti http://host:11434/api tidak kena).
_ABS_PATH = re.compile(r"(?<![\w:.~])(?:/[^\s/'\"\]\)\(,:;]+){2,}")


def redact(text: object) -> str:
    """Samarkan path absolut server di pesan error yang dikirim ke klien: '/home/u/.../uploads/x.png' -> '…/x.png'."""
    return _ABS_PATH.sub(lambda m: "…/" + m.group(0).rsplit("/", 1)[-1], str(text))


def err(e: BaseException) -> str:
    """repr(exception) yang aman dikirim ke klien."""
    return redact(repr(e))
