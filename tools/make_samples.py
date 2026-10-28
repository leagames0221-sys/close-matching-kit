"""Write the sample workbook and sample statements into `samples/` (all values are made up).

Run from the repo root:  .venv\\Scripts\\python tools\\make_samples.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import sample_builders as sb  # noqa: E402

LEDGER_HEADER = ["取引ID", "日付", "金額", "参照", "摘要"]
LEDGER_ROWS = [
    ["T001", "2026-10-01", 330000, "INV-2610-001", "10月分 売上（サンプル商店）"],
    ["T002", "2026-10-04", 220000, "INV-2610-002", "10月分 売上（見本工業）"],
    ["T003", "2026-10-05", 150000, "INV-2610-003", "10月分 売上（試験商会）"],
    ["T004", "2026-10-08", 80000, "INV-2610-004", "10月分 売上（仮名物産）"],
    ["T005", "2026-10-10", 45000, "", "現金売上の入金"],
    ["T006", "2026-10-12", 12000, "", "雑収入"],
    ["T007", "2026-10-15", 33000, "", "回収（参照なし）"],
    ["T008", "2026-10-16", 20000, "INV-2610-005", "分割請求 1/3"],
    ["T009", "2026-10-16", 30000, "INV-2610-005", "分割請求 2/3"],
    ["T010", "2026-10-16", 50000, "INV-2610-005", "分割請求 3/3"],
    ["T011", "2026-10-20", 7777, "", "入金予定（未着）"],
    ["T012", "2026-10-22", 60000, "INV-2610-009", "二重に入力した売上"],
    ["T013", "2026-10-22", 60000, "INV-2610-009", "二重に入力した売上"],
    ["T014", "2026-10-24", 5000, "INV-2610-010", "二重に入力（相手なし）"],
    ["T015", "2026-10-24", 5000, "INV-2610-010", "二重に入力（相手なし）"],
]
D = date
BANK_ENTRIES = [  # (date, in/out, amount, payer, summary, edi)
    (D(2026, 10, 2), "in", 330000, "ｻﾝﾌﾟﾙｼｮｳﾃﾝ", "ﾌﾘｺﾐ", "INV-2610-001"),
    (D(2026, 10, 5), "in", 219890, "ﾐﾎﾝｺｳｷﾞｮｳ", "ﾌﾘｺﾐ", "INV-2610-002"),
    (D(2026, 10, 6), "in", 149950, "ﾃｽﾄｼｮｳｶｲ", "ﾌﾘｺﾐ", "INV-2610-003"),
    (D(2026, 10, 11), "in", 45000, "ｹﾞﾝｷﾝ", "ﾆｭｳｷﾝ", ""),
    (D(2026, 10, 12), "in", 12000, "ｶﾒｲ", "ﾌﾘｺﾐ", ""),
    (D(2026, 10, 13), "in", 12000, "ｶﾒｲ2", "ﾌﾘｺﾐ", ""),
    (D(2026, 10, 17), "in", 100000, "ｶﾒｲﾌﾞｯｻﾝ", "ﾌﾘｺﾐ", "INV-2610-005"),
    (D(2026, 10, 20), "in", 33000, "ｶｲｼｭｳ", "ﾌﾘｺﾐ", ""),
    (D(2026, 10, 23), "in", 60000, "ﾆｼﾞｭｳｼｮｳｶｲ", "ﾌﾘｺﾐ", "INV-2610-009"),
    (D(2026, 10, 25), "in", 80000, "ｶﾒｲﾌﾞｯｻﾝ2", "ﾌﾘｺﾐ", "INV-2610-004"),
    (D(2026, 10, 28), "out", 1100, "ﾃｽｳﾘｮｳ", "ﾌﾘｺﾐﾃｽｳﾘｮｳ", ""),
]
BANK_ENTRIES.sort(key=lambda e: e[0])
OPENING = 1_000_000
PERIOD = (D(2026, 10, 1), D(2026, 10, 31))


def ledger_csv() -> bytes:
    return sb.build_csv(LEDGER_HEADER, LEDGER_ROWS)


def bank_zengin(**kw) -> bytes:
    return sb.build_zengin(BANK_ENTRIES, d_from=PERIOD[0], d_to=PERIOD[1], opening=OPENING, **kw)


def workbook() -> bytes:
    return sb.build_workbook()


def bad_workbook() -> bytes:
    sheets = sb.default_sheets()
    sheets["パスルール"] = [list(r) for r in sheets["パスルール"]]
    sheets["パスルール"][1][1] = 10              # order 10 twice
    sheets["パスルール"][2][4] = "1対多"          # type that does not agree with the bundle keys
    sheets["照合セット"] = [["預金照合", "帳簿", "存在しない銀行", "はい", "右", "1100", "いいえ"]]
    return sb.build_workbook(sheets)


# ----------------------------------------------------------------------------------------------
# The month-end sample (second release). A made-up company; every figure below was fixed first and the
# reconciliation was built to reach it (see docs/validation/acceptance.md for the table of the path).
#
#   bank (全銀, account 1234567)   opening 1,000,000 on 2026-10-01
#   ledger (CSV with a balance)    opening 1,000,000 on 2026-10-01
#
#   path to a difference of 0 on 2026-10-31 (bank 1,474,987, books 1,504,000):
#     INV-2610-003  books 150,000 / bank 149,950      fee 50     rule 10 names the account 6100: the pair is 完了 at
#                                                                 once and the tool drafts the journal -> books -50
#     (bank only)   interest 37 on 10-25                         reason 受取利息   -> journal   -> books +37
#     INV-2610-009  books +70,000 on 10-31, bank 11-02           reason 未達の入金 -> item      -> bank +70,000
#     PAY-2610-02   books -41,000 on 10-29, bank 11-02           the November statement pairs it -> bank -41,000
#     adjusted bank 1,474,987 + 70,000 - 41,000 = 1,503,987 = adjusted books 1,504,000 - 50 + 37
# ----------------------------------------------------------------------------------------------

SHIPPED_DIFF_ACCOUNT = "6100"
CLOSE_LEDGER_HEADER = ["取引ID", "日付", "金額", "参照", "摘要", "残高"]
_CLOSE_LEDGER_OCT = [  # (id, date, amount, reference, memo)
    ("T001", "2026-10-01", 330000, "INV-2610-001", "10月分 売上（サンプル商店）"),
    ("T002", "2026-10-04", 150000, "INV-2610-003", "10月分 売上（試験商会）"),
    ("T003", "2026-10-10", -5000, "PAY-2610-01", "仕入先への支払（見本工業）"),
    ("T004", "2026-10-29", -41000, "PAY-2610-02", "仕入先への支払（仮名物産・月末の振込）"),
    ("T005", "2026-10-31", 70000, "INV-2610-009", "10月分 売上（月末の入金予定）"),
]
_CLOSE_LEDGER_NOV = [("T101", "2026-11-05", 20000, "INV-2611-001", "11月分 売上（見本工業）")]
CLOSE_OPENING = 1_000_000
CLOSE_BANK_OCT = [  # (date, in/out, amount, payer, summary, edi)
    (D(2026, 10, 2), "in", 330000, "ｻﾝﾌﾟﾙｼｮｳﾃﾝ", "ﾌﾘｺﾐ", "INV-2610-001"),
    (D(2026, 10, 5), "in", 149950, "ﾃｽﾄｼｮｳｶｲ", "ﾌﾘｺﾐ", "INV-2610-003"),
    (D(2026, 10, 11), "out", 5000, "ﾐﾎﾝｺｳｷﾞｮｳ", "ﾌﾘｺﾐ", "PAY-2610-01"),
    (D(2026, 10, 25), "in", 37, "ｷﾞﾝｺｳ", "ﾘｿｸ", ""),
]
CLOSE_BANK_NOV = [
    (D(2026, 11, 2), "out", 41000, "ｶﾒｲﾌﾞｯｻﾝ", "ﾌﾘｺﾐ", "PAY-2610-02"),
    (D(2026, 11, 2), "in", 70000, "ｻﾝﾌﾟﾙｼｮｳﾃﾝ2", "ﾆｭｳｷﾝ", "INV-2610-009"),
    (D(2026, 11, 6), "in", 20000, "ﾐﾎﾝｺｳｷﾞｮｳ", "ﾌﾘｺﾐ", "INV-2611-001"),
]
CLOSE_BANK_OCT_CLOSING = CLOSE_OPENING + 330000 + 149950 - 5000 + 37        # 1,474,987
CLOSE_LEDGER_OCT_CLOSING = CLOSE_OPENING + 330000 + 150000 - 5000 - 41000 + 70000  # 1,504,000


def _ledger(rows, opening):
    out, bal = [], opening
    for i, d, amt, ref, memo in rows:
        bal += amt
        out.append([i, d, amt, ref, memo, bal])
    return sb.build_csv(CLOSE_LEDGER_HEADER, out)


def close_ledger_oct() -> bytes:
    return _ledger(_CLOSE_LEDGER_OCT, CLOSE_OPENING)


def close_ledger_nov() -> bytes:
    return _ledger(_CLOSE_LEDGER_NOV, CLOSE_LEDGER_OCT_CLOSING)


def close_bank_oct(**kw) -> bytes:
    return sb.build_zengin(CLOSE_BANK_OCT, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=CLOSE_OPENING, **kw)


def close_bank_nov(**kw) -> bytes:
    return sb.build_zengin(CLOSE_BANK_NOV, d_from=D(2026, 11, 1), d_to=D(2026, 11, 30),
                           opening=CLOSE_BANK_OCT_CLOSING, **kw)


# Six steps taken from the 13 of the CC BY 4.0 month-end close tracker of ZeraBooks (commit 8d31709), translated; the
# "Due day: Day N" is read as the N-th business day after the period end (small-business/README.md of the same commit:
# "Due day counts working days after month end."). Credit:
# ZeraBooks, month-end-checklists, CC BY 4.0, commit 8d31709; translation and selection by this repository.
# T07 is this repository's own addition (not from ZeraBooks): it is counted in calendar days so that the sample shows
# both ways of counting.
CLOSE_TASKS = [  # id, name, counting, N, predecessors, condition, match set
    ("T01", "明細の取り込み（銀行・カード・ローンの明細をそろえる）", "営業日", 5, "", "なし", ""),
    ("T02", "預金の残高一致（預金口座を明細の期末残高に合わせる）", "営業日", 7, "T01", "残高一致の差 0", "預金照合"),
    ("T03", "売掛金と買掛金の確認", "営業日", 7, "T02", "なし", ""),
    ("T04", "未払費用の計上", "営業日", 8, "T03", "なし", ""),
    ("T05", "損益の前月比較", "営業日", 9, "T04", "なし", ""),
    ("T06", "締めと報告の送付", "営業日", 10, "T05", "未処理 0 件", "預金照合"),
    ("T07", "報告書の保管（当社の追加・暦日で数える）", "暦日", 30, "T06", "なし", ""),
]


def close_sheets(*, preparer="demo-maker", reviewer="demo-approver", approvers=("demo-approver", "demo-approver2"),
                 senior=("demo-approver2",), threshold=100000, diff_account="") -> dict:
    sheets = sb.default_sheets()
    sheets["説明"] = [["2", "サンプル商事株式会社（架空）", "サンプル担当", D(2026, 10, 24), "1000", "SA"]]
    sheets["データ源"] = [
        ["帳簿", "csv", "", "帳簿の借方を + にする", 1, "西暦", "取引ID", "日付", "金額", "参照", "摘要", "残高"],
        ["銀行", "zengin", "1234567", "入金を + にする", 1, "令和", "", "", "", "", "", ""],
    ]
    sheets["照合セット"] = [["預金照合", "帳簿", "銀行", "はい", "右", "1100", "はい"]]
    sheets["パスルール"] = [list(r) for r in sheets["パスルール"]]
    sheets["パスルール"][0][6] = 5          # 参照が同じ: date window 5 days (a month-end payment shows 4 days later)
    # 差額の相手勘定 of rule 10: with "6100" (the shipped sample, SHIPPED_DIFF_ACCOUNT) a difference inside the tolerance
    # (the transfer fee of INV-2610-003) is booked by the tool to the fee account as a journal draft; with "" (the tests'
    # default) the pair waits in 差額の処理待ち and a person uses the reason code R01 - both give the same figures
    sheets["パスルール"][0] = sheets["パスルール"][0][:13] + [diff_account]
    sheets["承認ルート"] = [[0, threshold, "、".join(approvers)], [threshold, "", "、".join(senior)]]
    sheets["作業一覧"] = [[tid, name, "繰り返し", "", counting, n, pre, preparer, reviewer, cond, ms, "はい"]
                      for tid, name, counting, n, pre, cond, ms in CLOSE_TASKS]
    sheets["会社の休日"] = [[D(2026, 11, 6), "創立記念日（架空）"]]
    return sheets


def close_workbook(**names) -> bytes:
    return sb.build_workbook(close_sheets(**names))


def journals_import_good() -> bytes:
    rows = [["A-001", "2026-10-31", "6100", 12000, "1200", 12000, "見越し計上（見本）"],
            ["A-002", "2026/10/30", "1200", 3000, "", "", "売掛金の振替（借方）"],
            ["A-002", "2026/10/30", "", "", "4100", 3000, "売掛金の振替（貸方）"]]
    return sb.build_csv(["伝票No.", "取引日付", "借方勘定科目", "借方金額", "貸方勘定科目", "貸方金額", "摘要"], rows)


def journals_import_bad() -> bytes:
    rows = [["B-001", "2026-10-31", "6100", 10000, "1200", 9000, "借方と貸方が合わない"],
            ["B-002", "2026-10-31", "9999", 500, "1100", 500, "勘定表に無いコード"],
            ["B-003", "2026-09-30", "6100", 700, "1100", 700, "締めた期間（2026-09）の日付"]]
    return sb.build_csv(["伝票No.", "取引日付", "借方勘定科目", "借方金額", "貸方勘定科目", "貸方金額", "摘要"], rows)


def walk(tmp_dir: Path | None = None) -> dict:
    """Reproduce the sample's path to a difference of 0 on a throw-away database, with no server.

    The path itself lives in `close_kit.changecheck.walk_sample` (the test of a change request runs the same code on
    the files of `samples/`); here it is run on the sample builders' own bytes. Returns {period: Statement};
    `python tools/make_samples.py walk` prints them as the table in docs/validation/acceptance.md.
    """
    import tempfile

    from close_kit import changecheck

    files = {"ledger_oct": close_ledger_oct(), "bank_oct": close_bank_oct(), "ledger_nov": close_ledger_nov(),
             "bank_nov": close_bank_nov()}
    work = tmp_dir or Path(tempfile.mkdtemp())
    return changecheck.walk_sample(close_workbook(diff_account=SHIPPED_DIFF_ACCOUNT), files, work).statements


def print_walk() -> None:
    res = walk()
    print("| period | bank end | + in transit | - outstanding | = adjusted bank | books end | +/- bank movements | = adjusted books | difference |")
    print("|---|---|---|---|---|---|---|---|---|")
    for period, st in res.items():
        print(f"| {period} | {st.bank_end:,} | {st.transit_in:,} | {st.outstanding_out:,} | {st.adjusted_bank:,} | "
              f"{st.book_end:,} | {st.book_movements:,} | {st.adjusted_book:,} | {st.difference:,} |")
    for ln in res["2026-10"].lines:
        print(f"  {ln.section} / {ln.kind}: {ln.amount:,} ({ln.date} {ln.journal or ln.ref} {ln.reason_code})")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "walk":
        print_walk()
        return
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    (out / "sample_workbook.xlsx").write_bytes(close_workbook(diff_account=SHIPPED_DIFF_ACCOUNT))
    (out / "ledger_2026-10.csv").write_bytes(close_ledger_oct())
    (out / "ledger_2026-11.csv").write_bytes(close_ledger_nov())
    (out / "bank_2026-10_zengin.txt").write_bytes(close_bank_oct())
    (out / "bank_2026-11_zengin.txt").write_bytes(close_bank_nov())
    (out / "journals_import_sample.csv").write_bytes(journals_import_good())
    (out / "journals_import_bad.csv").write_bytes(journals_import_bad())
    (out / "bad_workbook.xlsx").write_bytes(bad_workbook())
    (out / "bad_bank_balance_mismatch_zengin.txt").write_bytes(close_bank_oct(closing_override=CLOSE_OPENING + 1))
    print("written to", out)


if __name__ == "__main__":
    main()
