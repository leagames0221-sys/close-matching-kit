"""CSV reader. Columns come from the data source sheet. A balance column is checked only when it is mapped."""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from .base import ParsedBundle, ParsedRow, ParseError, ParseResult, parse_amount, parse_date_text, to_minor


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ParseError("文字コードは UTF-8 か Shift_JIS（cp932）だけ受ける")


def parse(data: bytes, src: dict) -> ParseResult:
    cols = src["csv"]
    text = _decode(data)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ParseError("ファイルが空") from None
    header = [h.strip().lstrip("﻿") for h in header]
    idx = {h: i for i, h in enumerate(header)}
    need = {"日付": cols["date"], "金額": cols["amount"]}
    for label, name in (("参照", cols["reference"]), ("摘要", cols["memo"]), ("残高", cols["balance"])):
        if name:
            need[label] = name
    for key in src["row_key_cols"]:
        need[f"行のキー {key}"] = key
    missing = [f"{lab}（列名 {name!r}）" for lab, name in need.items() if name not in idx]
    if missing:
        raise ParseError("CSV の見出しに無い列: " + "、".join(missing))
    multiplier = int(src["multiplier"])

    bundle = ParsedBundle(account=src["account"], date_from=None, date_to=None, label="CSV")
    prev_balance: Decimal | None = None
    first_open: int | None = None
    last_close: int | None = None
    line_no = 1
    for rec in reader:
        line_no += 1
        if not rec or all(not c.strip() for c in rec):
            continue
        if len(rec) < len(header):
            bundle.errors.append(f"{line_no} 行目: 列の数が見出しより少ない")
            continue

        def cell(name: str) -> str:
            return rec[idx[name]].strip()

        try:
            d = parse_date_text(cell(cols["date"]))
        except (ValueError, TypeError) as e:
            bundle.errors.append(f"{line_no} 行目: 日付が読めない（{e}）")
            continue
        try:
            amt = parse_amount(cell(cols["amount"]))
            minor = to_minor(amt, multiplier)
        except ValueError as e:
            bundle.errors.append(f"{line_no} 行目: 金額が読めない（{e}）")
            continue
        bal_minor: int | None = None
        if cols["balance"]:
            try:
                bal = parse_amount(cell(cols["balance"]))
                bal_minor = to_minor(bal, multiplier)
            except ValueError as e:
                bundle.errors.append(f"{line_no} 行目: 残高が読めない（{e}）")
                continue
            if first_open is None:
                first_open = bal_minor - minor  # the balance before the first row
            last_close = bal_minor
            if prev_balance is not None and prev_balance + amt != bal:
                bundle.errors.append(
                    f"{line_no} 行目: 残高が合わない: 前の行の残高 {prev_balance} + 金額 {amt} = "
                    f"{prev_balance + amt}、この行の残高は {bal}")
            prev_balance = bal
        key = "|".join(cell(k) for k in src["row_key_cols"]) if src["row_key_cols"] else None
        if key is not None and not key.replace("|", ""):
            bundle.errors.append(f"{line_no} 行目: 行のキーの列が空")
            continue
        bundle.rows.append(ParsedRow(
            booking_date=d.isoformat(), amount_minor=minor,
            reference=cell(cols["reference"]) if cols["reference"] else "",
            memo=cell(cols["memo"]) if cols["memo"] else "", src_row=line_no, row_key=key,
            ext_id=key if key else str(line_no), balance=bal_minor))
    if not bundle.rows and not bundle.errors:
        raise ParseError("明細の行が 1 行も無い")
    if bundle.rows:
        ds = [r.booking_date for r in bundle.rows]
        bundle.date_from, bundle.date_to = min(ds), max(ds)
    keyed = [r.row_key for r in bundle.rows if r.row_key]
    if len(set(keyed)) != len(keyed):
        bundle.errors.append("ファイルの中に同じ行のキーが 2 回ある")
    if cols["balance"] and bundle.rows:
        bundle.opening, bundle.closing = first_open, last_close
    bundle.note = "残高の列で検算した" if cols["balance"] else "残高の列が無いので検算していない"
    return ParseResult(bundles=[bundle])
