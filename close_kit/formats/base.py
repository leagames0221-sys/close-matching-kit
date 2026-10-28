"""Shared types for statement readers. Amounts are integers in the smallest unit (never floats)."""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation


class ParseError(Exception):
    """The whole file cannot be read (wrong structure, wrong encoding, forbidden content)."""


@dataclass
class ParsedRow:
    booking_date: str          # ISO yyyy-mm-dd
    amount_minor: int          # + = money in, - = money out (after the data source's direction rule)
    reference: str
    memo: str
    src_row: int               # row / record number in the original file (1-based)
    row_key: str | None = None
    ext_id: str = ""
    balance: int | None = None  # the row's own balance column (CSV only), in minor units


@dataclass
class ParsedBundle:
    """One unit that is accepted or refused as a whole: a CSV file, a zengin account block, a camt Rpt."""
    account: str
    date_from: str | None
    date_to: str | None
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # non-empty -> the bundle is not imported
    note: str = ""                                    # what was checked (balances etc.)
    label: str = ""
    opening: int | None = None                        # balance before the first row, minor units (None = not known)
    closing: int | None = None                        # balance after the last row


@dataclass
class ParseResult:
    bundles: list[ParsedBundle] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # blocks that belong to other accounts


def norm_account(text: str) -> str:
    t = text.strip()
    t2 = t.lstrip("0")
    return t2 if t2 else ("0" if t else "")


def to_minor(value: Decimal, multiplier: int) -> int:
    scaled = value * multiplier
    if scaled != scaled.to_integral_value():
        raise ValueError(f"最小単位に直すと端数が出る: {value} x {multiplier}")
    return int(scaled)


def parse_amount(text: str) -> Decimal:
    """'1,234.50' / '(320.00)' / '-5' / '▲5' / '¥1,000' -> Decimal. Raises ValueError."""
    s = unicodedata.normalize("NFKC", text).strip()
    if not s:
        raise ValueError("空")
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    if s[:1] in ("▲", "△"):
        neg, s = True, s[1:]
    s = s.replace(",", "").replace("¥", "").replace("円", "").replace("$", "").strip()
    if s.startswith("-"):
        neg, s = (not neg), s[1:]
    elif s.startswith("+"):
        s = s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"数値ではない: {text!r}") from None
    if not d.is_finite():
        raise ValueError(f"数値ではない: {text!r}")
    return -d if neg else d


def parse_date_text(text: str) -> date:
    s = unicodedata.normalize("NFKC", text).strip()
    s = s.split(" ")[0].split("T")[0]
    for sep in ("-", "/", "."):
        if sep in s:
            y, m, d = s.split(sep)
            return date(int(y), int(m), int(d))
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:]))
    raise ValueError(f"日付ではない: {text!r}")
