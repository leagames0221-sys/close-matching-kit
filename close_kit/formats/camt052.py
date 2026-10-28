"""Reader for the camt.052.001.02 account report (XML) as written by the MUFG Bank statement download.

Element names follow the bank's field table. The XML guard (DOCTYPE / ENTITY / encoding) runs before
the parser sees the bytes. Each <Rpt> is checked on its own: opening balance (OPAV) + credits - debits
must equal closing balance (CLAV), and the entry count and the credit / debit totals must agree with
the summary. A report with a zero balance on both ends and entries is refused: the bank's manual says
balances come out as 0 for users whose output is restricted, so the check cannot be done.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation

from .. import safefile
from .base import ParsedBundle, ParsedRow, ParseError, ParseResult, norm_account, to_minor

NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.02"


def _q(*names: str) -> str:
    return "/".join(f"{{{NS}}}{n}" for n in names)


def _text(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path)
    return (found.text or "").strip() if found is not None else ""


def _iso_date(text: str, what: str) -> date:
    t = text.strip()[:10]
    try:
        return date.fromisoformat(t)
    except ValueError:
        raise ParseError(f"{what} が日付として読めない: {text!r}") from None


def _amount(text: str, what: str) -> Decimal:
    try:
        d = Decimal(text.strip())
    except InvalidOperation:
        raise ParseError(f"{what} が数値として読めない: {text!r}") from None
    if not d.is_finite() or d < 0:
        raise ParseError(f"{what} が 0 以上の数値ではない: {text!r}")
    return d


def parse(data: bytes, src: dict) -> ParseResult:
    safefile.check_xml(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ParseError(f"XML として読めない: {e}") from None
    if root.tag != f"{{{NS}}}Document":
        raise ParseError(f"ルートが camt.052.001.02 の Document ではない（{root.tag}）")
    multiplier = int(src["multiplier"])
    want_account = norm_account(src["account"])
    rpts = root.findall(_q("BkToCstmrAcctRpt", "Rpt"))
    if not rpts:
        raise ParseError("入出金取引明細情報（Rpt）が 1 つも無い")

    result = ParseResult()
    for idx, rpt in enumerate(rpts, start=1):
        account = norm_account(_text(rpt, _q("Acct", "Id", "Othr", "Id")))
        if account != want_account:
            result.skipped.append(f"口座 {account}（データ源の口座 {want_account} ではない）")
            continue
        d_from = _iso_date(_text(rpt, _q("FrToDt", "FrDtTm")), "勘定日（自）")
        d_to = _iso_date(_text(rpt, _q("FrToDt", "ToDtTm")), "勘定日（至）")
        bundle = ParsedBundle(account=account, date_from=d_from.isoformat(), date_to=d_to.isoformat(),
                              label=f"口座 {account}（Rpt {idx}）")

        balances: dict[str, Decimal] = {}
        for bal in rpt.findall(_q("Bal")):
            code = _text(bal, _q("Tp", "CdOrPrtry", "Cd"))
            amt = _amount(_text(bal, _q("Amt")), f"残高 {code}")
            ind = _text(bal, _q("CdtDbtInd"))
            if ind not in ("CRDT", "DBIT"):
                raise ParseError(f"残高 {code} の貸越区分 {ind!r} は CRDT か DBIT")
            balances[code] = -amt if ind == "DBIT" else amt
        if "OPAV" not in balances or "CLAV" not in balances:
            raise ParseError(f"{bundle.label}: 残高の種類 OPAV / CLAV が揃っていない")
        opening, closing = balances["OPAV"], balances["CLAV"]

        entries = rpt.findall(_q("Ntry"))
        declared = _text(rpt, _q("TxsSummry", "TtlNtries", "NbOfNtries"))
        total_in = total_out = Decimal(0)
        n_in = n_out = 0
        for k, ntry in enumerate(entries, start=1):
            amt = _amount(_text(ntry, _q("Amt")), f"取引金額（{k} 件目）")
            ind = _text(ntry, _q("CdtDbtInd"))
            if ind == "CRDT":
                total_in += amt
                n_in += 1
                sign = 1
            elif ind == "DBIT":
                total_out += amt
                n_out += 1
                sign = -1
            else:
                raise ParseError(f"{bundle.label}: {k} 件目の入払区分 {ind!r} は CRDT か DBIT")
            if _text(ntry, _q("RvslInd")).lower() == "true":
                bundle.errors.append(
                    f"{k} 件目は訂正取引（RvslInd）。符号の扱いが説明書から決められないので取り込まない")
            if _text(ntry, _q("Sts")) not in ("BOOK", ""):
                bundle.errors.append(f"{k} 件目の取引ステータス {_text(ntry, _q('Sts'))!r} は BOOK ではない")
            booking = _iso_date(_text(ntry, _q("BookgDt", "Dt")), f"勘定日（{k} 件目）")
            if not (d_from <= booking <= d_to):
                bundle.errors.append(
                    f"{k} 件目の勘定日 {booking} がレポートの勘定日の範囲 {d_from}〜{d_to} の外")
            ref8 = _text(ntry, _q("NtryDtls", "TxDtls", "Refs", "Prtry", "Ref"))
            if not ref8:
                bundle.errors.append(f"{k} 件目に照会番号（Prtry/Ref）が無い。行のキーが作れない")
            edi = _text(ntry, _q("NtryDtls", "TxDtls", "RltdRmtInf", "RmtId"))
            e2e = _text(ntry, _q("NtryDtls", "TxDtls", "Refs", "EndToEndId"))
            payer = _text(ntry, _q("NtryDtls", "TxDtls", "RltdPties", "Dbtr", "Nm"))
            summary = _text(ntry, _q("AddtlNtryInf"))
            memo = " ".join(x for x in (payer, summary) if x)
            bundle.rows.append(ParsedRow(
                booking_date=booking.isoformat(),
                amount_minor=sign * to_minor(amt, multiplier),
                reference=edi or e2e, memo=memo, src_row=k,
                row_key=f"{account}|{booking.isoformat()}|{ref8}" if ref8 else None,
                ext_id=f"{booking.isoformat()}|{ref8}"))

        # ---- checks
        if entries and opening == 0 and closing == 0:
            bundle.errors.append(
                "検算できない: 取引前・取引後の残高がどちらも 0 で明細がある"
                "（出力が制限された利用者の取得の可能性。説明書は、制限された利用者の取得では残高が 0 で出ると書いている）")
        else:
            expected = opening + total_in - total_out
            if expected != closing:
                bundle.errors.append(
                    f"残高が合わない: 取引前残高 {opening} + 入金 {total_in} − 出金 {total_out} = {expected}"
                    f"、取引後残高は {closing}（差 {closing - expected}）")
        if declared.isdigit() and int(declared) != len(entries):
            bundle.errors.append(f"データ・レコード件数が合わない: 明細 {len(entries)} 件、サマリ {declared} 件")
        for tag, n, tot, label in (("TtlCdtNtries", n_in, total_in, "入金"),
                                   ("TtlDbtNtries", n_out, total_out, "出金")):
            dn = _text(rpt, _q("TxsSummry", tag, "NbOfNtries"))
            ds = _text(rpt, _q("TxsSummry", tag, "Sum"))
            if dn.isdigit() and int(dn) != n:
                bundle.errors.append(f"{label}件数が合わない: 明細 {n} 件、サマリ {dn} 件")
            if ds:
                try:
                    if Decimal(ds) != tot:
                        bundle.errors.append(f"{label}額合計が合わない: 明細 {tot}、サマリ {ds}")
                except InvalidOperation:
                    bundle.errors.append(f"{label}額合計が数値ではない: {ds!r}")
        bundle.opening = to_minor(opening, multiplier)
        bundle.closing = to_minor(closing, multiplier)
        bundle.note = (f"残高 {opening} + 入金 {total_in} − 出金 {total_out}"
                       f" = {opening + total_in - total_out}（取引後 {closing}）、入金 {n_in} 件・出金 {n_out} 件")
        result.bundles.append(bundle)

    if not result.bundles:
        raise ParseError("データ源の口座に当たる Rpt が無い（" + "、".join(result.skipped) + "）")
    return result
