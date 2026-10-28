from datetime import date

import pytest

from close_kit import ingest, roles, safefile
from close_kit.formats import camt052, csvfmt, zengin
from close_kit.formats.base import ParseError, parse_amount
from tools import make_samples, sample_builders as sb

D = date
ZSRC = {"calendar": "令和", "multiplier": 1, "account": "1234567"}
CSRC = {"multiplier": 100, "account": "", "row_key_cols": [],
        "csv": {"date": "date", "amount": "amount", "reference": "reference", "memo": "description",
                "balance": ""}}
ENTRIES = [(D(2026, 10, 2), "in", 5000, "ｱ", "ﾌﾘｺﾐ", "R-1"), (D(2026, 10, 3), "out", 1200, "ｲ", "", "R-2")]


def z(**kw):
    return sb.build_zengin(ENTRIES, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000, **kw)


# ---- zengin ------------------------------------------------------------------------------

@pytest.mark.parametrize("crlf", [False, True])
def test_zengin_200_and_202_byte_records(crlf):
    data = z(crlf=crlf)
    assert len(data) == (5 * 202 - 2 if crlf else 5 * 200)
    res = zengin.parse(data, ZSRC)
    b = res.bundles[0]
    assert not b.errors, b.errors
    assert [r.amount_minor for r in b.rows] == [5000, -1200]
    assert [r.reference for r in b.rows] == ["R-1", "R-2"]
    assert b.rows[0].booking_date == "2026-10-02"
    assert b.date_from == "2026-10-01" and b.date_to == "2026-10-31"


def test_zengin_calendar_comes_from_the_data_source():
    data = sb.build_zengin(ENTRIES, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=0, calendar="西暦")
    res = zengin.parse(data, {**ZSRC, "calendar": "西暦"})
    assert res.bundles[0].rows[0].booking_date == "2026-10-02"
    res = zengin.parse(data, ZSRC)  # read as 令和: yy 26 -> 2044, a valid date but another one
    assert res.bundles[0].rows[0].booking_date == "2044-10-02"


def test_zengin_overdraft_sign():
    data = sb.build_zengin([(D(2026, 10, 2), "in", 3000, "ｱ", "", "")], d_from=D(2026, 10, 1),
                           d_to=D(2026, 10, 31), opening=1000, overdraft_open=True)
    b = zengin.parse(data, ZSRC).bundles[0]
    assert not b.errors, b.errors  # -1000 + 3000 = 2000 (not overdrawn at the end)
    bad = sb.build_zengin([(D(2026, 10, 2), "in", 3000, "ｱ", "", "")], d_from=D(2026, 10, 1),
                          d_to=D(2026, 10, 31), opening=1000, overdraft_open=True, closing_override=4000)
    assert zengin.parse(bad, ZSRC).bundles[0].errors
    # closing balance negative is written with the overdraft flag
    neg = sb.build_zengin([(D(2026, 10, 2), "out", 3000, "ｱ", "", "")], d_from=D(2026, 10, 1),
                          d_to=D(2026, 10, 31), opening=1000)
    assert not zengin.parse(neg, ZSRC).bundles[0].errors


def test_zengin_balance_trailer_and_end_mismatches_name_the_figure():
    b = zengin.parse(z(closing_override=1), ZSRC).bundles[0]
    assert any("残高が合わない" in e for e in b.errors)
    b = zengin.parse(z(trailer_count_override=5), ZSRC).bundles[0]
    assert any("データ・レコード件数" in e for e in b.errors)
    b = zengin.parse(z(end_total_override=99), ZSRC).bundles[0]
    assert any("レコード総件数" in e for e in b.errors)


