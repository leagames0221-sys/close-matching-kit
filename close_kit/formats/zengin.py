"""Reader for the 全銀協 deposit/withdrawal statement (fixed length, 200 bytes per record).

The file is cut by byte position first, then each text field is decoded as cp932 (JIS). EBCDIC is
not accepted. Each account block (header .. trailer) is checked on its own; a block whose balances
or totals do not agree is not imported and the reason names the figure that differs.
"""
from __future__ import annotations

from datetime import date

from .base import ParsedBundle, ParsedRow, ParseError, ParseResult, norm_account, to_minor
from .zengin_layout import KIND_NAMES, LAYOUTS, RECORD_LENGTH, total_width
from decimal import Decimal


def split_records(data: bytes) -> list[bytes]:
    """200-byte records, or 200 bytes + CR LF (202 per record). No other framing is accepted."""
    n = len(data)
    if n == 0:
        raise ParseError("ファイルが空")
    if n >= RECORD_LENGTH + 2 and data[RECORD_LENGTH:RECORD_LENGTH + 2] == b"\r\n":
        recs: list[bytes] = []
        pos = 0
        while pos < n:
            rec = data[pos:pos + RECORD_LENGTH]
            if len(rec) != RECORD_LENGTH:
                raise ParseError(f"{len(recs) + 1} 番目のレコードが 200 バイトに足りない")
            recs.append(rec)
            pos += RECORD_LENGTH
            if pos == n:
                break
            if data[pos:pos + 2] != b"\r\n":
                raise ParseError(f"{len(recs)} 番目のレコードの後に改行（CR LF）が無い")
            pos += 2
        return recs
    if n % RECORD_LENGTH == 0:
        return [data[i:i + RECORD_LENGTH] for i in range(0, n, RECORD_LENGTH)]
    raise ParseError(f"ファイルの長さ {n} バイトが 200 の倍数でも、200+改行 2 バイトの形でもない")


def slice_record(rec: bytes, kind: str) -> dict[str, bytes]:
    layout = LAYOUTS[kind]
    assert total_width(layout) == RECORD_LENGTH
    out: dict[str, bytes] = {}
    pos = 0
    for name, _typ, width in layout:
        out[name] = rec[pos:pos + width]
        pos += width
    return out


def _num(raw: bytes, what: str, recno: int) -> int:
    s = raw.decode("ascii", errors="replace").strip()
    if not s.isdigit():
        raise ParseError(f"レコード {recno}: {what} が数字ではない（{s!r}）")
    return int(s)


def _text(raw: bytes, what: str, recno: int) -> str:
    try:
        return raw.decode("cp932").rstrip(" 　")
    except UnicodeDecodeError:
        raise ParseError(f"レコード {recno}: {what} が JIS（cp932）の文字として読めない") from None


def _date(raw: bytes, what: str, recno: int, calendar: str) -> date:
    n = _num(raw, what, recno)
    s = f"{n:06d}"
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
    year = (2018 + yy) if calendar == "令和" else (2000 + yy)
    try:
        return date(year, mm, dd)
    except ValueError:
        raise ParseError(f"レコード {recno}: {what} が日付として正しくない（{s}、暦 {calendar}）") from None


