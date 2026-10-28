import io
import sqlite3
import zipfile

import pytest

from close_kit import config, versions, workbook
from tools import make_samples, sample_builders as sb
from conftest import load_workbook_into


def parse(data, name="w.xlsx"):
    return workbook.parse_workbook(data, name)


def errs(res):
    return [str(e) for e in res.errors]


def test_good_workbook_reads_and_is_canonical():
    res = parse(make_samples.workbook())
    assert res.ok, errs(res)
    cfg = res.config
    assert [s["name"] for s in cfg["sources"]] == ["帳簿", "銀行"]
    assert cfg["pass_rules"][3]["type"] == "多対1"
    assert cfg["pass_rules"][0]["keys"] == [["参照", "参照"]]
    again = parse(make_samples.workbook())
    assert again.sha256 == res.sha256  # same content -> same normalised JSON


def test_direction_has_exactly_two_values():
    assert workbook.DIRECTIONS == ("入金を+にする", "帳簿の借方を+にする")
    sheets = sb.default_sheets()
    sheets["データ源"][0][3] = "入金を - にする"
    res = parse(sb.build_workbook(sheets))
    assert not res.ok and any("向き" in e for e in errs(res))


def test_errors_carry_sheet_row_column_and_nothing_accepted():
    sheets = sb.default_sheets()
    sheets["照合セット"] = [["預金照合", "帳簿", "存在しない", "はい"]]
    sheets["パスルール"][1][1] = 10
    res = parse(sb.build_workbook(sheets))
    assert not res.ok and res.config is None
    msgs = errs(res)
    assert any(m.startswith("照合セット 行2 列「右のデータ源」") for m in msgs)
    assert any(m.startswith("パスルール 行3 列「順番」") for m in msgs)
    assert {e.sheet for e in res.errors} >= {"照合セット", "パスルール"}


def test_missing_column_and_wrong_type():
    hdr = sb.HEADERS["パスルール"][:]
    hdr.remove("date_window")
    res = parse(sb.build_workbook(headers={"パスルール": hdr}))
    assert any("パスルール 行1 列「date_window」" in m for m in errs(res))
    sheets = sb.default_sheets()
    sheets["パスルール"][0][6] = "三日"
    res = parse(sb.build_workbook(sheets))
    assert any(m.startswith("パスルール 行2 列「date_window」") for m in errs(res))


def test_missing_data_source_name_in_rule_set_and_missing_sheet():
    res = parse(sb.build_workbook(drop_sheets=("理由コード",)))
    assert any(e.sheet == "理由コード" for e in res.errors)


@pytest.mark.parametrize("typ,lb,rb,ok", [
    ("1対1", "", "", True), ("1対1", "参照", "", False),
    ("1対多", "", "参照", True), ("1対多", "参照", "", False), ("1対多", "参照", "参照", False),
    ("多対1", "参照", "", True), ("多対1", "", "参照", False), ("多対1", "参照", "参照", False),
    ("多対多", "参照", "参照", True), ("多対多", "参照", "", False),
])
def test_rule_type_must_agree_with_bundle_keys(typ, lb, rb, ok):
    sheets = sb.default_sheets()
    keys = "参照=参照" if typ == "1対1" else ""
    pk, limit = ("", "") if typ == "1対1" else ("参照", 10)
    sheets["パスルール"] = [["預金照合", 10, "r", "自動", typ, keys, 1, 0, lb, rb, pk, limit, "はい"]]
    res = parse(sb.build_workbook(sheets))
    assert res.ok == ok, errs(res)


def test_comparison_other_than_equals_refused():
    sheets = sb.default_sheets()
    sheets["パスルール"][0][5] = "金額>=金額"
    res = parse(sb.build_workbook(sheets))
    assert any("等しい" in m for m in errs(res))


