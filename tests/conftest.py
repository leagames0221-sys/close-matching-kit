import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from close_kit import auth, db, versions, workbook
from close_kit.roles import User
from tools import make_samples


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    """The production cost (N=2**17) makes the tests slow; the table of parameters is tested elsewhere."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2**10)


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite3")
    yield c
    c.close()


def make_user(conn, name, roles):
    uid = auth.create_user(conn, name, "password-" + name, list(roles))
    return User(id=uid, name=name, roles=frozenset(roles))


@pytest.fixture()
def admin(conn):
    return make_user(conn, "admin1", ["管理者", "作成者"])


@pytest.fixture()
def maker(conn):
    return make_user(conn, "maker1", ["作成者"])


@pytest.fixture()
def approver(conn):
    return make_user(conn, "approver1", ["承認者"])


def load_workbook_into(conn, user, data: bytes | None = None) -> int:
    data = data if data is not None else make_samples.workbook()
    res = workbook.parse_workbook(data)
    assert res.ok, [str(e) for e in res.errors]
    return versions.save_version(conn, user, res, data)


@pytest.fixture()
def loaded(conn, admin):
    """A database with the sample workbook loaded into sandbox."""
    return load_workbook_into(conn, admin)


# ---- fixtures of the second release (month-end sample) --------------------------------------------------------
import types  # noqa: E402

from close_kit import ingest, matchrun  # noqa: E402


@pytest.fixture()
def users(conn):
    return types.SimpleNamespace(
        admin=make_user(conn, "demo-admin", ["管理者"]), admin2=make_user(conn, "demo-admin2", ["管理者"]),
        maker=make_user(conn, "demo-maker", ["作成者"]),
        approver=make_user(conn, "demo-approver", ["承認者"]), approver2=make_user(conn, "demo-approver2", ["承認者"]),
        auditor=make_user(conn, "demo-auditor", ["監査人"]))


@pytest.fixture()
def oct_world(conn, users):
    """The closing sample with only the October files imported and matched."""
    load_workbook_into(conn, users.admin, make_samples.close_workbook())
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct()).ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct()).ok
    matchrun.run_match(conn, users.admin, "預金照合")
    return users


# ---- fixtures of the change requests: production gets its version the only way there is --------------------------------
@pytest.fixture()
def prod_empty(conn, users):
    """The closing sample's workbook in production (through a change request); nothing imported yet."""
    from close_helpers import put_in_production
    put_in_production(conn, make_samples.close_workbook(), users.admin, users.admin2, users.approver)
    return users


@pytest.fixture()
def oct_prod(conn, prod_empty):
    """The same as `oct_world`, but in production: the October files imported and matched there."""
    users = prod_empty
    assert ingest.ingest(conn, users.admin, "帳簿", "l.csv", make_samples.close_ledger_oct(), "production").ok
    assert ingest.ingest(conn, users.admin, "銀行", "b.txt", make_samples.close_bank_oct(), "production").ok
    matchrun.run_match(conn, users.admin, "預金照合", "production")
    return users
