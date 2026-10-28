"""Builders for sample / test inputs (workbook, zengin fixed-length file, camt.052 XML, CSV).

All values are made up. Nothing here is copied from a bank's sample. Used by `tools/make_samples.py`
and by the tests.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook

from close_kit.formats import zengin_layout as zl

# ----------------------------------------------------------------------------------------------
# workbook

HEADERS = {
    "説明": ["schemaの版", "会社名", "作成者", "更新日", "会社コード", "仕訳の種類", "期日の日数の下限", "期日の日数の上限"],
    "会計期間": ["期間の名前", "開始日", "終了日", "締め"],
    "勘定科目": ["勘定コード", "名前", "区分"],
    "データ源": ["名前", "形式", "口座", "向き", "最小単位の倍率", "日付の暦", "行のキーの列",
               "CSV日付列", "CSV金額列", "CSV参照列", "CSV摘要列", "CSV残高列"],
    "照合セット": ["名前", "左のデータ源", "右のデータ源", "有効", "銀行の側", "預金の勘定", "残高一致に使う"],
    "表示項目": ["照合セット", "項目", "順番"],
    "小計項目": ["照合セット", "項目"],
    "パスルール": ["照合セット", "順番", "名前", "種別", "型", "一致させる項目", "date_window",
               "amount_tolerance_minor", "左の束ねるキー", "右の束ねるキー", "束どうしを組にするキー",
               "1束の行の上限", "有効", "差額の相手勘定"],
    "理由コード": ["コード", "名前", "処理", "相手勘定", "有効"],
    "承認ルート": ["下限", "上限", "承認できる人"],
    "作業一覧": ["作業ID", "名前", "種類", "対象の期間", "数え方", "期末からの日数", "前の作業", "作成者", "確認者",
             "完了の条件", "条件の照合セット", "有効"],
    "会社の休日": ["日付", "名前"],
}


def default_sheets() -> dict[str, list[list[Any]]]:
    """Rows (without the header row) of a valid sample workbook. Company and accounts are made up."""
    return {
        "説明": [["2", "サンプル商事株式会社（架空）", "サンプル担当", date(2026, 10, 24), "1000", "SA"]],
        "会計期間": [["2026-09", date(2026, 9, 1), date(2026, 9, 30), "はい"],
                 ["2026-10", date(2026, 10, 1), date(2026, 10, 31), "いいえ"],
                 ["2026-11", date(2026, 11, 1), date(2026, 11, 30), "いいえ"]],
        "勘定科目": [["1100", "普通預金", "資産"], ["1200", "売掛金", "資産"],
                 ["6100", "支払手数料", "費用"], ["4100", "雑収入", "収益"]],
        "データ源": [
            ["帳簿", "csv", "", "帳簿の借方を + にする", 1, "西暦", "取引ID", "日付", "金額", "参照", "摘要", ""],
            ["銀行", "zengin", "1234567", "入金を + にする", 1, "令和", "", "", "", "", "", ""],
        ],
        "照合セット": [["預金照合", "帳簿", "銀行", "はい", "右", "1100", "いいえ"]],
        "表示項目": [["預金照合", "勘定日", 1], ["預金照合", "金額", 2], ["預金照合", "参照", 3],
                 ["預金照合", "摘要", 4]],
        "小計項目": [["預金照合", "金額"]],
        "パスルール": [
            ["預金照合", 10, "参照が同じ", "自動", "1対1", "参照=参照", 3, 100, "", "", "", "", "はい"],
            ["預金照合", 20, "金額と日付が同じ", "自動", "1対1", "金額=金額; 勘定日=勘定日", 2, 0, "", "", "",
             "", "はい"],
            ["預金照合", 30, "金額が同じ（日付は 7 日以内）", "候補", "1対1", "金額=金額", 7, 0, "", "", "", "", "はい"],
            ["預金照合", 40, "請求書ごとの合算", "自動", "多対1", "", 5, 0, "参照", "", "参照", 10, "はい"],
        ],
        "理由コード": [["R01", "振込手数料", "仕訳", "6100", "はい"], ["R02", "端数差", "仕訳", "4100", "はい"],
                   ["R03", "受取利息", "仕訳", "4100", "はい"], ["R04", "未達の入金", "調整項目", "", "はい"],
                   ["R05", "未取付の出金", "調整項目", "", "はい"]],
        "承認ルート": [[0, 1000000, "approver1"], [1000000, "", "approver2"]],
        "作業一覧": [],
        "会社の休日": [],
    }


def build_workbook(sheets: dict[str, list[list[Any]]] | None = None, headers: dict[str, list[str]] | None = None,
                   drop_sheets: tuple[str, ...] = (), formulas: dict[tuple[str, str], str] | None = None) -> bytes:
    data = default_sheets()
    if sheets:
        data.update(sheets)
    hdr = dict(HEADERS)
    if headers:
        hdr.update(headers)
    wb = Workbook()
    wb.remove(wb.active)
    for name in HEADERS:
        if name in drop_sheets:
            continue
        ws = wb.create_sheet(name)
        ws.append(hdr[name])
        for row in data[name]:
            ws.append(row)
    for (sheet, cell), f in (formulas or {}).items():
        wb[sheet][cell] = f
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ----------------------------------------------------------------------------------------------
# zengin (200 bytes per record, cp932)

def _pack(layout: list[tuple[str, str, int]], values: dict[str, Any]) -> bytes:
    out = b""
    for name, typ, width in layout:
        v = values.get(name)
        if typ == "N" and v == " ":
            raw = b" " * width            # the manual says "all spaces" for the inquiry number
        elif typ == "N":
            raw = str(int(v or 0)).zfill(width).encode("ascii")
        else:
            raw = (v or "").encode("cp932")
            if len(raw) > width:
                raise ValueError(f"{name} が {width} バイトを超える")
            raw = raw.ljust(width, b" ")
        assert len(raw) == width, (name, len(raw), width)
        out += raw
    assert len(out) == zl.RECORD_LENGTH
    return out


def zengin_date(d: date, calendar: str) -> int:
    yy = d.year - 2018 if calendar == "令和" else d.year - 2000
    return yy * 10000 + d.month * 100 + d.day


def build_zengin(entries: list[tuple[date, str, int, str, str, str]], *, account: str = "1234567",
                 d_from: date, d_to: date, opening: int, calendar: str = "令和", crlf: bool = False,
                 closing_override: int | None = None, trailer_count_override: int | None = None,
                 end_total_override: int | None = None, overdraft_open: bool = False,
                 extra_blocks: list[bytes] | None = None) -> bytes:
    """entries: (date, 'in'|'out', amount, payer, summary, edi). Balances are computed so the file is consistent
    unless an override is given."""
    recs: list[bytes] = []
    # Bank code 5, branch code 1, the names ｻﾝﾌﾟﾙｷﾞﾝｺｳ / ﾎﾝﾃﾝ / ｻﾝﾌﾟﾙｼｮｳｼﾞ(ｶ and the account 1234567 are made up. The codes
    # were not checked against the real list of bank and branch codes (a real bank may hold the same numbers); the
    # manual's own example code for its bank (0288) is not used.
    header = _pack(zl.HEADER, {
        "データ区分": 1, "種別コード": 3, "コード区分": 0, "作成日": zengin_date(d_to, calendar),
        "勘定日（自）": zengin_date(d_from, calendar), "勘定日（至）": zengin_date(d_to, calendar),
        "銀行コード": 5, "銀行名": "ｻﾝﾌﾟﾙｷﾞﾝｺｳ", "支店コード": 1, "支店名": "ﾎﾝﾃﾝ", "預金種目": 1,
        "口座番号": int(account), "口座名": "ｻﾝﾌﾟﾙｼｮｳｼﾞ(ｶ", "貸越区分": 2 if overdraft_open else 1,
        "通帳・証書区分": 1, "取引前残高": opening})
    recs.append(header)
    bal = -opening if overdraft_open else opening
    n_in = n_out = s_in = s_out = 0
    for i, (d, io_, amt, payer, summary, edi) in enumerate(entries, start=1):
        if io_ == "in":
            bal += amt
            n_in += 1
            s_in += amt
        else:
            bal -= amt
            n_out += 1
            s_out += amt
        recs.append(_pack(zl.DATA, {
            "データ区分": 2, "照会番号": " ", "勘定日": zengin_date(d, calendar),
            "預入・払出日": zengin_date(d, calendar), "入払区分": 1 if io_ == "in" else 2,
            "取引区分": 11, "取引金額": amt, "振込依頼人名または契約者番号": payer, "摘要内容": summary,
            "EDI情報": edi}))
    closing = closing_override if closing_override is not None else bal
    recs.append(_pack(zl.TRAILER, {
        "データ区分": 8, "入金件数": n_in, "入金額合計": s_in, "出金件数": n_out, "出金額合計": s_out,
        "貸越区分": 2 if closing < 0 else 1, "取引後残高": abs(closing),
        "データ・レコード件数": trailer_count_override if trailer_count_override is not None else len(entries)}))
    blocks = len(extra_blocks or []) + 1
    body = recs + [r for blk in (extra_blocks or []) for r in [blk[i:i + 200] for i in range(0, len(blk), 200)]]
    total = len(body) + 1
    body.append(_pack(zl.END, {"データ区分": 9,
                               "レコード総件数": end_total_override if end_total_override is not None else total,
                               "口座数": blocks}))
    sep = b"\r\n" if crlf else b""
    return sep.join(body)


# ----------------------------------------------------------------------------------------------
# camt.052.001.02

CAMT_NS = "urn:iso:std:iso:20022:tech:xsd:camt.052.001.02"


def build_camt(entries: list[tuple[date, str, int, str, str, str]], *, account: str = "1234567",
               d_from: date, d_to: date, opening: int, closing_override: int | None = None,
               opening_ind: str = "CRDT", zero_balances: bool = False, nb_override: int | None = None) -> bytes:
    """entries: (date, 'CRDT'|'DBIT', amount, payer, summary, edi). Ref is DD + 6 digit running number."""
    bal = opening if opening_ind == "CRDT" else -opening
    s_in = s_out = n_in = n_out = 0
    ntries = []
    for i, (d, ind, amt, payer, summary, edi) in enumerate(entries, start=1):
        if ind == "CRDT":
            bal += amt
            s_in += amt
            n_in += 1
        else:
            bal -= amt
            s_out += amt
            n_out += 1
        ref = f"{d.day:02d}{i:06d}"
        ntries.append(
            f"<Ntry><Amt Ccy=\"JPY\">{amt}</Amt><CdtDbtInd>{ind}</CdtDbtInd><Sts>BOOK</Sts>"
            f"<BookgDt><Dt>{d.isoformat()}</Dt></BookgDt><ValDt><Dt>{d.isoformat()}</Dt></ValDt>"
            f"<BkTxCd><Prtry><Cd>11</Cd></Prtry></BkTxCd><NtryDtls><TxDtls><Refs>"
            f"<Prtry><Tp>Reference/Identification Number</Tp><Ref>{ref}</Ref></Prtry></Refs>"
            f"<RltdPties><Dbtr><Nm>{escape(payer)}</Nm></Dbtr></RltdPties>"
            + (f"<RltdRmtInf><RmtId>{escape(edi)}</RmtId></RltdRmtInf>" if edi else "")
            + f"</TxDtls></NtryDtls><AddtlNtryInf>{escape(summary)}</AddtlNtryInf></Ntry>")
    closing = closing_override if closing_override is not None else bal
    if zero_balances:
        opening_val, closing_val, closing_ind = 0, 0, "CRDT"
    else:
        opening_val, closing_val = opening, abs(closing)
        closing_ind = "DBIT" if closing < 0 else "CRDT"
    nb = nb_override if nb_override is not None else len(entries)
    xml = (
        f"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Document xmlns=\"{CAMT_NS}\">"
        "<BkToCstmrAcctRpt><GrpHdr><MsgId>000000000000000000001</MsgId><CreDtTm>2026-10-31T00:00:00</CreDtTm></GrpHdr>"
        f"<Rpt><Id>000001</Id><CreDtTm>{d_from.isoformat()}T00:00:00</CreDtTm>"
        f"<FrToDt><FrDtTm>{d_from.isoformat()}T00:00:00</FrDtTm><ToDtTm>{d_to.isoformat()}T00:00:00</ToDtTm></FrToDt>"
        f"<Acct><Id><Othr><Id>{account}</Id></Othr></Id></Acct>"
        f"<Bal><Tp><CdOrPrtry><Cd>OPAV</Cd></CdOrPrtry></Tp><Amt Ccy=\"JPY\">{opening_val}</Amt>"
        f"<CdtDbtInd>{opening_ind}</CdtDbtInd><Dt><Dt>{d_from.isoformat()}</Dt></Dt></Bal>"
        f"<Bal><Tp><CdOrPrtry><Cd>CLAV</Cd></CdOrPrtry></Tp><Amt Ccy=\"JPY\">{closing_val}</Amt>"
        f"<CdtDbtInd>{closing_ind}</CdtDbtInd><Dt><Dt>{d_to.isoformat()}</Dt></Dt></Bal>"
        f"<TxsSummry><TtlNtries><NbOfNtries>{nb}</NbOfNtries></TtlNtries>"
        f"<TtlCdtNtries><NbOfNtries>{n_in}</NbOfNtries><Sum>{s_in}</Sum></TtlCdtNtries>"
        f"<TtlDbtNtries><NbOfNtries>{n_out}</NbOfNtries><Sum>{s_out}</Sum></TtlDbtNtries></TxsSummry>"
        + "".join(ntries) + "</Rpt></BkToCstmrAcctRpt></Document>")
    return xml.encode("utf-8")


# ----------------------------------------------------------------------------------------------
# csv

def build_csv(header: list[str], rows: list[list[Any]], encoding: str = "utf-8") -> bytes:
    def q(v: Any) -> str:
        s = str(v)
        return '"' + s.replace('"', '""') + '"' if any(c in s for c in ',"\n') else s
    text = "\r\n".join(",".join(q(c) for c in r) for r in [header, *rows]) + "\r\n"
    return text.encode(encoding)


def D(s: str) -> Decimal:
    return Decimal(s)