def test_xlsm_formula_and_big_zip_refused():
    res = parse(make_samples.workbook(), "book.xlsm")
    assert not res.ok and "マクロ" in errs(res)[0]
    res = parse(sb.build_workbook(formulas={("パスルール", "G2"): "=1+2"}))
    assert any("数式" in m for m in errs(res))
    # a zip whose directory announces more than the unzipped limit
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("big.bin", b"\0" * (config.LIMIT_XLSX_UNZIPPED + 1))
    assert buf.tell() < config.LIMIT_XLSX
    res = parse(buf.getvalue())
    assert not res.ok and "展開した合計" in errs(res)[0]
    res = parse(b"x" * (config.LIMIT_XLSX + 1))
    assert not res.ok and "大きすぎる" in errs(res)[0]


def test_xlsx_with_more_entries_than_apache_poi_allows_is_refused():
    # Apache POI, ZipSecureFile.DEFAULT_MAX_FILE_COUNT = 1000, refused when the count is larger (ZipPackage.java)
    assert config.LIMIT_XLSX_ENTRIES == 1000
    assert config.LIMIT_XLSX == config.LIMIT_XLSX_UNZIPPED == 25 * 1024 * 1024  # Frappe's two defaults

    def book_with(extra: int) -> bytes:
        src = zipfile.ZipFile(io.BytesIO(make_samples.workbook()))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for info in src.infolist():
                z.writestr(info, src.read(info.filename))
            for i in range(extra):
                z.writestr(f"customXml/pad{i}.bin", b"")
        return buf.getvalue()

    base = len(zipfile.ZipFile(io.BytesIO(make_samples.workbook())).infolist())
    res = parse(book_with(config.LIMIT_XLSX_ENTRIES + 1 - base))
    assert not res.ok and "ファイル数が多すぎる" in errs(res)[0] and "上限 1000" in errs(res)[0]
    res = parse(book_with(config.LIMIT_XLSX_ENTRIES - base))
    assert not any("ファイル数" in m for m in errs(res))


def test_xlsx_part_with_doctype_or_entity_is_refused_before_parsing():
    src = zipfile.ZipFile(io.BytesIO(make_samples.workbook()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/workbook.xml":
                dtd = b'<!DOCTYPE w [<!ENTITY a "aaaa">]>'
                data = data.replace(b"?>", b"?>" + dtd, 1) if data.startswith(b"<?xml") else dtd + data
            z.writestr(info, data)
    res = parse(buf.getvalue())
    assert not res.ok and "DOCTYPE" in errs(res)[0] and "xl/workbook.xml" in errs(res)[0]
    assert parse(make_samples.workbook()).ok


def test_nothing_saved_when_refused(conn, admin):
    res = parse(make_samples.bad_workbook())
    assert not res.ok
    with pytest.raises(ValueError):
        versions.save_version(conn, admin, res, b"")
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 0


def test_two_loads_make_two_versions_old_one_unchanged(conn, admin):
    data1 = make_samples.workbook()
    v1 = load_workbook_into(conn, admin, data1)
    before = conn.execute("SELECT * FROM config_versions WHERE id = ?", (v1,)).fetchone()
    sheets = sb.default_sheets()
    sheets["パスルール"][0][6] = 5
    v2 = load_workbook_into(conn, admin, sb.build_workbook(sheets))
    assert v2 != v1
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 2
    after = conn.execute("SELECT * FROM config_versions WHERE id = ?", (v1,)).fetchone()
    assert tuple(before) == tuple(after)
    assert versions.env_version_id(conn, config.ENV_SANDBOX) == v2
    assert before["source_bytes"] == data1


def test_version_and_log_tables_refuse_update_and_delete(conn, admin, loaded):
    for sql in ("UPDATE config_versions SET sha256 = 'x'", "DELETE FROM config_versions",
                "UPDATE environment_log SET env = 'production'", "DELETE FROM environment_log",
                "UPDATE audit_log SET detail = 'x'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)


def test_only_sandbox_points_at_a_version(conn, admin, loaded):
    res = parse(make_samples.workbook())
    with pytest.raises(ValueError):
        versions.save_version(conn, admin, res, b"", env=config.ENV_PRODUCTION)
    assert versions.env_version_id(conn, config.ENV_PRODUCTION) is None
    with pytest.raises(versions.NoConfig):
        versions.load_config(conn, config.ENV_PRODUCTION)
    assert conn.execute("SELECT COUNT(*) FROM environment_log").fetchone()[0] == 1
