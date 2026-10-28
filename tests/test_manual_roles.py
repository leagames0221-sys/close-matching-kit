import pytest

from close_kit import ingest, manual, matchrun, queries, roles, versions, workbook
from conftest import make_user
from tools import make_samples

# The role table of this repository (the table is the author's design decision; docs/sources.md "Author's design decisions"), written out here cell by cell
# independently of close_kit/roles.py (operation -> roles that may do it)
TABLE = {
    "workbook_load": {"管理者"},
    "ingest": {"作成者", "管理者"},
    "match_run": {"作成者", "管理者"},
    "manual_match": {"作成者"},
    "manual_decide": {"承認者"},
    "view": {"作成者", "承認者", "管理者", "監査人"},
    "audit_csv": {"管理者", "監査人"},
}
ALL_ROLES = ["作成者", "承認者", "管理者", "監査人"]


def test_permission_table_is_the_design_table():
    assert {k: set(v) for k, v in roles.PERMISSIONS.items()} == TABLE


@pytest.fixture()
def world(conn, admin, loaded):
    assert ingest.ingest(conn, admin, "帳簿", "l.csv", make_samples.ledger_csv()).ok
    assert ingest.ingest(conn, admin, "銀行", "b.txt", make_samples.bank_zengin()).ok
    matchrun.run_match(conn, admin, "預金照合")
    return conn


def attempts(conn, user):
    """One attempt of every operation, as plain callables."""
    wb = workbook.parse_workbook(make_samples.workbook())
    return {
        "workbook_load": lambda: versions.save_version(conn, user, wb, make_samples.workbook()),
        "ingest": lambda: ingest.ingest(conn, user, "帳簿", "x.csv", b"x"),
        "match_run": lambda: matchrun.run_match(conn, user, "預金照合"),
        "manual_match": lambda: manual.create_manual(conn, user, "預金照合", [999999], [999999]),
        "manual_decide": lambda: manual.decide(conn, user, 999999, True),
        "view": lambda: queries.overview(conn, user),
        "audit_csv": lambda: queries.audit_csv(conn, user),
    }


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_operation_by_every_role_is_checked_on_the_server(world, role):
    user = make_user(world, "u-" + role, [role])
    for op, call in attempts(world, user).items():
        if role in TABLE[op]:
            try:
                call()
            except roles.PermissionDenied:
                pytest.fail(f"{role} should be allowed to {op}")
            except Exception:
                pass  # allowed; it fails for another reason (made-up ids / content)
        else:
            with pytest.raises(roles.PermissionDenied):
                call()
            row = world.execute("SELECT outcome FROM audit_log WHERE user_id = ? AND action = ? ORDER BY id DESC",
                                (user.id, op)).fetchone()
            assert row["outcome"] == "拒否"


def test_union_of_roles_allows_the_union_of_operations(world):
    both = make_user(world, "both", ["作成者", "承認者"])
    assert roles.allowed(both, "manual_match") and roles.allowed(both, "manual_decide")
    assert not roles.allowed(both, "workbook_load")


# ---- manual match + approval -------------------------------------------------------------------------

def pick(conn, klass, side, text):
    r = conn.execute("SELECT m.txn_id FROM match_rows m JOIN transactions t ON t.id = m.txn_id"
                     " WHERE m.klass = ? AND m.side = ? AND (t.reference = ? OR t.memo LIKE ?)",
                     (klass, side, text, f"%{text}%")).fetchone()
    return r["txn_id"]


def unmatched_pair(conn):
    l = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='L'").fetchone()[0]
    r = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='R'").fetchone()[0]
    return l, r