def test_zengin_structure_errors():
    with pytest.raises(ParseError):
        zengin.parse(z()[:-200], ZSRC)             # no end record
    with pytest.raises(ParseError):
        zengin.parse(b"", ZSRC)
    ebcdic = bytearray(z())
    ebcdic[3] = ord("1")                            # コード区分 = 1 (not 0 = JIS; the MUFG manual lists only "0：JIS" and calls the character code "JISコードまたはEBCDICコード", it does not show which value is EBCDIC)
    with pytest.raises(ParseError, match="EBCDIC"):
        zengin.parse(bytes(ebcdic), ZSRC)
    with pytest.raises(ParseError, match="口座"):
        zengin.parse(z(), {**ZSRC, "account": "7654321"})
    bad_flag = bytearray(z())
    bad_flag[1 + 2 + 1 + 6 + 6 + 6 + 4 + 15 + 3 + 15 + 3 + 1 + 10 + 40] = ord("0")   # header 貸越区分 = 0
    with pytest.raises(ParseError, match="貸越区分"):
        zengin.parse(bytes(bad_flag), ZSRC)


def test_zengin_account_blocks_are_checked_one_by_one():
    other = sb.build_zengin([(D(2026, 10, 2), "in", 100, "ｳ", "", "")], account="7654321",
                            d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=0)
    other_block = other[:-200]  # drop its own end record
    data = sb.build_zengin(ENTRIES, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000,
                           extra_blocks=[other_block])
    res = zengin.parse(data, ZSRC)
    assert len(res.bundles) == 1 and not res.bundles[0].errors and res.skipped


# ---- camt.052 ----------------------------------------------------------------------------

CENT = [(D(2026, 10, 2), "CRDT", 5000, "アイウ", "振込", "R-1"), (D(2026, 10, 3), "DBIT", 1200, "エオ", "手数料", "")]


def c(**kw):
    return sb.build_camt(CENT, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000, **kw)


def test_camt_reads_direction_and_reference():
    b = camt052.parse(c(), {"multiplier": 1, "account": "1234567"}).bundles[0]
    assert not b.errors, b.errors
    assert [r.amount_minor for r in b.rows] == [5000, -1200]
    assert b.rows[0].reference == "R-1" and "アイウ" in b.rows[0].memo
    assert b.rows[0].row_key == "1234567|2026-10-02|02000001"


def test_camt_zero_balance_with_entries_is_refused():
    b = camt052.parse(c(zero_balances=True), {"multiplier": 1, "account": "1234567"}).bundles[0]
    assert any("検算できない" in e and "制限" in e for e in b.errors)


def test_camt_balance_and_count_mismatch():
    src = {"multiplier": 1, "account": "1234567"}
    assert any("残高が合わない" in e for e in camt052.parse(c(closing_override=1), src).bundles[0].errors)
    assert any("件数" in e for e in camt052.parse(c(nb_override=7), src).bundles[0].errors)
    # a debit entry flips the sign: if it were read as a credit the balance would not agree
    flipped = c().replace(b"<CdtDbtInd>DBIT</CdtDbtInd><Sts>", b"<CdtDbtInd>CRDT</CdtDbtInd><Sts>")
    assert camt052.parse(flipped, src).bundles[0].errors


def test_camt_doctype_and_entity_refused():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
            b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>')
    with pytest.raises(safefile.FileRejected):
        camt052.parse(bomb, {"multiplier": 1, "account": "1"})
    with pytest.raises(safefile.FileRejected):
        safefile.check_xml(b"<!ENTITY x 'y'><a/>")


# ---- CSV ---------------------------------------------------------------------------------

def test_csv_thousands_separator_and_bracketed_negatives():
    data = sb.build_csv(["date", "amount", "reference", "description"],
                        [["2024-02-01", "1,250.00", "A", "x"], ["2024-02-02", "(1,075.50)", "B", "y"],
                         ["2024-02-03", "-5", "C", "z"]])
    b = csvfmt.parse(data, CSRC).bundles[0]
    assert not b.errors, b.errors
    assert [r.amount_minor for r in b.rows] == [125000, -107550, -500]
    assert parse_amount("¥1,000") == 1000


