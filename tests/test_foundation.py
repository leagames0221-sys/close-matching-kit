import sqlite3

import pytest

from close_kit import audit, auth, config, db, safefile
from close_kit.formats import zengin
from close_kit.formats import zengin_layout as zl
from close_kit.formats.base import ParseError


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "t.sqlite3")


def test_zengin_layouts_sum_to_200():
    for name in ("HEADER", "DATA", "TRAILER", "END"):
        assert zl.total_width(getattr(zl, name)) == 200, name


def test_zengin_split_rejects_bad_length():
    with pytest.raises(ParseError):
        zengin.split_records(b"1" * 199)
    assert len(zengin.split_records(b"1" * 400)) == 2
    assert len(zengin.split_records((b"1" * 200 + b"\r\n") * 2)) == 2


def test_password_hash_roundtrip(monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_N", 2**10)
    h = auth.hash_password("correct horse")
    assert auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong", h)


def test_login_throttle(conn, monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_N", 2**10)
    auth.create_user(conn, "u1", "password-1", ["作成者"])
    auth.create_user(conn, "u2", "password-2", ["作成者"])
    assert config.LOGIN_FAIL_LIMIT == 10 and config.LOGIN_FAIL_WINDOW_SECONDS == 15 * 60   # Microsoft's baseline values
    for _ in range(config.LOGIN_FAIL_LIMIT - 1):
        assert auth.authenticate(conn, "u2", "bad") is None
    assert auth.authenticate(conn, "u2", "password-2") is not None  # one failure short of the limit: still let in
    for _ in range(config.LOGIN_FAIL_LIMIT):
        assert auth.authenticate(conn, "u1", "bad") is None
    assert auth.authenticate(conn, "u1", "password-1") is None  # throttled even with the right password


def test_audit_table_is_append_only(conn):
    audit.log(conn, user_id=None, user_name="x", action="a", outcome=audit.OUTCOME_OK)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_log SET detail = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit_log")


def test_xml_guard_rejects_doctype_and_entity():
    bomb = b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa">]><l>&a;</l>'
    with pytest.raises(safefile.FileRejected):
        safefile.check_xml(bomb)
    safefile.check_xml(b'<?xml version="1.0" encoding="UTF-8"?><a/>')


def test_unknown_user_costs_the_same_scrypt_work_as_a_real_one(conn, monkeypatch):
    import hashlib
    monkeypatch.setattr(auth, "SCRYPT_N", 2**10)
    auth.create_user(conn, "u2", "password-2", ["作成者"])
    seen = []
    real = hashlib.scrypt

    def spy(*a, **kw):
        seen.append((kw["n"], kw["r"], kw["p"]))
        return real(*a, **kw)

    monkeypatch.setattr(hashlib, "scrypt", spy)
    auth.authenticate(conn, "u2", "bad")
    auth.authenticate(conn, "nobody", "bad")
    assert len(seen) == 2 and seen[0] == seen[1]


@pytest.mark.parametrize("name", ['=HYPERLINK("x")', "+1", "-1", "@SUM(A1)", "\tx", "\rx", "\nx",
                                  "＝1+1", "＋1", "－1", "＠x"])
def test_audit_csv_neutralises_formula_cells(conn, name):
    """OWASP CSV Injection lists leading = + - @ tab CR LF and their full-width forms. The single quote in front is the
    author's choice; the page says such techniques are not reliable in Microsoft Excel after saving and re-opening."""
    import csv
    import io
    audit.log(conn, user_id=None, user_name=name, action="login", outcome=audit.OUTCOME_DENIED, detail=name)
    text = audit.to_csv(conn)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][2] == "'" + name and rows[1][7] == "'" + name
    assert text.split("\r\n")[1].startswith('"')      # every field is quoted