def parse(data: bytes, src: dict) -> ParseResult:
    calendar = src["calendar"]
    multiplier = int(src["multiplier"])
    want_account = norm_account(src["account"])
    records = split_records(data)

    # ---- structure: (1 2* 8)+ 9
    kinds = []
    for i, rec in enumerate(records, start=1):
        k = chr(rec[0]) if rec[0] < 128 else "?"
        if k not in LAYOUTS:
            raise ParseError(f"レコード {i}: データ区分 {k!r} は 1・2・8・9 のどれでもない")
        kinds.append(k)
    if kinds[-1] != "9" or kinds.count("9") != 1:
        raise ParseError("最後のレコードがエンド・レコード（9）ではない、またはエンドが複数ある")

    result = ParseResult()
    end_fields = slice_record(records[-1], "9")
    end_total = _num(end_fields["レコード総件数"], "エンド レコード総件数", len(records))
    end_accounts = _num(end_fields["口座数"], "エンド 口座数", len(records))

    blocks: list[tuple[int, int]] = []  # (header index, trailer index) 0-based
    i = 0
    while i < len(records) - 1:
        if kinds[i] != "1":
            raise ParseError(f"レコード {i + 1}: ヘッダー（1）が来るはずの所に {KIND_NAMES[kinds[i]]}がある")
        j = i + 1
        while j < len(records) - 1 and kinds[j] == "2":
            j += 1
        if j >= len(records) - 1 or kinds[j] != "8":
            raise ParseError(f"レコード {i + 1} のヘッダーに対するトレーラ（8）が無い")
        blocks.append((i, j))
        i = j + 1

    file_problems: list[str] = []
    if end_total != len(records):
        file_problems.append(f"エンドのレコード総件数 {end_total} とファイルの実際の件数 {len(records)} が違う")
    if end_accounts != len(blocks):
        file_problems.append(f"エンドの口座数 {end_accounts} とファイルの実際の口座数 {len(blocks)} が違う")

    for hi, ti in blocks:
        h = slice_record(records[hi], "1")
        t = slice_record(records[ti], "8")
        recno = hi + 1
        if _num(h["データ区分"], "ヘッダー データ区分", recno) != 1:
            raise ParseError(f"レコード {recno}: ヘッダーのデータ区分が 1 ではない")
        if _num(h["種別コード"], "種別コード", recno) != 3:
            raise ParseError(f"レコード {recno}: 種別コードが 03（入出金取引明細）ではない")
        code_kind = _num(h["コード区分"], "コード区分", recno)
        if code_kind != 0:
            raise ParseError(f"レコード {recno}: コード区分 {code_kind} は受けない（JIS=0 だけ。EBCDIC は受けない）")
        account_raw = h["口座番号"].decode("ascii", errors="replace")
        account = norm_account(account_raw)
        if account != want_account:
            result.skipped.append(f"口座 {account}（データ源の口座 {want_account} ではない）")
            continue
        d_from = _date(h["勘定日（自）"], "勘定日（自）", recno, calendar)
        d_to = _date(h["勘定日（至）"], "勘定日（至）", recno, calendar)
        flag = _num(h["貸越区分"], "貸越区分", recno)
        if flag not in (1, 2):
            raise ParseError(f"レコード {recno}: 貸越区分 {flag} は 1（プラス）か 2（マイナス）")
        sign_before = -1 if flag == 2 else 1
        before = sign_before * _num(h["取引前残高"], "取引前残高", recno)

        bundle = ParsedBundle(account=account, date_from=d_from.isoformat(), date_to=d_to.isoformat(),
                              label=f"口座 {account}（レコード {hi + 1}〜{ti + 1}）")
        total_in = total_out = n_in = n_out = 0
        for k in range(hi + 1, ti):
            rn = k + 1
            d = slice_record(records[k], "2")
            kind_io = _num(d["入払区分"], "入払区分", rn)
            amount = _num(d["取引金額"], "取引金額", rn)
            booking = _date(d["勘定日"], "勘定日", rn, calendar)
            if kind_io == 1:
                total_in += amount
                n_in += 1
                sign = 1
            elif kind_io == 2:
                total_out += amount
                n_out += 1
                sign = -1
            else:
                raise ParseError(f"レコード {rn}: 入払区分 {kind_io} は 1（入金）か 2（出金）")
            if not (d_from <= booking <= d_to):
                bundle.errors.append(
                    f"レコード {rn}: 勘定日 {booking} がヘッダーの勘定日の範囲 {d_from}〜{d_to} の外")
            payer = _text(d["振込依頼人名または契約者番号"], "振込依頼人名", rn)
            summary = _text(d["摘要内容"], "摘要内容", rn)
            edi = _text(d["EDI情報"], "EDI情報", rn)
            memo = " ".join(x for x in (payer, summary) if x)
            bundle.rows.append(ParsedRow(
                booking_date=booking.isoformat(),
                amount_minor=sign * to_minor(Decimal(amount), multiplier),
                reference=edi, memo=memo, src_row=rn, row_key=None, ext_id=str(rn)))

        # ---- balance and totals (the block is refused when any of them differs)
        flag_after = _num(t["貸越区分"], "トレーラ 貸越区分", ti + 1)
        if flag_after not in (1, 2):
            raise ParseError(f"レコード {ti + 1}: トレーラの貸越区分 {flag_after} は 1（プラス）か 2（マイナス）")
        sign_after = -1 if flag_after == 2 else 1
        after = sign_after * _num(t["取引後残高"], "取引後残高", ti + 1)
        t_in_n = _num(t["入金件数"], "入金件数", ti + 1)
        t_in_sum = _num(t["入金額合計"], "入金額合計", ti + 1)
        t_out_n = _num(t["出金件数"], "出金件数", ti + 1)
        t_out_sum = _num(t["出金額合計"], "出金額合計", ti + 1)
        t_rec_n = _num(t["データ・レコード件数"], "データ・レコード件数", ti + 1)
        expected = before + total_in - total_out
        if expected != after:
            bundle.errors.append(
                f"残高が合わない: 取引前残高 {before} + 入金 {total_in} − 出金 {total_out} = {expected}"
                f"、トレーラの取引後残高は {after}（差 {after - expected}）")
        if t_in_n != n_in:
            bundle.errors.append(f"入金件数が合わない: 明細 {n_in} 件、トレーラ {t_in_n} 件")
        if t_in_sum != total_in:
            bundle.errors.append(f"入金額合計が合わない: 明細 {total_in}、トレーラ {t_in_sum}")
        if t_out_n != n_out:
            bundle.errors.append(f"出金件数が合わない: 明細 {n_out} 件、トレーラ {t_out_n} 件")
        if t_out_sum != total_out:
            bundle.errors.append(f"出金額合計が合わない: 明細 {total_out}、トレーラ {t_out_sum}")
        if t_rec_n != ti - hi - 1:
            bundle.errors.append(f"データ・レコード件数が合わない: 実際 {ti - hi - 1}、トレーラ {t_rec_n}")
        bundle.opening = before * multiplier
        bundle.closing = after * multiplier
        bundle.note = (f"残高 {before} + 入金 {total_in} − 出金 {total_out} = {expected}"
                       f"（トレーラ {after}）、入金 {n_in} 件・出金 {n_out} 件")
        bundle.errors.extend(file_problems)  # the end record disagrees: nothing in this file is trusted
        result.bundles.append(bundle)

    if not result.bundles and not result.skipped:
        raise ParseError("口座のまとまりが 1 つも無い")
    if not result.bundles:
        raise ParseError("データ源の口座に当たるまとまりが無い（" + "、".join(result.skipped) + "）")
    return result