def test_csv_balance_column_checked_only_when_mapped():
    rows = [["2024-02-01", "100", "A", "x", "1100"], ["2024-02-02", "-30", "B", "y", "1070"]]
    hdr = ["date", "amount", "reference", "description", "balance"]
    src = {**CSRC, "multiplier": 1, "csv": {**CSRC["csv"], "balance": "balance"}}
    assert not csvfmt.parse(sb.build_csv(hdr, rows), src).bundles[0].errors
    bad = [rows[0], ["2024-02-02", "-30", "B", "y", "1000"]]
    errors = csvfmt.parse(sb.build_csv(hdr, bad), src).bundles[0].errors
    assert errors and "2 行目" not in errors[0] and "3 行目" in errors[0]
    # without the mapping the wrong balance is not looked at
    assert not csvfmt.parse(sb.build_csv(hdr, bad), {**src, "csv": {**CSRC["csv"]}}).bundles[0].errors


def test_csv_missing_column_and_shift_jis():
    with pytest.raises(ParseError, match="見出しに無い列"):
        csvfmt.parse(sb.build_csv(["date", "amount"], [["2024-01-01", "1"]]), CSRC)
    data = sb.build_csv(["date", "amount", "reference", "description"], [["2024-01-01", "1", "R", "日本語"]],
                        encoding="cp932")
    assert csvfmt.parse(data, CSRC).bundles[0].rows[0].memo == "日本語"


# ---- ingest + duplicate refusal ----------------------------------------------------------------

def ledger(conn, user, data=None, name="ledger.csv"):
    return ingest.ingest(conn, user, "帳簿", name, data or make_samples.ledger_csv())


def bank(conn, user, data=None):
    return ingest.ingest(conn, user, "銀行", "bank.txt", data or make_samples.bank_zengin())


def test_ingest_stores_rows_with_run_environment_and_version(conn, admin, loaded):
    r = bank(conn, admin)
    assert r.ok and r.rows == 11
    row = conn.execute("SELECT * FROM transactions ORDER BY id LIMIT 1").fetchone()
    assert row["env"] == "sandbox" and row["version_id"] == loaded and row["run_id"] == r.run_id
    assert row["period"] == "2026-10"
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (r.run_id,)).fetchone()
    assert run["kind"] == "ingest" and run["env"] == "sandbox" and run["version_id"] == loaded


def test_ingest_refuses_unbalanced_zengin_and_stores_nothing(conn, admin, loaded):
    r = bank(conn, admin, make_samples.bank_zengin(closing_override=5))
    assert not r.ok and any("残高が合わない" in x for x in r.reasons)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
    log = conn.execute("SELECT outcome, detail FROM audit_log WHERE action='ingest'").fetchone()
    assert log["outcome"] == "拒否" and "残高" in log["detail"]


def test_second_import_of_same_range_refused(conn, admin, loaded):
    assert bank(conn, admin).ok
    r = bank(conn, admin)
    assert not r.ok
    assert any("sha256" in x for x in r.reasons) and any("重なる" in x for x in r.reasons)
    # a different file with an overlapping range is refused by the range alone
    other = sb.build_zengin([(D(2026, 10, 9), "in", 1, "ｱ", "", "X")], d_from=D(2026, 10, 9),
                            d_to=D(2026, 10, 12), opening=2040740)
    r = bank(conn, admin, other)
    assert not r.ok and any("重なる" in x for x in r.reasons) and not any("sha256" in x for x in r.reasons)
    # the next day range is accepted
    nxt = sb.build_zengin([(D(2026, 11, 1), "in", 1, "ｱ", "", "X")], d_from=D(2026, 11, 1),
                          d_to=D(2026, 11, 5), opening=2040740)
    assert bank(conn, admin, nxt).ok


