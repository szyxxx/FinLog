#!/usr/bin/env python3
"""Ekspor docs/use-case.md menjadi docs/use-case.docx (atau dokumen lain: --doc srs-cara-kerja -> docs/srs-cara-kerja.docx).

- Diagram awam: docs/diagrams/ (HTML) dirender ke docs/img/diagram-*.png oleh build_docx.sh (node diagrams/render.mjs).

- Diagram Mermaid dirender menjadi PNG (mermaid-cli lokal di docs/.build, di-cache per isi diagram).
- Heading, tabel, daftar, dan kutipan dikonversi dengan parser Markdown sederhana (khusus dokumen ini).
- Daftar isi dibuat sebagai field TOC Word; nomor halamannya diisi dari hasil render PDF
  (LibreOffice) bila tersedia. Di Word, klik kanan daftar isi > Update Field untuk menyegarkan.

Jalankan lewat docs/build_docx.sh (menyiapkan dependensi) atau langsung:
    uv run --with python-docx --with pillow python docs/build_docx.py [--doc NAMA] [--no-pdf-pass]

Penanda tambahan di Markdown: baris `<!-- pagebreak -->` = pindah halaman.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT  # noqa: F401
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
DOC = sys.argv[sys.argv.index("--doc") + 1] if "--doc" in sys.argv else "use-case"
SRC = HERE / f"{DOC}.md"
OUT = HERE / f"{DOC}.docx"
BUILD = HERE / ".build"
IMG_DIR = BUILD / "img"

BODY_FONT = "Times New Roman"
HEAD_FONT = "Calibri"
CODE_FONT = "Courier New"
BODY_PT = 11.5
TABLE_PT = 9.5
LINE_SPACING = 1.15
CONTENT_W_CM = 15.5  # A4 21 cm - kiri 3 cm - kanan 2.5 cm
MAX_IMG_H_CM = 22.0
ACCENT = RGBColor(0x1F, 0x3A, 0x5F)
HEADER_FILL = "D9E2F3"
KEY_FILL = "EEF2F8"

SOFFICE_CANDIDATES = [
    os.environ.get("SOFFICE", ""),
    shutil.which("soffice") or "",
    shutil.which("libreoffice") or "",
    str(Path.home() / "tools/squashfs-root/opt/libreoffice26.8/program/soffice"),
]


# --------------------------------------------------------------------------- util XML

def set_run_font(run, name=None, size=None, bold=None, italic=None, color=None):
    if name:
        run.font.name = name
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rfonts.set(qn(a), name)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if italic is not None:
        run.font.italic = italic
    if color is not None:
        run.font.color.rgb = color


def style_font(style, name, size=None, bold=None, italic=None, color=None):
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(a), name)
    for a in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        if rfonts.get(qn(a)) is not None:
            del rfonts.attrib[qn(a)]
    if size:
        style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if italic is not None:
        style.font.italic = italic
    if color is not None:
        style.font.color.rgb = color


def shade(element_pr, fill):
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    element_pr.append(shd)


def add_field(paragraph, instr, result_text="", **fontkw):
    """Field sederhana (mis. PAGE)."""
    def r(child):
        run = paragraph.add_run()
        run._r.append(child)
        set_run_font(run, **fontkw)
        return run
    b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin"); r(b)
    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = f" {instr} "; r(it)
    s = OxmlElement("w:fldChar"); s.set(qn("w:fldCharType"), "separate"); r(s)
    t = paragraph.add_run(result_text); set_run_font(t, **fontkw)
    e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end"); r(e)


# --------------------------------------------------------------------------- inline

INLINE_RE = re.compile(r"(`[^`]+`|\*\*.+?\*\*|(?<![\*\w])\*[^\*\s][^\*]*?\*(?![\*\w])|\[[^\]]+\]\([^)]*\))")


def add_inline(par, text, size=None, bold=False, italic=False, font=None):
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            _plain(par, text[pos:m.start()], size, bold, italic, font)
        tok = m.group(0)
        if tok.startswith("`"):
            run = par.add_run(tok[1:-1])
            set_run_font(run, name=CODE_FONT, size=(size or BODY_PT) - 1, bold=bold, italic=italic)
            rpr = run._element.get_or_add_rPr()
            shade(rpr, "F0F0F0")
        elif tok.startswith("**"):
            add_inline(par, tok[2:-2], size, True, italic, font)
        elif tok.startswith("["):
            label = re.match(r"\[([^\]]+)\]", tok).group(1)
            add_inline(par, label, size, bold, italic, font)
        else:
            add_inline(par, tok[1:-1], size, bold, True, font)
        pos = m.end()
    if pos < len(text):
        _plain(par, text[pos:], size, bold, italic, font)


def _plain(par, text, size, bold, italic, font):
    run = par.add_run(text)
    set_run_font(run, name=font, size=size, bold=bold or None, italic=italic or None)


# --------------------------------------------------------------------------- Mermaid

def render_mermaid(code: str) -> Path:
    """Render satu diagram. Baris pertama opsional: %% build: fontsize=16 nodespacing=30 rankspacing=40"""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    opts = {"fontsize": 16, "nodespacing": 30, "rankspacing": 40}
    m = re.match(r"\s*%%\s*build:(.*)\n", code)
    if m:
        for kv in m.group(1).split():
            k, _, v = kv.partition("=")
            opts[k] = int(v)
    fs = opts["fontsize"]
    cfg = {
        "theme": "default",
        "themeVariables": {"fontFamily": "Arial, Helvetica, sans-serif", "fontSize": f"{fs}px"},
        "flowchart": {"useMaxWidth": False, "htmlLabels": True, "nodeSpacing": opts["nodespacing"],
                      "rankSpacing": opts["rankspacing"], "padding": 8},
        "sequence": {"useMaxWidth": False, "wrap": True, "width": 150, "actorMargin": 30, "boxMargin": 5,
                     "messageMargin": 30, "mirrorActors": False, "messageFontSize": fs, "actorFontSize": fs,
                     "noteFontSize": fs - 2, "wrapPadding": 6, "diagramMarginX": 10, "diagramMarginY": 6,
                     "boxTextMargin": 3, "noteMargin": 6},
        "er": {"useMaxWidth": False},
        "state": {"useMaxWidth": False},
    }
    key = hashlib.sha1((code + json.dumps(cfg, sort_keys=True) + "v5").encode()).hexdigest()[:16]
    png = IMG_DIR / f"{key}.png"
    if png.exists():
        return png
    mmd = IMG_DIR / f"{key}.mmd"
    mmd.write_text(code, encoding="utf8")
    cfgf = IMG_DIR / f"{key}.json"
    cfgf.write_text(json.dumps(cfg), encoding="utf8")
    mmdc = BUILD / "node_modules/.bin/mmdc"
    cmd = [str(mmdc), "-i", str(mmd), "-o", str(png), "-c", str(cfgf), "-b", "white", "-s", "3"]
    pj = BUILD / "puppeteer.json"
    if pj.exists():
        cmd += ["-p", str(pj)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return png


# --------------------------------------------------------------------------- document setup

def setup_document() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin, sec.right_margin = Cm(3.0), Cm(2.5)
    sec.top_margin, sec.bottom_margin = Cm(2.5), Cm(2.3)
    sec.different_first_page_header_footer = True

    st = doc.styles
    normal = st["Normal"]
    style_font(normal, BODY_FONT, BODY_PT)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = LINE_SPACING
    # bahasa Indonesia
    rpr = normal.element.get_or_add_rPr()
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "id-ID"); lang.set(qn("w:eastAsia"), "id-ID")
    rpr.append(lang)

    sizes = {"Heading 1": 16, "Heading 2": 13, "Heading 3": 11.5}
    for name, size in sizes.items():
        h = st[name]
        style_font(h, HEAD_FONT, size, bold=True, color=ACCENT)
        h.paragraph_format.keep_with_next = True
        h.paragraph_format.space_before = Pt({"Heading 1": 18, "Heading 2": 14, "Heading 3": 10}[name])
        h.paragraph_format.space_after = Pt(6)
    style_font(st["Title"], HEAD_FONT, 24, bold=True, color=ACCENT)
    pf = st["Title"].paragraph_format
    pf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf.space_before, pf.space_after = Pt(120), Pt(18)
    # hilangkan garis bawah bawaan judul
    ppr = st["Title"].element.get_or_add_pPr()
    for b in ppr.findall(qn("w:pBdr")):
        ppr.remove(b)

    for lvl, left in ((1, 0), (2, 0.6)):
        s = st.add_style(f"toc {lvl}", 1)
        s.base_style = normal
        style_font(s, BODY_FONT, 10 if lvl == 1 else 9.5, bold=(lvl == 1))
        s.paragraph_format.left_indent = Cm(left)
        s.paragraph_format.space_after = Pt(0)
        s.paragraph_format.space_before = Pt(2 if lvl == 1 else 0)
        s.paragraph_format.line_spacing = 1.0
        s.paragraph_format.tab_stops.add_tab_stop(Cm(CONTENT_W_CM), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)

    cap = st["Caption"]
    style_font(cap, HEAD_FONT, 9.5, bold=False, italic=True, color=RGBColor(0x40, 0x40, 0x40))
    cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_after = Pt(10)

    # nomor halaman di footer (halaman pertama/sampul tanpa nomor)
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(fp, "PAGE", "1", name=HEAD_FONT, size=9.5)
    return doc


# --------------------------------------------------------------------------- blocks

def para(doc, text, **kw):
    p = doc.add_paragraph()
    add_inline(p, text, **kw)
    return p


def add_list_item(doc, marker, text, level):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    base = 0.9 + 0.9 * level
    pf.left_indent = Cm(base)
    pf.first_line_indent = Cm(-0.7 if marker != "•" else -0.5)
    pf.space_after = Pt(3)
    pf.tab_stops.add_tab_stop(Cm(base))
    r = p.add_run(marker + "\t")
    set_run_font(r, name=BODY_FONT)
    add_inline(p, text)
    return p


def add_quote(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.left_indent = Cm(0.4)
    pf.space_before, pf.space_after = Pt(6), Pt(8)
    ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    for k, v in (("val", "single"), ("sz", "18"), ("space", "8"), ("color", "1F3A5F")):
        left.set(qn(f"w:{k}"), v)
    bdr.append(left)
    ppr.append(bdr)
    shade(ppr, "F3F6FA")
    add_inline(p, text, size=BODY_PT - 1)
    return p


def table_widths(rows, ncols):
    weights = []
    for c in range(ncols):
        lens = [len(re.sub(r"[`*]", "", r[c])) if c < len(r) else 0 for r in rows]
        avg = sum(lens) / max(len(lens), 1)
        mx = max(lens) if lens else 0
        w = 0.55 * avg + 0.45 * min(mx, 70)
        weights.append(min(max(w, 7), 60))
    # lebar minimum: kata terpanjang di kolom tidak boleh terpotong
    mins = []
    for c in range(ncols):
        words = [len(w) for r in rows if c < len(r) for w in re.sub(r"[`*]", "", r[c]).split()]
        mins.append(min(max(words, default=3) * 0.2 + 0.45, 4.2))
    total = sum(weights)
    widths = [CONTENT_W_CM * w / total for w in weights]
    for _ in range(5):
        short = [i for i, w in enumerate(widths) if w < mins[i]]
        if not short:
            break
        for i in short:
            widths[i] = mins[i]
        rest = [i for i in range(ncols) if i not in short]
        spare = CONTENT_W_CM - sum(widths[i] for i in short)
        tot = sum(weights[i] for i in rest) or 1
        for i in rest:
            widths[i] = spare * weights[i] / tot
    return widths


def add_table(doc, rows, header_empty):
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    body_rows = rows[1:] if header_empty else rows
    data = body_rows
    t = doc.add_table(rows=len(data), cols=ncols)
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    tblPr = t._tbl.tblPr
    layout = OxmlElement("w:tblLayout"); layout.set(qn("w:type"), "fixed"); tblPr.append(layout)
    mar = OxmlElement("w:tblCellMar")
    for side, w in (("top", 40), ("left", 80), ("bottom", 40), ("right", 80)):
        e = OxmlElement(f"w:{side}"); e.set(qn("w:w"), str(w)); e.set(qn("w:type"), "dxa"); mar.append(e)
    tblPr.append(mar)
    widths = table_widths(data, ncols)
    # kolom pertama pada tabel kunci-nilai tidak perlu lebar
    for ri, rdata in enumerate(data):
        row = t.rows[ri]
        trPr = row._tr.get_or_add_trPr()
        cs = OxmlElement("w:cantSplit"); trPr.append(cs)
        is_head = (ri == 0 and not header_empty)
        if is_head:
            th = OxmlElement("w:tblHeader"); trPr.append(th)
        for ci in range(ncols):
            cell = row.cells[ci]
            cell.width = Cm(widths[ci])
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = 1.0
            text = rdata[ci]
            parts = re.split(r"<br\s*/?>", text)
            for k, part in enumerate(parts):
                if k:
                    p = cell.add_paragraph()
                    p.paragraph_format.space_after = Pt(0)
                    p.paragraph_format.line_spacing = 1.0
                add_inline(p, part, size=TABLE_PT, bold=is_head or (header_empty and ci == 0),
                           font=HEAD_FONT if is_head else None)
            tcPr = cell._tc.get_or_add_tcPr()
            if is_head:
                shade(tcPr, HEADER_FILL)
            elif header_empty and ci == 0:
                shade(tcPr, KEY_FILL)
    # grid widths
    grid = t._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths):
        gc.set(qn("w:w"), str(int(w / 2.54 * 1440)))
    if len(data) <= 10:  # tabel pendek: jangan dipecah antarhalaman
        for row in t.rows[:-1]:
            for cell in row.cells:
                for cp in cell.paragraphs:
                    cp.paragraph_format.keep_with_next = True
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(4)
    spacer.paragraph_format.line_spacing = 0.6
    return t


def add_picture(doc, png: Path, caption: str, screenshot: bool = False, width_cm: float | None = None):
    with Image.open(png) as im:
        wpx, hpx = im.size
    if width_cm:
        w = min(width_cm, CONTENT_W_CM)
    elif screenshot:
        w = CONTENT_W_CM
    else:
        nat_w = wpx / 3 / 96 * 2.54  # -s 3, 96 dpi
        w = min(CONTENT_W_CM, nat_w * 1.15)
    h = w * hpx / wpx
    if h > MAX_IMG_H_CM:
        w *= MAX_IMG_H_CM / h
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(2)
    p.add_run().add_picture(str(png), width=Cm(w))
    c = doc.add_paragraph(caption, style="Caption")
    return p


def add_page_break(doc):
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def add_toc(doc, entries, pages):
    """entries: list[(level, text)]; pages: dict text->page (opsional)."""
    h = doc.add_paragraph("Daftar Isi")
    set_run_font(h.runs[0], name=HEAD_FONT, size=16, bold=True, color=ACCENT)
    h.paragraph_format.space_after = Pt(10)
    n = len(entries)
    for i, (lvl, text) in enumerate(entries):
        p = doc.add_paragraph(style=f"toc {lvl}")
        if i == 0:
            for kind, txt in (("begin", None), ("instr", ' TOC \\o "1-2" \\h \\z \\u '), ("separate", None)):
                run = p.add_run()
                if kind == "instr":
                    it = OxmlElement("w:instrText"); it.set(qn("xml:space"), "preserve"); it.text = txt
                    run._r.append(it)
                else:
                    fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), kind); run._r.append(fc)
        p.add_run(text)
        pg = pages.get(text)
        p.add_run("\t" + (str(pg) if pg else ""))
        if i == n - 1:
            run = p.add_run()
            fc = OxmlElement("w:fldChar"); fc.set(qn("w:fldCharType"), "end"); run._r.append(fc)


# --------------------------------------------------------------------------- markdown parse

def parse_md(text: str):
    """Hasilkan daftar blok: (type, data)."""
    lines = text.splitlines()
    blocks = []
    i = 0
    skipping_toc = False
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if skipping_toc:
            if s == "---":
                skipping_toc = False
            i += 1
            continue
        if s == "<!-- pagebreak -->":
            blocks.append(("pb", None))
            i += 1
            continue
        if not s or s.startswith("<!--"):
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip()
            j = i + 1
            buf = []
            while j < len(lines) and not lines[j].strip().startswith("```"):
                buf.append(lines[j]); j += 1
            blocks.append(("code", (lang, "\n".join(buf) + "\n")))
            i = j + 1
            continue
        mi = re.match(r"^!\[(.*)\]\(([^)]*)\)(?:\{w=([\d.]+)\})?$", s)
        if mi:  # opsional: {w=13} = lebar gambar dalam cm
            blocks.append(("img", (mi.group(1), mi.group(2), float(mi.group(3)) if mi.group(3) else None)))
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if title == "Daftar Isi":
                skipping_toc = True
            else:
                blocks.append(("h", (level, title)))
            i += 1
            continue
        if s == "---":
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip()); i += 1
            parsed = []
            for r in rows:
                cells = [c.strip() for c in r.strip().strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    continue
                parsed.append(cells)
            blocks.append(("table", parsed))
            continue
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip()); i += 1
            blocks.append(("quote", " ".join(b for b in buf if b)))
            continue
        lm = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if lm:
            items = []
            while i < len(lines):
                ln2 = lines[i]
                lm2 = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln2)
                if lm2 and not ln2.strip().startswith("**"):
                    indent = len(lm2.group(1))
                    mk = lm2.group(2)
                    items.append([indent // 3, "•" if mk in "-*" else mk, lm2.group(3)])
                    i += 1
                elif ln2.strip() and ln2.startswith(" ") and items:
                    items[-1][2] += " " + ln2.strip(); i += 1
                else:
                    break
            blocks.append(("list", items))
            continue
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||>|```|---$|\s*([-*]|\d+\.)\s)", lines[i]):
            buf.append(lines[i].strip()); i += 1
        blocks.append(("p", " ".join(buf)))
    return blocks


def caption_from_heading(h):
    h = re.sub(r"^\d+(\.\d+)*\s+", "", h)
    h = re.sub(r"^Sequence diagram:\s*", "Sequence diagram ", h)
    return h


# --------------------------------------------------------------------------- build

SHOTS_SRC = HERE.parent / "webapp" / "screenshots"
SHOTS_OUT = HERE / "img"
# nama berkas -> potongan vertikal (y_atas, y_bawah) dalam piksel; beberapa potongan disambung ke bawah dengan jarak
# kosong. Bilah atas (nama aplikasi) selalu dipotong. Semua screenshot berasal dari web app mode LIVE (server sungguhan).
SHOT_CROPS = {
    "live-00-sandbox-utama.png": [(85, 900)],
    "live-03-teks-trace.png": [(85, 1525)],
    "live-04-struk-baru.png": [(85, 1235)],
    # bilah navigasi melayang pada tangkapan penuh menimpa bagian tengah halaman, maka hanya kartu saldo dan tabel
    "live-10-transaksi.png": [(195, 380), (765, 1585)],
    "live-01-sistem.png": [(85, 1215)],
    # versi ringkas: hanya kartu atas, latensi, dan model (tanpa daftar tools)
    "live-01-sistem-ringkas.png": [(85, 812)],
}
# nama berkas keluaran -> berkas sumber, bila berbeda
SHOT_SOURCE = {"live-01-sistem-ringkas.png": "live-01-sistem.png"}
# nama berkas -> kotak (x0, y0, x1, y1) pada koordinat berkas sumber yang diburamkan (path pengguna pada daftar proses GPU)
SHOT_BLUR = {
    "live-01-sistem.png": [(866, 676, 1350, 734)],
    "live-01-sistem-ringkas.png": [(866, 676, 1350, 734)],
}
SHOT_GAP = 22
SHOT_BG = (13, 15, 18)


def prep_screenshots() -> None:
    SHOTS_OUT.mkdir(exist_ok=True)
    for name, segs in SHOT_CROPS.items():
        src = SHOTS_SRC / SHOT_SOURCE.get(name, name)
        if not src.exists():
            continue
        with Image.open(src) as im:
            im = im.convert("RGB")
            for box in SHOT_BLUR.get(name, []):
                im.paste(im.crop(box).filter(ImageFilter.GaussianBlur(7)), box[:2])
            parts = [im.crop((0, y0, im.width, min(y1, im.height))) for y0, y1 in segs]
            out = Image.new("RGB", (im.width, sum(p.height for p in parts) + SHOT_GAP * (len(parts) - 1)), SHOT_BG)
            y = 0
            for part in parts:
                out.paste(part, (0, y))
                y += part.height + SHOT_GAP
            out.save(SHOTS_OUT / name, optimize=True)


def is_compact() -> bool:
    """Baris `<!-- build: compact -->` di Markdown: tanpa halaman sampul dan daftar isi."""
    return "<!-- build: compact -->" in SRC.read_text(encoding="utf8")


def apply_typography() -> None:
    """Baris opsional `<!-- build: body=11 table=9 spacing=1.08 -->` di Markdown mengganti ukuran huruf/spasi."""
    global BODY_PT, TABLE_PT, LINE_SPACING
    m = re.search(r"<!-- build:([^>]*?)-->", SRC.read_text(encoding="utf8"))
    for kv in (m.group(1).split() if m else []):
        k, _, v = kv.partition("=")
        if k == "body":
            BODY_PT = float(v)
        elif k == "table":
            TABLE_PT = float(v)
        elif k == "spacing":
            LINE_SPACING = float(v)


def build(pages: dict[str, int]) -> list[tuple[int, str]]:
    apply_typography()
    blocks = parse_md(SRC.read_text(encoding="utf8"))
    doc = setup_document()
    compact = is_compact()
    if compact:
        doc.sections[0].different_first_page_header_footer = False
        tpf = doc.styles["Title"].paragraph_format
        tpf.space_before, tpf.space_after = Pt(0), Pt(6)
        doc.styles["Title"].font.size = Pt(17)
        doc.styles["Heading 1"].paragraph_format.space_before = Pt(10)
        doc.styles["Heading 1"].font.size = Pt(14)
        doc.styles["Heading 2"].paragraph_format.space_before = Pt(8)
    toc_entries = [(1 if lv == 2 else 2, t) for k, d in blocks if k == "h" for lv, t in [d] if lv in (2, 3)]
    fig = 0
    last_heading = ""
    cover_done = False
    first_table_done = False
    for idx, (kind, data) in enumerate(blocks):
        if kind == "h":
            level, title = data
            if level == 1:
                doc.add_paragraph(title, style="Title")
                continue
            if not cover_done and not compact:
                cover_done = True
                add_page_break(doc)
                add_toc(doc, toc_entries, pages)
                add_page_break(doc)
            last_heading = title
            doc.add_heading(title, level=level - 1)
        elif kind == "p":
            pp = para(doc, data)
            nxt = blocks[idx + 1][0] if idx + 1 < len(blocks) else ""
            if len(data) < 90 and nxt in ("table", "list"):
                pp.paragraph_format.keep_with_next = True
        elif kind == "quote":
            add_quote(doc, data)
        elif kind == "pb":
            add_page_break(doc)
        elif kind == "list":
            for lvl, mk, txt in data:
                add_list_item(doc, mk, txt, lvl)
            doc.paragraphs[-1].paragraph_format.space_after = Pt(8)
        elif kind == "table":
            header_empty = all(not c for c in data[0])
            add_table(doc, data, header_empty)
            first_table_done = True
        elif kind == "img":
            cap, rel, wcm = data
            fig += 1
            add_picture(doc, (HERE / rel).resolve(), f"Gambar {fig}. {cap}", screenshot=True, width_cm=wcm)
        elif kind == "code":
            lang, code = data
            if lang == "mermaid":
                fig += 1
                png = render_mermaid(code)
                add_picture(doc, png, f"Gambar {fig}. {caption_from_heading(last_heading)}")
            else:
                p = doc.add_paragraph()
                r = p.add_run(code.rstrip())
                set_run_font(r, name=CODE_FONT, size=9.5)
    if not cover_done:  # dokumen tanpa heading selain judul
        pass
    # footer sampul kosong, footer lain sudah di section.footer
    doc.core_properties.title = next((d[1] for k, d in blocks if k == "h" and d[0] == 1), "Dokumen Use Case")
    doc.core_properties.author = "Teuku Laksamana Andrian Rahman; Axel David"
    doc.core_properties.language = "id-ID"
    doc.save(OUT)
    return toc_entries


# --------------------------------------------------------------------------- pass 2: nomor halaman

def find_soffice() -> str | None:
    for c in SOFFICE_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def to_pdf(docx: Path, outdir: Path) -> Path | None:
    so = find_soffice()
    if not so:
        return None
    prof = Path(tempfile.mkdtemp(prefix="lo_prof_"))
    cmd = [so, f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "pdf", "--outdir", str(outdir), str(docx)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    shutil.rmtree(prof, ignore_errors=True)
    pdf = outdir / (docx.stem + ".pdf")
    return pdf if pdf.exists() else None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def compute_pages(pdf: Path, entries, anchor: str) -> dict[str, int]:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    raw_pages = out.split("\f")
    pages = [norm(p) for p in raw_pages]
    start = next((i for i, p in enumerate(pages) if norm(anchor) in p), None)
    if start is None:
        return {}
    line_pages = [[norm(l) for l in p.splitlines() if l.strip()] for p in raw_pages]
    res, cur = {}, start
    for _, text in entries:
        key = norm(text)
        for j in range(cur, len(pages)):
            if key in line_pages[j]:  # judul harus berdiri sendiri pada satu baris
                res[text] = j + 1
                cur = j
                break
    return res


def main():
    prep_screenshots()
    pass2 = "--no-pdf-pass" not in sys.argv and not is_compact()
    entries = build({})
    if pass2 and find_soffice():
        blocks = parse_md(SRC.read_text(encoding="utf8"))
        # jangkar: awal paragraf pertama setelah heading bagian pertama
        anchor = ""
        seen_h = False
        for k, d in blocks:
            if k == "h" and d[0] == 2:
                seen_h = True
            elif seen_h and k == "p":
                anchor = re.sub(r"[`*]", "", d)[:40]
                break
        tmp = Path(tempfile.mkdtemp(prefix="docx_pass_"))
        pdf = to_pdf(OUT, tmp)
        if pdf and anchor:
            pages = compute_pages(pdf, entries, anchor)
            if pages:
                build(pages)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"OK: {OUT}")


if __name__ == "__main__":
    main()