def test_manual_match_waits_for_another_persons_approval(world, maker, approver):
    l, r = unmatched_pair(world)
    gid = manual.create_manual(world, maker, "預金照合", [l], [r], note="手数料と入金予定を相殺")
    g = world.execute("SELECT * FROM match_groups WHERE id = ?", (gid,)).fetchone()
    assert (g["kind"], g["state"], g["created_by"]) == ("手", "承認待ち", maker.id)
    assert g["diff_minor"] == 7777 - (-1100)
    klasses = {x[0] for x in world.execute("SELECT klass FROM match_rows WHERE group_id = ?", (gid,))}
    assert klasses == {"手"}
    # not done before approval; a second run keeps it as it is
    s = matchrun.run_match(world, make_user(world, "runner", ["作成者"]), "預金照合")
    assert world.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "承認待ち"
    assert manual.decide(world, approver, gid, True) == "差額の処理待ち"   # the two sides do not add up
    assert world.execute("SELECT decided_by FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == approver.id


def test_approval_gives_done_when_the_sides_add_up(world, maker, approver):
    # the two bundle-less rows 12000: candidate (ambiguous) rows: use one left 6 + one right 6 of equal amount
    l = world.execute("SELECT m.txn_id FROM match_rows m JOIN transactions t ON t.id=m.txn_id WHERE m.klass='候補'"
                      " AND m.side='L' AND t.amount_minor=33000").fetchone()[0]
    r = world.execute("SELECT m.txn_id FROM match_rows m JOIN transactions t ON t.id=m.txn_id WHERE m.klass='候補'"
                      " AND m.side='R' AND t.amount_minor=33000").fetchone()[0]
    gid = manual.create_manual(world, maker, "預金照合", [l], [r])
    # the automatic candidate that held these rows is gone
    assert world.execute("SELECT COUNT(*) FROM match_groups WHERE kind='候補' AND reason='候補ルール'").fetchone()[0] == 0
    assert manual.decide(world, approver, gid, True) == "完了"


def test_self_approval_is_refused_even_with_both_roles(world, approver):
    both = make_user(world, "both", ["作成者", "承認者"])
    l, r = unmatched_pair(world)
    gid = manual.create_manual(world, both, "預金照合", [l], [r])
    with pytest.raises(manual.SelfApproval):
        manual.decide(world, both, gid, True)
    with pytest.raises(manual.SelfApproval):
        manual.decide(world, both, gid, False)
    assert world.execute("SELECT state FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == "承認待ち"
    log = world.execute("SELECT outcome, detail FROM audit_log WHERE action='manual_decide' ORDER BY id DESC").fetchone()
    assert log["outcome"] == "拒否" and "本人" in log["detail"]
    assert manual.decide(world, approver, gid, True) in ("完了", "差額の処理待ち")


def test_reject_returns_the_rows_to_candidates(world, maker, approver):
    l, r = unmatched_pair(world)
    gid = manual.create_manual(world, maker, "預金照合", [l], [r])
    assert manual.decide(world, approver, gid, False) == "確認待ち"
    assert {x[0] for x in world.execute("SELECT klass FROM match_rows WHERE group_id = ?", (gid,))} == {"候補"}
    with pytest.raises(manual.ManualError):
        manual.decide(world, approver, gid, True)    # already decided
    # a re-run fixes only 完了 / 差額の処理待ち / 承認待ち: the rejected group is recomputed like any candidate
    matchrun.run_match(world, make_user(world, "runner", ["作成者"]), "預金照合")
    assert world.execute("SELECT COUNT(*) FROM match_groups WHERE id = ?", (gid,)).fetchone()[0] == 0
    assert {x[0] for x in world.execute("SELECT klass FROM match_rows WHERE txn_id IN (?, ?)", (l, r))} == {"未照合"}
    log = world.execute("SELECT detail FROM audit_log WHERE action='manual_decide' AND outcome='成功'"
                        " ORDER BY id DESC").fetchone()
    assert f"グループ {gid}: 却下" in log["detail"]          # the rejection stays in the operation log


def test_manual_match_refuses_rows_that_are_already_done(world, maker):
    done = world.execute("SELECT txn_id FROM match_rows WHERE klass='自動' AND side='L'").fetchone()[0]
    r = world.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='R'").fetchone()[0]
    with pytest.raises(manual.ManualError, match="手の照合には使えない"):
        manual.create_manual(world, maker, "預金照合", [done], [r])
    with pytest.raises(manual.ManualError):
        manual.create_manual(world, maker, "預金照合", [], [r])
    with pytest.raises(manual.ManualError, match="左"):
        manual.create_manual(world, maker, "預金照合", [r], [r])    # right row given as a left row