def test_csv_duplicate_by_sha256_and_by_row_key(conn, admin, loaded):
    assert ledger(conn, admin).ok
    r = ledger(conn, admin)
    assert not r.ok and any("sha256" in x for x in r.reasons)
    # different dates (no overlap) but the same 取引ID: refused through the row key
    hdr = ["取引ID", "日付", "金額", "参照", "摘要"]
    r = ledger(conn, admin, sb.build_csv(hdr, [["T001", "2026-12-01", 1, "", ""]]))
    assert not r.ok and any("行のキー" in x for x in r.reasons) and not any("重なる" in x for x in r.reasons)
    r = ledger(conn, admin, sb.build_csv(hdr, [["T900", "2026-12-01", 1, "", ""]]))
    assert r.ok


def test_camt_row_key_is_account_date_ref(conn, admin, loaded):
    sheets = sb.default_sheets()
    sheets["データ源"][1] = ["銀行", "camt052", "1234567", "入金を + にする", 1, "", "", "", "", "", "", ""]
    from conftest import load_workbook_into
    load_workbook_into(conn, admin, sb.build_workbook(sheets))
    data = sb.build_camt(CENT, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000)
    assert bank(conn, admin, data).ok
    keys = [r[0] for r in conn.execute("SELECT row_key FROM transactions ORDER BY id")]
    assert keys == ["1234567|2026-10-02|02000001", "1234567|2026-10-03|03000002"]
    # same refs, range moved off: the range does not overlap, the row keys do not either -> accepted?
    # (a repeated key can only come from a repeated date, which the range check already refuses)
    r = bank(conn, admin, data)
    assert not r.ok


def test_ingest_unknown_source_and_no_config(conn, admin):
    r = ingest.ingest(conn, admin, "帳簿", "a.csv", b"x")
    assert not r.ok and "設定の版がまだ無い" in r.reasons[0]


def test_ingest_is_checked_by_role_on_the_server(conn, loaded, approver):
    with pytest.raises(roles.PermissionDenied):
        ledger(conn, approver)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    log = conn.execute("SELECT outcome FROM audit_log WHERE action='ingest'").fetchone()
    assert log["outcome"] == "拒否"


def test_two_bundles_of_one_account_in_the_same_file_are_refused(conn, admin, loaded):
    """Overlapping blocks / reports inside one file are refused as a whole, not stored twice or crashing."""
    other = sb.build_zengin([(D(2026, 10, 9), "in", 1, "ｱ", "", "X")], d_from=D(2026, 10, 9),
                            d_to=D(2026, 10, 12), opening=0)[:-200]
    data = sb.build_zengin(ENTRIES, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000,
                           extra_blocks=[other])
    src = {**ZSRC, "calendar": "令和"}
    assert len(zengin.parse(data, src).bundles) == 2
    from conftest import load_workbook_into
    sheets = sb.default_sheets()
    sheets["データ源"][1] = ["銀行", "zengin", "1234567", "入金を + にする", 1, "令和", "", "", "", "", "", ""]
    load_workbook_into(conn, admin, sb.build_workbook(sheets))
    r = bank(conn, admin, data)
    assert not r.ok and any("同じファイルの中で勘定日の範囲が重なる" in x for x in r.reasons)
    # camt: the same report twice in one file (same row keys) is a refusal, not a database error
    sheets["データ源"][1] = ["銀行", "camt052", "1234567", "入金を + にする", 1, "", "", "", "", "", "", ""]
    load_workbook_into(conn, admin, sb.build_workbook(sheets))
    one = sb.build_camt(CENT, d_from=D(2026, 10, 1), d_to=D(2026, 10, 31), opening=10000)
    rpt = one[one.index(b"<Rpt>"):one.index(b"</Rpt>") + len(b"</Rpt>")]
    twice = one.replace(rpt, rpt + rpt)
    r = bank(conn, admin, twice)
    assert not r.ok and any("同じ行のキーが 2 回" in x for x in r.reasons)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
