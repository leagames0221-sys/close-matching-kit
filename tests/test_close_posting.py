"""The posting file: heading, columns, 999 per file, once only, release, 25-character text, JPY only, environment.

The file is written from production only (the sandbox is for trying things out), so these tests run in production, whose
version got there through a change request (`close_helpers.put_in_production`)."""
import csv
import io

import pytest

from close_kit import config, journals, posting, roles, versions
from tools import make_samples, sample_builders as sb
from close_helpers import PROD, put_in_production, walk_the_path

HEADING = ["BUKRS", "BLART", "BLDAT", "BUDAT", "WAERS", "XBLNR", "BKTXT", "HKONT", "WRSOL", "WRHAB"]


def rows_of(raw: bytes):
    text = raw.decode("utf-8-sig")
    lines = text.split("\r\n")
    return lines[0], list(csv.reader(io.StringIO("\r\n".join(lines[1:]))))


def make_approved(conn, n: int, memo="ok", env: str = PROD) -> list[int]:
    """n approved journals made directly (fast): insert as drafts, then one UPDATE to 承認済み."""
    vid, cfg = versions.load_config(conn, env)
    maker = conn.execute("SELECT id FROM users WHERE name = 'demo-maker'").fetchone()[0]
    from close_kit.roles import User
    mk = User(maker, "demo-maker", frozenset({"作成者"}))
    ids = []
    with conn:
        for i in range(n):
            lines = [{"account": "6100", "debit": 1 + i, "credit": 0, "memo": ""},
                     {"account": "1100", "debit": 0, "credit": 1 + i, "memo": ""}]
            ids.append(journals._insert(conn, mk, env, vid, cfg, "2026-10-15", memo, lines, "手入力"))
        conn.executemany("UPDATE journals SET status = '承認済み' WHERE id = ?", [(i,) for i in ids])
    return ids


def test_heading_columns_environment_and_amounts(conn, oct_prod):
    u = oct_prod
    steps = walk_the_path(conn, u, env=PROD)
    res = posting.export(conn, u.admin, PROD)
    assert res.count == 2 and len(res.files) == 1
    f = res.files[0]
    assert f["name"].startswith("journal_post_production_") and f["name"].endswith("_1of1.csv")
    raw = bytes(conn.execute("SELECT content FROM posting_files WHERE id = ?", (f["id"],)).fetchone()[0])
    note, table = rows_of(raw)
    assert "production" in note and note.startswith("#")                    # the environment is in the first line
    assert table[0] == HEADING
    body = table[1:]
    assert body[0] == ["1000", "SA", "20261005", "20261005", "JPY", steps["fee"].number,
                       "振込手数料（照合セット 預金照合 グループ 2）", "6100", "50", ""]
    assert body[1][7:] == ["1100", "", "50"]                                  # credit line: WRHAB filled, WRSOL empty
    assert body[2][5] == steps["interest"].number and body[2][7:] == ["1100", "37", ""]
    assert body[3][7:] == ["4100", "", "37"]
    import hashlib
    assert f["sha256"] == hashlib.sha256(raw).hexdigest()


def test_999_journals_are_one_file_and_1000_are_two(conn, oct_prod):
    u = oct_prod
    make_approved(conn, 999)
    r = posting.export(conn, u.admin, PROD)
    assert [f["count"] for f in r.files] == [999]
    make_approved(conn, 1000)
    r2 = posting.export(conn, u.admin, PROD)
    assert [f["count"] for f in r2.files] == [999, 1] and r2.files[0]["name"].endswith("_1of2.csv")
    assert r2.files[1]["name"].endswith("_2of2.csv")


def test_the_same_journal_is_not_written_twice_until_it_is_released(conn, oct_prod):
    u = oct_prod
    ids = make_approved(conn, 2)
    posting.export(conn, u.admin, PROD)
    with pytest.raises(posting.PostingError) as e:
        posting.export(conn, u.admin, PROD)
    assert "無い" in str(e.value)
    with pytest.raises(roles.PermissionDenied):
        posting.release(conn, u.maker, ids[0], "再出力", PROD)
    with pytest.raises(posting.PostingError):
        posting.release(conn, u.admin, ids[0], "", PROD)                         # a reason is required
    posting.release(conn, u.admin, ids[0], "SAP に取り込めなかった", PROD)
    again = posting.export(conn, u.admin, PROD)
    assert again.count == 1
    raw = bytes(conn.execute("SELECT content FROM posting_files WHERE id = ?", (again.files[0]["id"],)).fetchone()[0])
    assert "JE-%06d" % ids[0] in raw.decode("utf-8-sig") and "JE-%06d" % ids[1] not in raw.decode("utf-8-sig")
    log = [(r["journal_id"], r["action"]) for r in conn.execute("SELECT journal_id, action FROM posting_log ORDER BY id")]
    assert log == [(ids[0], "出力"), (ids[1], "出力"), (ids[0], "外す"), (ids[0], "出力")]
    with pytest.raises(posting.PostingError):
        posting.release(conn, u.admin, 999999, "x", PROD)


