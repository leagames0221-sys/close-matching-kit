"""Guards applied to every uploaded file before it is parsed."""
from __future__ import annotations

import io
import re
import zipfile
from typing import BinaryIO

from . import config


class FileRejected(Exception):
    """The file must not be read. The message is shown to the user."""


def read_limited(stream: BinaryIO, limit: int, label: str) -> bytes:
    """Read at most `limit` bytes; reject as soon as the limit is exceeded (not after reading it all)."""
    buf = io.BytesIO()
    total = 0
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FileRejected(f"{label}が大きすぎる（上限 {limit // (1024 * 1024)}MB）")
        buf.write(chunk)
    return buf.getvalue()


_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def check_size(data: bytes, limit: int, label: str) -> None:
    if len(data) > limit:
        raise FileRejected(f"{label}が大きすぎる（上限 {limit // (1024 * 1024)}MB）")


def check_xlsx(data: bytes, filename: str = "") -> None:
    """Reject macro books and zip bombs before openpyxl sees the file. Uses the zip directory only."""
    check_size(data, config.LIMIT_XLSX, "xlsx")
    if filename.lower().endswith(".xlsm"):
        raise FileRejected("マクロ付きのブック（.xlsm）は読み込まない")
    if not filename.lower().endswith(".xlsx") and filename:
        raise FileRejected("拡張子が .xlsx ではない")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise FileRejected("xlsx（zip）として読めない") from None
    infos = zf.infolist()
    if len(infos) > config.LIMIT_XLSX_ENTRIES:
        raise FileRejected(f"zip の中のファイル数が多すぎる（上限 {config.LIMIT_XLSX_ENTRIES}）")
    total = sum(i.file_size for i in infos)
    if total > config.LIMIT_XLSX_UNZIPPED:
        raise FileRejected(
            f"zip を展開した合計が大きすぎる（{total // (1024 * 1024)}MB、上限 "
            f"{config.LIMIT_XLSX_UNZIPPED // (1024 * 1024)}MB）")
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names:
        raise FileRejected("xlsx として読めない（[Content_Types].xml が無い）")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise FileRejected("マクロ付きのブックは読み込まない")
    ct = zf.read("[Content_Types].xml")
    if b"macroEnabled" in ct:
        raise FileRejected("マクロ付きのブックは読み込まない")
    # the parts of an xlsx are XML: the same DOCTYPE / ENTITY refusal as for XML files, before openpyxl parses them
    # (the unzipped total is already bounded above, and zipfile stops at each part's declared size)
    for info in infos:
        if info.filename.lower().endswith((".xml", ".rels", ".vml")) and _DTD_RE.search(zf.read(info)):
            raise FileRejected(f"xlsx の中の {info.filename} に DOCTYPE または ENTITY が含まれているため拒否")


def check_xml(data: bytes) -> None:
    """Reject DOCTYPE / ENTITY before any XML parser sees the bytes (entity expansion attacks)."""
    check_size(data, config.LIMIT_XML, "XML")
    head = data[:4]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")) or head[:1] == b"\x00" or head[1:2] == b"\x00":
        raise FileRejected("XML の文字コードは UTF-8 だけ受ける")
    if _DTD_RE.search(data):
        raise FileRejected("XML に DOCTYPE または ENTITY が含まれているため拒否")
    m = re.match(rb"\s*(?:\xef\xbb\xbf)?<\?xml[^>]*encoding\s*=\s*[\"']([^\"']+)[\"']", data)
    if m and m.group(1).lower() not in (b"utf-8", b"utf8"):
        raise FileRejected("XML の文字コードは UTF-8 だけ受ける")


def check_text_file(data: bytes) -> None:
    check_size(data, config.LIMIT_FIXED_OR_CSV, "ファイル")