def test_the_posting_tables_are_append_only(conn, oct_prod):
    u = oct_prod
    make_approved(conn, 1)
    posting.export(conn, u.admin, PROD)
    import sqlite3
    for sql in ("UPDATE posting_log SET note = 'x'", "DELETE FROM posting_log", "UPDATE posting_files SET name = 'x'",
                "DELETE FROM posting_files"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)


def test_the_memo_is_cut_to_25_characters_and_the_screen_says_so(conn, oct_prod):
    u = oct_prod
    long_memo = "あ" * 30
    ids = make_approved(conn, 1, memo=long_memo)
    short = make_approved(conn, 1, memo="い" * 25)
    res = posting.export(conn, u.admin, PROD)
    assert res.truncated == ["JE-%06d" % ids[0]]
    raw = bytes(conn.execute("SELECT content FROM posting_files WHERE id = ?", (res.files[0]["id"],)).fetchone()[0])
    _note, table = rows_of(raw)
    bk = {r[5]: r[6] for r in table[1:]}
    assert bk["JE-%06d" % ids[0]] == "あ" * 25 and bk["JE-%06d" % short[0]] == "い" * 25


def test_the_file_is_refused_unless_the_unit_is_1(conn, users):
    s = make_samples.close_sheets()
    s["データ源"][0][4] = 100
    s["データ源"][1][4] = 100
    put_in_production(conn, sb.build_workbook(s), users.admin, users.admin2, users.approver)
    make_approved(conn, 1)
    with pytest.raises(posting.PostingError) as e:
        posting.export(conn, users.admin, PROD)
    assert "JPY" in str(e.value) or "倍率" in str(e.value)
    assert conn.execute("SELECT COUNT(*) FROM posting_files").fetchone()[0] == 0
    row = conn.execute("SELECT outcome FROM audit_log WHERE action = 'posting_export' ORDER BY id DESC").fetchone()
    assert row["outcome"] == "拒否"


def test_only_an_administrator_writes_the_file_and_the_log_records_who_and_where(conn, oct_prod):
    u = oct_prod
    make_approved(conn, 1)
    for who in (u.maker, u.approver, u.auditor):
        with pytest.raises(roles.PermissionDenied):
            posting.export(conn, who, PROD)
    posting.export(conn, u.admin, PROD)
    row = conn.execute("SELECT * FROM posting_log").fetchone()
    assert (row["env"], row["by_user"], row["action"]) == (config.ENV_PRODUCTION, u.admin.id, "出力") and row["sha256"] and row["at"]


def test_a_formula_looking_memo_never_reaches_the_file(conn, oct_prod):
    u = oct_prod
    with pytest.raises(journals.JournalInvalid):
        journals.create_manual(conn, u.maker, "2026-10-15", "=1+1", [
            {"account": "6100", "debit": 1, "credit": 0, "memo": ""}, {"account": "1100", "debit": 0, "credit": 1, "memo": ""}],
            env=PROD)


def test_the_sandbox_never_writes_a_posting_file(conn, oct_world):
    """The posting file comes from production only: in the sandbox the output is refused, and the refusal is logged."""
    u = oct_world
    make_approved(conn, 1, env="sandbox")
    with pytest.raises(posting.PostingError) as e:
        posting.export(conn, u.admin)
    assert "production" in str(e.value) and "試験用" in str(e.value)
    assert conn.execute("SELECT COUNT(*) FROM posting_files").fetchone()[0] == 0
    row = conn.execute("SELECT outcome, env FROM audit_log WHERE action = 'posting_export' ORDER BY id DESC").fetchone()
    assert (row["outcome"], row["env"]) == ("拒否", "sandbox")
