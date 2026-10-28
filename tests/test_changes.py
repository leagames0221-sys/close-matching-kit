"""Change requests (moving a settings version into production): every state, every person rule, the concurrent request,
the sha256 of the tested version, closed periods, rollback, the append-only tables, and the people who make users."""
import json
import sqlite3

import pytest

from close_kit import changeinfo, changes, config, db, roles, versions
from conftest import load_workbook_into, make_user
from close_helpers import PROD, put_in_production
from tools import make_samples, sample_builders as sb

WB = make_samples.close_workbook


def sheets_with(**changes_):
    s = make_samples.close_sheets()
    for k, v in changes_.items():
        s[k] = v
    return s


def draft(conn, users, wb: bytes | None = None, *, title="設定の変更", reason="月次の見直し", **kw) -> int:
    """Load a workbook into the sandbox as the requester and make the draft of the request."""
    vid = load_workbook_into(conn, users.admin, wb or WB())
    return changes.create_draft(conn, users.admin, title=title, to_version_id=vid, justification=reason, **kw)


def to_state(conn, users, rid: int, state: str) -> None:
    """Walk a request (made by demo-admin) up to `state`."""
    steps = [config.CR_WAIT_PRE, config.CR_TESTED, config.CR_RESULT_OK, config.CR_APPLIED]
    if state == config.CR_DRAFT:
        return
    changes.submit(conn, users.admin, rid)
    if state == config.CR_WAIT_PRE:
        return
    changes.pre_decide(conn, users.admin2, rid, True)                      # the test runs by itself
    if state == config.CR_TESTED:
        return
    changes.decide_result(conn, users.approver, rid, True)
    changes.decide_result(conn, users.admin2, rid, True)
    if state == config.CR_RESULT_OK:
        return
    changes.apply(conn, users.admin2, rid)
    assert state in steps


def prod_version(conn):
    return versions.env_version_id(conn, PROD)


# ---- the whole way, and what it leaves ---------------------------------------------------------------------------------------

def test_the_first_request_goes_through_every_step_and_leaves_who_when_in_which_role(conn, users):
    vid = load_workbook_into(conn, users.admin, WB())
    rid = changes.create_draft(conn, users.admin, title="最初の本番", to_version_id=vid, justification="本番を始める")
    assert changes.state_of(conn, rid) == config.CR_DRAFT and prod_version(conn) is None
    changes.submit(conn, users.admin, rid)
    assert changes.pre_decide(conn, users.admin2, rid, True) == config.CR_TESTED       # the test ran by itself
    assert changes.state_of(conn, rid) == config.CR_TESTED
    assert changes.decide_result(conn, users.approver, rid, True) == config.CR_TESTED  # one of the two people
    assert changes.decide_result(conn, users.admin2, rid, True) == config.CR_RESULT_OK
    assert prod_version(conn) is None                                                  # nothing moved yet
    assert changes.apply(conn, users.admin2, rid) == config.CR_APPLIED and prod_version(conn) == vid
    assert changes.confirm(conn, users.admin, rid, True) == config.CR_CONFIRMED
    ev = [(e["action"], e["to_state"], e["user_name"], e["roles"], e["env"]) for e in conn.execute(
        "SELECT * FROM change_request_events WHERE request_id = ? ORDER BY id", (rid,))]
    assert ev == [("作成", "下書き", "demo-admin", "管理者", "production"),
                  ("申請", "事前承認待ち", "demo-admin", "管理者", "production"),
                  ("事前承認", "試験待ち", "demo-admin2", "管理者", "production"),
                  ("試験", "試験済み", "demo-admin2", "管理者", "使い捨ての DB"),
                  ("結果の承認", "試験済み", "demo-approver", "承認者", "production"),
                  ("結果の承認", "結果の承認済み", "demo-admin2", "管理者", "production"),
                  ("適用", "本番に適用済み", "demo-admin2", "管理者", "production"),
                  ("確認", "確認済み", "demo-admin", "管理者", "production")]
    log = conn.execute("SELECT env, version_id, change_request_id, changed_by FROM environment_log"
                       " WHERE env = 'production'").fetchall()
    assert [tuple(r) for r in log] == [("production", vid, rid, users.admin2.id)]       # which request moved the pointer
    d = changes.detail(conn, users.auditor, rid)
    assert d["state"] == "確認済み" and d["enough"] and [a["user_name"] for a in d["approvals"]] == \
        ["demo-approver", "demo-admin2"]


def test_what_the_form_fills_by_itself_is_fixed_when_it_is_put_forward(conn, users):
    rid = draft(conn, users)
    d0 = changes.detail(conn, users.auditor, rid)
    assert d0["diff"] is None and d0["req"]["status"] == "下書き"
    changes.submit(conn, users.admin, rid)
    d = changes.detail(conn, users.auditor, rid)
    sheets = changeinfo.changed_sheets(d["diff"])
    assert "承認ルート" in sheets and "作業一覧" in sheets and all(x["change"] == "追加" for x in d["diff"])  # all of it
    assert d["security"]["any"] and d["impact"]["match_sets"][0]["name"] == "預金照合"
    assert d["req"]["urgency"] == "Scheduled" and d["req"]["from_version_id"] is None
    assert d["req"]["rollback_version_id"] is None                                    # nothing to go back to
    with pytest.raises(sqlite3.IntegrityError):                                       # the row is frozen now
        conn.execute("UPDATE change_requests SET title = 'x' WHERE id = ?", (rid,))


# ---- the requester decides nothing ----------------------------------------------------------------------------------------

def test_the_requester_cannot_approve_reject_or_apply_whatever_roles_he_has(conn, users):
    both = make_user(conn, "all-roles", ["管理者", "承認者", "作成者"])
    vid = load_workbook_into(conn, both, WB())
    rid = changes.create_draft(conn, both, title="t", to_version_id=vid, justification="r")
    changes.submit(conn, both, rid)
    with pytest.raises(changes.NotAllowedToChange):
        changes.pre_decide(conn, both, rid, True)
    with pytest.raises(changes.NotAllowedToChange):
        changes.pre_decide(conn, both, rid, False, "no")
    changes.pre_decide(conn, users.admin2, rid, True)
    for approve in (True, False):
        with pytest.raises(changes.NotAllowedToChange):
            changes.decide_result(conn, both, rid, approve, "no")
    changes.decide_result(conn, users.approver, rid, True)
    changes.decide_result(conn, users.admin2, rid, True)
    with pytest.raises(changes.NotAllowedToChange):
        changes.apply(conn, both, rid)
    assert changes.state_of(conn, rid) == config.CR_RESULT_OK and prod_version(conn) is None
    denied = conn.execute("SELECT COUNT(*) FROM audit_log WHERE user_id = ? AND outcome = '拒否'"
                          " AND action LIKE 'change_%'", (both.id,)).fetchone()[0]
    assert denied == 5                                                                # every refusal is in the log
    changes.apply(conn, users.admin2, rid)
    with pytest.raises(changes.NotAllowedToChange):                                   # only the requester confirms
        changes.confirm(conn, users.admin2, rid, True)
    assert changes.confirm(conn, both, rid, True) == config.CR_CONFIRMED


def test_the_pre_approval_is_for_an_administrator_who_is_not_the_requester(conn, users):
    rid = draft(conn, users)
    changes.submit(conn, users.admin, rid)
    for who in (users.maker, users.approver, users.auditor):
        with pytest.raises(roles.PermissionDenied):
            changes.pre_decide(conn, who, rid, True)
    with pytest.raises(changes.ChangeError):
        changes.pre_decide(conn, users.admin2, rid, False, "")                        # a rejection needs its reason
    assert changes.pre_decide(conn, users.admin2, rid, False, "今期は見送る") == config.CR_REJECTED
    assert conn.execute("SELECT COUNT(*) FROM change_tests").fetchone()[0] == 0       # no test for a rejected request
    with pytest.raises(changes.ChangeError):
        changes.pre_decide(conn, users.admin2, rid, True)                              # it is closed


# ---- the result needs two people ------------------------------------------------------------------------------------------

def test_the_result_needs_an_approver_and_an_administrator_who_are_two_people(conn, users):
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_TESTED)
    assert changes.decide_result(conn, users.approver, rid, True) == config.CR_TESTED
    with pytest.raises(changes.ChangeError):                                           # the same person twice
        changes.decide_result(conn, users.approver, rid, True)
    assert changes.decide_result(conn, users.approver2, rid, True) == config.CR_TESTED  # two approvers: still no administrator
    with pytest.raises(changes.ChangeError):
        changes.apply(conn, users.admin2, rid)                                         # not at that state yet
    assert changes.decide_result(conn, users.admin2, rid, True) == config.CR_RESULT_OK


def test_two_administrators_without_an_approver_are_not_enough(conn, users):
    third = make_user(conn, "demo-admin3", ["管理者"])
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_TESTED)
    assert changes.decide_result(conn, users.admin2, rid, True) == config.CR_TESTED
    assert changes.decide_result(conn, third, rid, True) == config.CR_TESTED
    assert not changes.detail(conn, users.auditor, rid)["enough"]


def test_one_person_with_both_roles_counts_once(conn, users):
    both = make_user(conn, "both-roles", ["管理者", "承認者"])
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_TESTED)
    assert changes.decide_result(conn, both, rid, True) == config.CR_TESTED            # one person: not enough
    with pytest.raises(changes.ChangeError):
        changes.decide_result(conn, both, rid, True)
    assert changes.state_of(conn, rid) == config.CR_TESTED
    assert changes.decide_result(conn, users.admin2, rid, True) == config.CR_RESULT_OK  # he is the approver, admin2 the administrator
    assert changes.enough_approvals([(1, frozenset({"管理者", "承認者"}))]) is False
    assert changes.enough_approvals([(1, frozenset({"管理者", "承認者"})), (2, frozenset({"承認者"}))]) is True
    assert changes.enough_approvals([(1, frozenset({"承認者"})), (2, frozenset({"承認者"}))]) is False
    assert changes.enough_approvals([(1, frozenset({"管理者"})), (2, frozenset({"管理者"}))]) is False


def test_a_new_test_voids_the_approvals_given_to_the_earlier_result(conn, users):
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_RESULT_OK)
    first = changes.latest_test(conn, rid)
    assert len(changes.approvals_of(conn, first["id"])) == 2
    assert changes.retest(conn, users.admin2, rid) == config.CR_TESTED
    second = changes.latest_test(conn, rid)
    assert second["id"] != first["id"] and changes.approvals_of(conn, second["id"]) == []
    assert changes.state_of(conn, rid) == config.CR_TESTED
    with pytest.raises(changes.ChangeError):
        changes.apply(conn, users.admin2, rid)                                         # the old approvals do not count
    changes.decide_result(conn, users.approver, rid, True)
    assert changes.state_of(conn, rid) == config.CR_TESTED
    changes.decide_result(conn, users.admin2, rid, True)
    assert changes.state_of(conn, rid) == config.CR_RESULT_OK
    assert changes.detail(conn, users.auditor, rid)["test"]["id"] == second["id"]


def test_either_kind_of_person_can_reject_the_result_and_the_request_is_closed(conn, users):
    for who in (users.approver, users.admin2):
        rid = draft(conn, users)
        to_state(conn, users, rid, config.CR_TESTED)
        with pytest.raises(changes.ChangeError):
            changes.decide_result(conn, who, rid, False, "")
        assert changes.decide_result(conn, who, rid, False, "影響が大きい") == config.CR_REJECTED
        with pytest.raises(changes.ChangeError):
            changes.apply(conn, users.admin2, rid)
    for who in (users.maker, users.auditor):
        rid = draft(conn, users)
        to_state(conn, users, rid, config.CR_TESTED)
        with pytest.raises(roles.PermissionDenied):
            changes.decide_result(conn, who, rid, True)


def test_a_request_can_be_withdrawn_by_its_requester_until_it_is_applied(conn, users):
    states = [config.CR_DRAFT, config.CR_WAIT_PRE, config.CR_TESTED, config.CR_RESULT_OK]
    for st in states:
        rid = draft(conn, users)
        to_state(conn, users, rid, st)
        with pytest.raises(changes.NotAllowedToChange):
            changes.withdraw(conn, users.admin2, rid)                                   # not his request
        assert changes.withdraw(conn, users.admin, rid, "取り下げる") == config.CR_WITHDRAWN
        assert prod_version(conn) is None
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_APPLIED)
    with pytest.raises(changes.ChangeError):
        changes.withdraw(conn, users.admin, rid)                                         # applied: too late
    assert prod_version(conn) is not None


def test_a_change_that_cannot_be_confirmed_stays_open_and_a_rollback_draft_can_be_made(conn, users):
    base = put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    s = make_samples.close_sheets()
    s["説明"][0][1] = "名前を変えた会社（架空）"
    rid = draft(conn, users, sb.build_workbook(s))
    to_state(conn, users, rid, config.CR_APPLIED)
    with pytest.raises(changes.ChangeError):
        changes.confirm(conn, users.admin, rid, False, "")                               # the reason is needed
    assert changes.confirm(conn, users.admin, rid, False, "画面で確かめられなかった") == config.CR_CANNOT_CONFIRM
    assert changes.state_of(conn, rid) not in config.CR_CLOSED
    back = changes.draft_rollback_of(conn, users.admin, rid, "確認できなかったので戻す")
    r = changes.detail(conn, users.auditor, back)["req"]
    assert r["kind"] == "戻し" and r["rollback_of"] == rid and r["rollback_version_id"] == prod_version(conn)
    # and a request in another state has no rollback draft
    other = draft(conn, users)
    with pytest.raises(changes.ChangeError):
        changes.draft_rollback_of(conn, users.admin, other, "x")
    assert base  # (the version that was in production before)


# ---- two requests at once ---------------------------------------------------------------------------------------------------

def test_the_later_of_two_requests_tested_against_the_same_version_is_refused_at_apply(conn, users):
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    v0 = prod_version(conn)
    s1 = make_samples.close_sheets()
    s1["説明"][0][1] = "A社（架空）"
    s2 = make_samples.close_sheets()
    s2["説明"][0][1] = "B社（架空）"
    r1, r2 = draft(conn, users, sb.build_workbook(s1)), draft(conn, users, sb.build_workbook(s2))
    to_state(conn, users, r1, config.CR_RESULT_OK)
    to_state(conn, users, r2, config.CR_RESULT_OK)                                       # both were tested against v0
    assert changes.latest_test(conn, r1)["base_version_id"] == changes.latest_test(conn, r2)["base_version_id"] == v0
    changes.apply(conn, users.admin2, r1)
    v1 = prod_version(conn)
    assert v1 != v0
    log_before = conn.execute("SELECT COUNT(*) FROM environment_log").fetchone()[0]
    with pytest.raises(changes.StaleTest):
        changes.apply(conn, users.admin2, r2)
    assert prod_version(conn) == v1                                                      # r1's change is not overwritten
    assert conn.execute("SELECT COUNT(*) FROM environment_log").fetchone()[0] == log_before
    assert changes.state_of(conn, r2) == config.CR_WAIT_TEST                             # back to the test
    assert changes.detail(conn, users.auditor, r2)["events"][-1]["action"] == "適用の拒否"
    changes.retest(conn, users.admin2, r2)
    assert changes.latest_test(conn, r2)["base_version_id"] == v1                        # tested against what is there now
    changes.decide_result(conn, users.approver, r2, True)
    changes.decide_result(conn, users.admin2, r2, True)
    changes.apply(conn, users.admin2, r2)
    assert prod_version(conn) != v1


def test_the_move_of_the_pointer_is_one_conditional_update(conn, users):
    vid = load_workbook_into(conn, users.admin, WB())
    other = load_workbook_into(conn, users.admin, WB())
    with conn:
        assert versions.point_environment(conn, PROD, vid, users.admin2.id, expect=None) is True
    with conn:
        assert versions.point_environment(conn, PROD, other, users.admin2.id, expect=None) is False   # it holds vid now
    assert prod_version(conn) == vid
    with conn:
        assert versions.point_environment(conn, PROD, other, users.admin2.id, expect=vid, change_request_id=7)
    assert [tuple(r) for r in conn.execute("SELECT version_id, change_request_id FROM environment_log"
                                           " WHERE env = 'production' ORDER BY id")] == [(vid, None), (other, 7)]


# ---- the version that was tested is the version that is applied -----------------------------------------------------------------

def test_a_version_whose_sha256_differs_from_the_tested_one_is_not_applied(conn, users):
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_RESULT_OK)
    s = make_samples.close_sheets()
    s["説明"][0][1] = "別の中身（架空）"
    other = load_workbook_into(conn, users.admin, sb.build_workbook(s))                    # a version that was never tested
    before = prod_version(conn)
    with pytest.raises(changes.ChangeError) as e:
        changes.apply(conn, users.admin2, rid, version_id=other)
    assert "sha256" in str(e.value) and prod_version(conn) == before
    assert changes.state_of(conn, rid) == config.CR_RESULT_OK
    row = conn.execute("SELECT detail FROM audit_log WHERE action = 'change_apply' AND outcome = '拒否'").fetchone()
    assert "sha256" in row["detail"]


def test_the_sha256_is_computed_again_from_the_stored_text_at_apply(conn, users):
    """Someone who rewrites the stored text (the table refuses it, so the trigger is dropped first, as the owner of the
    file could) is caught: the value is not read from the column but computed from the text."""
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_RESULT_OK)
    vid = conn.execute("SELECT to_version_id FROM change_requests WHERE id = ?", (rid,)).fetchone()[0]
    conn.execute("DROP TRIGGER config_versions_no_update")
    tampered = json.loads(conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (vid,)).fetchone()[0])
    tampered["description"]["company"] = "書き換えた会社"
    conn.execute("UPDATE config_versions SET normalized_json = ? WHERE id = ?", (json.dumps(tampered, ensure_ascii=False), vid))
    conn.commit()
    with pytest.raises(changes.ChangeError) as e:
        changes.apply(conn, users.admin2, rid)
    assert "sha256" in str(e.value) and prod_version(conn) is None


def test_a_version_cannot_be_applied_under_another_number_even_if_the_text_is_the_same(conn, users):
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_RESULT_OK)
    t = changes.latest_test(conn, rid)
    same = load_workbook_into(conn, users.admin, WB())                                      # same content, other number
    texts = [conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (v,)).fetchone()[0]
             for v in (same, t["to_version_id"])]
    assert same != t["to_version_id"] and texts[0] == texts[1]                               # the sha256 cannot tell them apart
    with pytest.raises(changes.ChangeError) as e:
        changes.apply(conn, users.admin2, rid, version_id=same)
    assert "申請の版" in str(e.value) and prod_version(conn) is None
    assert changes.apply(conn, users.admin2, rid, version_id=t["to_version_id"]) == config.CR_APPLIED


# ---- closed periods: the base is production ------------------------------------------------------------------------------------

def test_a_version_that_opens_a_period_production_closed_is_refused_when_asked_and_when_applied(conn, users):
    s_open = make_samples.close_sheets()
    s_open["会計期間"][0][3] = "いいえ"                                    # 2026-09 open
    old_open = load_workbook_into(conn, users.admin, sb.build_workbook(s_open))     # sandbox: 2026-09 open
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)       # production: 2026-09 closed
    with pytest.raises(changes.ChangeError) as e:                                   # asked for an old version number
        changes.create_draft(conn, users.admin, title="t", to_version_id=old_open, justification="r")
    assert "締めた期間" in str(e.value) and "2026-09" in str(e.value)
    assert conn.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0] == 1   # only the first request exists
    # a request that was fine when it was made, and then production closes a period
    s_a = make_samples.close_sheets()
    s_a["説明"][0][1] = "A（架空）"
    s_a["会計期間"][1][3] = "いいえ"
    ra = draft(conn, users, sb.build_workbook(s_a))
    to_state(conn, users, ra, config.CR_RESULT_OK)
    s_close = make_samples.close_sheets()
    s_close["会計期間"][1][3] = "はい"                                                # 2026-10 closed (month-end closing)
    rb = draft(conn, users, sb.build_workbook(s_close), title="10 月を締める")
    to_state(conn, users, rb, config.CR_APPLIED)
    with pytest.raises(changes.ChangeError) as e2:                                   # ra would open 2026-10 again
        changes.apply(conn, users.admin2, ra)
    assert "締めた期間" in str(e2.value) and "2026-10" in str(e2.value)
    assert changes.state_of(conn, ra) == config.CR_RESULT_OK


def test_the_check_of_the_workbook_load_still_compares_with_the_sandbox_only(conn, users):
    """Loading a workbook is the sandbox's business: it is refused against the sandbox's version, not production's."""
    s_open = make_samples.close_sheets()
    s_open["会計期間"][0][3] = "いいえ"
    old_open = load_workbook_into(conn, users.admin, sb.build_workbook(s_open))
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    s = make_samples.close_sheets()
    s["会計期間"][0][3] = "いいえ"
    with pytest.raises(versions.VersionRejected):                                     # sandbox now holds the closed version
        load_workbook_into(conn, users.admin, sb.build_workbook(s))
    assert old_open


# ---- rollback ----------------------------------------------------------------------------------------------------------------------

def test_the_rollback_version_has_the_closing_of_production_added(conn, users):
    v1 = put_in_production(conn, WB(), users.admin, users.admin2, users.approver)         # 2026-09 closed
    s2 = make_samples.close_sheets()
    s2["会計期間"][1][3] = "はい"                                                          # month-end: 2026-10 closed as well
    rid = draft(conn, users, sb.build_workbook(s2), title="10 月を締める")
    to_state(conn, users, rid, config.CR_APPLIED)
    changes.confirm(conn, users.admin, rid, True)
    v2 = prod_version(conn)
    assert {p["name"] for p in versions.load_config(conn, PROD)[1]["periods"] if p["closed"]} == {"2026-09", "2026-10"}
    back = changes.create_rollback(conn, users.admin, v1, justification="前の設定に戻す")
    r = changes.detail(conn, users.auditor, back)["req"]
    assert r["kind"] == "戻し" and r["to_version_id"] not in (v1, v2)                       # a new version, not v1 itself
    new_cfg = json.loads(conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (r["to_version_id"],)).fetchone()[0])
    assert {p["name"] for p in new_cfg["periods"] if p["closed"]} == {"2026-09", "2026-10"}  # putting v1 back as it was would open 2026-10
    old = json.loads(conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (v1,)).fetchone()[0])
    assert [p["closed"] for p in old["periods"]] == [True, False, False]                     # v1 itself is untouched
    assert r["rollback_version_id"] == v2
    changes.submit(conn, users.admin, back)                                                # the same flow as any request
    changes.pre_decide(conn, users.admin2, back, True)
    changes.decide_result(conn, users.approver, back, True)
    changes.decide_result(conn, users.admin2, back, True)
    changes.apply(conn, users.admin2, back)
    assert prod_version(conn) == r["to_version_id"]
    assert {p["name"] for p in versions.load_config(conn, PROD)[1]["periods"] if p["closed"]} == {"2026-09", "2026-10"}


def test_a_rollback_to_a_version_of_the_old_schema_is_refused(conn, users):
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    old = {"description": {"schema_version": "1"}, "periods": [], "accounts": [], "sources": [], "match_sets": [],
           "reason_codes": [], "pass_rules": []}
    with conn:
        cur = conn.execute("INSERT INTO config_versions(schema_version, normalized_json, sha256, source_bytes, loaded_by,"
                           " loaded_at) VALUES ('1', ?, 'h', x'00', ?, 'now')", (json.dumps(old), users.admin.id))
    with pytest.raises(changes.ChangeError) as e:
        changes.create_rollback(conn, users.admin, cur.lastrowid, justification="x")
    assert "schema" in str(e.value)
    with pytest.raises(changes.ChangeError):
        changes.create_draft(conn, users.admin, title="t", to_version_id=cur.lastrowid, justification="r")
    with pytest.raises(changes.ChangeError):                                              # nothing in production: nothing to go back from
        c2 = db.connect(":memory:")
        try:
            u = make_user(c2, "a", ["管理者"])
            changes.create_rollback(c2, u, 1, justification="x")
        finally:
            c2.close()


def test_loading_a_workbook_never_changes_production(conn, users):
    v1 = put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    log = conn.execute("SELECT COUNT(*) FROM environment_log WHERE env = 'production'").fetchone()[0]
    s = make_samples.close_sheets()
    s["説明"][0][1] = "読み込むだけの会社（架空）"
    v2 = load_workbook_into(conn, users.admin, sb.build_workbook(s))
    assert prod_version(conn) == v1 and versions.env_version_id(conn, "sandbox") == v2
    assert conn.execute("SELECT COUNT(*) FROM environment_log WHERE env = 'production'").fetchone()[0] == log
    with pytest.raises(ValueError):                                                       # and the function itself refuses
        versions.save_version(conn, users.admin, __import__("close_kit.workbook", fromlist=["x"]).parse_workbook(WB()), WB(), env=PROD)
    assert prod_version(conn) == v1


# ---- what the database itself refuses --------------------------------------------------------------------------------------------

def test_the_rows_that_may_not_change_are_refused_by_the_database(conn, users):
    rid = draft(conn, users)
    conn.execute("UPDATE change_requests SET title = 'まだ下書き' WHERE id = ?", (rid,))      # a draft can be changed
    to_state(conn, users, rid, config.CR_TESTED)
    for sql in ("UPDATE change_requests SET title = 'x'", "UPDATE change_requests SET status = '下書き'",
                "DELETE FROM change_requests", "UPDATE change_request_events SET reason = 'x'",
                "DELETE FROM change_request_events", "UPDATE change_tests SET result_status = '成功'",
                "DELETE FROM change_tests", "UPDATE change_approvals SET user_name = 'x'", "DELETE FROM change_approvals"):
        conn.execute("INSERT OR IGNORE INTO change_approvals(request_id, test_id, user_id, user_name, roles, approved_at)"
                     " VALUES (?, ?, ?, 'x', '承認者', 'now')", (rid, changes.latest_test(conn, rid)["id"], users.maker.id))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)


def test_the_third_migration_is_numbered_and_running_it_twice_changes_nothing(tmp_path):
    c = db.connect(tmp_path / "m.sqlite3")
    assert [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4]
    cols = [r[1] for r in c.execute("PRAGMA table_info(environment_log)")]
    assert "change_request_id" in cols
    before = [tuple(r) for r in c.execute("SELECT name, sql FROM sqlite_master ORDER BY name")]
    db.migrate(c)
    db.migrate(c)
    assert [tuple(r) for r in c.execute("SELECT name, sql FROM sqlite_master ORDER BY name")] == before
    for t in ("change_requests", "change_request_events", "change_tests", "change_approvals"):
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (t,)).fetchone()[0] == 1
    c.close()


def test_a_database_of_the_second_release_gets_the_new_tables_and_keeps_its_rows(tmp_path):
    p = tmp_path / "old.sqlite3"
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA)
    c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    c.execute("INSERT INTO users(name, pw_hash, roles, created_at) VALUES ('u', 'h', '管理者', 'now')")
    c.execute("INSERT INTO schema_migrations VALUES (1, 'now')")
    c.commit()
    db.migrate(c)
    c.execute("DELETE FROM schema_migrations WHERE version = 3")                          # as if migration 3 had not run
    c.execute("DROP TABLE change_approvals")
    c.execute("DROP TABLE change_tests")
    c.execute("DROP TABLE change_request_events")
    c.execute("DROP TABLE change_requests")
    c.commit()
    db.migrate(c)
    assert c.execute("SELECT name FROM users").fetchone()[0] == "u"
    assert c.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0] == 0
    assert [r[0] for r in c.execute("SELECT version FROM schema_migrations ORDER BY version")] == [1, 2, 3, 4]
    c.close()


# ---- people: who made the users, who was made after the request, who gets a wider range ----------------------------------------------

def test_adduser_and_seed_demo_write_the_os_user_to_the_operation_log(tmp_path, monkeypatch):
    import getpass

    from close_kit import __main__ as cli
    monkeypatch.setattr(config, "DEFAULT_DB", tmp_path / "cli.sqlite3")
    monkeypatch.setattr(config, "VAR_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO_USERS_FILE", tmp_path / "demo_users.txt")
    monkeypatch.setattr(getpass, "getuser", lambda: "pc-account")
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "long-enough-password")
    assert cli.main(["adduser", "someone", "--roles", "作成者,承認者"]) == 0
    assert cli.main(["seed-demo"]) == 0
    assert cli.main(["seed-demo"]) == 0                                                    # only the missing ones, the second time
    c = db.connect(tmp_path / "cli.sqlite3")
    rows = c.execute("SELECT user_name, action, outcome, detail FROM audit_log WHERE action = 'user_create' ORDER BY id").fetchall()
    assert len(rows) == 1 + len(cli.DEMO_USERS) and "demo-admin2" in [n for n, _r in cli.DEMO_USERS]
    assert all(r["user_name"] == "PC の利用者 pc-account" and r["outcome"] == "成功" for r in rows)
    assert "someone" in rows[0]["detail"] and "pc-account" in rows[0]["detail"] and "作成者,承認者" in rows[0]["detail"]
    users = {r["name"]: r["roles"] for r in c.execute("SELECT name, roles FROM users")}
    assert users["demo-admin2"] == "管理者" and len(users) == 1 + len(cli.DEMO_USERS)
    c.close()


def test_the_detail_marks_a_person_who_was_made_after_the_request(conn, users):
    rid = draft(conn, users)
    late = make_user(conn, "late-approver", ["承認者"])
    changes.submit(conn, users.admin, rid)
    changes.pre_decide(conn, users.admin2, rid, True)
    changes.decide_result(conn, late, rid, True)
    changes.decide_result(conn, users.admin2, rid, True)
    d = changes.detail(conn, users.auditor, rid)
    assert d["marks"]["late-approver"]["after_request"] is True
    assert d["marks"]["demo-admin2"]["after_request"] is False and d["marks"]["demo-admin"]["after_request"] is False
    assert d["marks"]["late-approver"]["created_at"]


def test_a_person_whose_range_of_amounts_widens_is_marked_when_he_approves_the_result(conn, users):
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)             # approver: below 100,000 only
    s = make_samples.close_sheets(approvers=("demo-approver", "demo-approver2"), senior=("demo-approver", "demo-approver2"))
    rid = draft(conn, users, sb.build_workbook(s))
    changes.submit(conn, users.admin, rid)
    d = changes.detail(conn, users.auditor, rid)
    assert d["security"]["widened"] == {"demo-approver": ["100,000 以上"]}
    changes.pre_decide(conn, users.admin2, rid, True)
    changes.decide_result(conn, users.approver, rid, True)                                 # demo-approver is the one who widens
    d2 = changes.detail(conn, users.auditor, rid)
    assert d2["widened_among_approvers"] == {"demo-approver": ["100,000 以上"]}
    assert changes.allowed_actions(users.admin2, d2) and "result_approve" in changes.allowed_actions(users.admin2, d2)


def test_the_diff_the_widening_and_the_boundaries_are_computed_from_the_two_settings(conn, users):
    import copy
    base = {"periods": [], "approval_routes": [{"lower": 0, "upper": 1000, "approvers": ["a", "b"]},
                                                {"lower": 1000, "upper": None, "approvers": ["b"]}],
            "tasks": [{"id": "T", "preparer": "p", "reviewer": "r"}], "reason_codes": [], "description": {"company": "X"}}
    new = copy.deepcopy(base)
    new["approval_routes"][1]["approvers"] = ["a", "b"]
    new["approval_routes"][0]["upper"] = 500
    new["approval_routes"].insert(1, {"lower": 500, "upper": 1000, "approvers": ["c"]})
    new["tasks"][0]["reviewer"] = "q"
    diff = changeinfo.diff_configs(base, new)
    sheets = {(d["sheet"], d["key"], d["change"]) for d in diff}
    assert ("承認ルート", "500 以上", "追加") in sheets and ("承認ルート", "0 以上", "変更") in sheets
    assert ("作業一覧", "T", "変更") in sheets and ("承認ルート", "1000 以上", "変更") in sheets
    assert changeinfo.widened_approvers(base, new) == {"a": ["1,000 以上"], "c": ["500 以上 1,000 未満"]}
    assert changeinfo.approvers_at(base, 999) == ["a", "b"] and changeinfo.approvers_at(new, 999) == ["c"]
    assert changeinfo.boundary_amounts(base, new) == [0, 499, 500, 999, 1000]
    sec = changeinfo.security_impact(diff, base, new)
    assert sec["task_people"] == [{"task": "T", "field": "確認者", "old": "r", "new": "q"}] and sec["any"]


def test_the_list_is_a_csv_for_administrators_and_auditors_with_formula_characters_neutralised(conn, users):
    rid = draft(conn, users, title="=HYPERLINK(\"x\")")
    changes.submit(conn, users.admin, rid)
    for who in (users.admin, users.auditor):
        text = changes.to_csv(conn, who)
        assert text.splitlines()[0].startswith('"番号"') and "'=HYPERLINK" in text and '"=HYPERLINK' not in text
    for who in (users.maker, users.approver):
        with pytest.raises(roles.PermissionDenied):
            changes.to_csv(conn, who)
    assert changes.list_requests(conn, users.maker)[0]["id"] == rid and changes.list_requests(conn, users.maker)[0]["state"] == "事前承認待ち"


# ---- the result on the screen, every refusal in the log, the back-out plan ------------------------------------------------------------

def test_an_approval_names_the_result_it_was_given_for_and_an_earlier_one_is_refused(conn, users):
    rid = draft(conn, users)
    to_state(conn, users, rid, config.CR_TESTED)
    first = changes.latest_test(conn, rid)["id"]
    changes.retest(conn, users.admin2, rid)
    second = changes.latest_test(conn, rid)["id"]
    for stale in (first, 0):                                                      # an older result, or none named
        with pytest.raises(changes.NotLatestResult):
            changes.decide_result(conn, users.approver, rid, True, test_id=stale)
        with pytest.raises(changes.NotLatestResult):
            changes.decide_result(conn, users.admin2, rid, False, "古い", test_id=stale)
    assert conn.execute("SELECT COUNT(*) FROM change_approvals").fetchone()[0] == 0
    assert changes.state_of(conn, rid) == config.CR_TESTED
    assert changes.decide_result(conn, users.approver, rid, True, test_id=second) == config.CR_TESTED
    assert changes.decide_result(conn, users.admin2, rid, True, test_id=second) == config.CR_RESULT_OK


def denials(conn, user):
    return [(r["action"], r["detail"]) for r in conn.execute(
        "SELECT action, detail FROM audit_log WHERE user_id = ? AND outcome = '拒否' ORDER BY id", (user.id,))]


def test_every_refused_step_of_a_request_is_a_row_of_the_operation_log_once(conn, users):
    vid = load_workbook_into(conn, users.admin, WB())
    cases = [
        (users.admin, lambda: changes.create_draft(conn, users.admin, title=" ", to_version_id=vid, justification="r"),
         "change_create", "題名が空"),
    ]
    rid = changes.create_draft(conn, users.admin, title="t", to_version_id=vid, justification="r")
    cases += [
        (users.admin2, lambda: changes.apply(conn, users.admin2, rid), "change_apply", "「下書き」で"),     # the state
        (users.admin2, lambda: changes.pre_decide(conn, users.admin2, rid, True), "change_pre_decide", "「下書き」で"),
        (users.admin, lambda: changes.confirm(conn, users.admin, rid, True), "change_confirm", "「下書き」で"),
    ]
    for who, call, action, needle in cases:
        before = denials(conn, who)
        with pytest.raises(changes.ChangeError):
            call()
        new = denials(conn, who)[len(before):]
        assert len(new) == 1 and new[0][0] == action and needle in new[0][1], (action, new)
    changes.submit(conn, users.admin, rid)
    for who, call, action, needle in (
            (users.admin2, lambda: changes.pre_decide(conn, users.admin2, rid, False, ""), "change_pre_decide", "理由"),
            (users.admin, lambda: changes.pre_decide(conn, users.admin, rid, True), "change_pre_decide", "申請者は")):
        before = denials(conn, who)
        with pytest.raises(changes.ChangeError):
            call()
        new = denials(conn, who)[len(before):]
        assert len(new) == 1 and new[0][0] == action and needle in new[0][1], (action, new)    # once, not twice
    changes.pre_decide(conn, users.admin2, rid, True)
    changes.decide_result(conn, users.approver, rid, True)
    before = denials(conn, users.approver)
    with pytest.raises(changes.ChangeError):
        changes.decide_result(conn, users.approver, rid, True)                        # a second approval
    new = denials(conn, users.approver)[len(before):]
    assert len(new) == 1 and "すでに承認" in new[0][1] and f"申請 {rid}" in new[0][1]


def test_the_rollback_draft_of_a_request_goes_to_the_version_of_its_back_out_plan(conn, users):
    def company(name):
        s = make_samples.close_sheets()
        s["説明"][0][1] = name
        return sb.build_workbook(s)

    def name_of(vid):
        return json.loads(conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?",
                                       (vid,)).fetchone()[0])["description"]["company"]
    v1 = put_in_production(conn, company("戻し先に決めた会社（架空）"), users.admin, users.admin2, users.approver)
    put_in_production(conn, company("今の本番の会社（架空）"), users.admin, users.admin2, users.approver)
    rid = draft(conn, users, company("新しい会社（架空）"), rollback_version_id=v1)        # the plan names v1, not the version now
    to_state(conn, users, rid, config.CR_APPLIED)
    changes.confirm(conn, users.admin, rid, False, "画面で確かめられなかった")
    back = changes.draft_rollback_of(conn, users.admin, rid, "計画どおりに戻す")
    target = changes.detail(conn, users.auditor, back)["req"]["to_version_id"]
    assert name_of(target) == "戻し先に決めた会社（架空）"
    # without a plan, the default is the version in production when the request was put forward
    rid2 = draft(conn, users, company("次の会社（架空）"))
    to_state(conn, users, rid2, config.CR_APPLIED)
    from_v = changes.detail(conn, users.auditor, rid2)["req"]["from_version_id"]
    changes.confirm(conn, users.admin, rid2, False, "確かめられなかった")
    back2 = changes.draft_rollback_of(conn, users.admin, rid2, "前に戻す")
    assert name_of(changes.detail(conn, users.auditor, back2)["req"]["to_version_id"]) == name_of(from_v)


# ---- how the texts of a request read: no Python None, and the columns under the headers the person wrote ----------------------

def test_the_first_request_says_no_version_not_none_in_its_history_and_in_the_log(conn, users):
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    first = conn.execute("SELECT id FROM change_requests").fetchone()[0]
    ev = {e["action"]: e["reason"] for e in conn.execute("SELECT * FROM change_request_events WHERE request_id = ?",
                                                         (first,))}
    assert ev["適用"] == "本番の版: 版なし -> 版 1"
    log = conn.execute("SELECT detail FROM audit_log WHERE action = 'change_apply' AND outcome <> '拒否'").fetchone()[0]
    assert log.endswith("本番を版 1 にした（試験の時の本番: 版なし）")
    s = make_samples.close_sheets()
    s["パスルール"][0][7] = 0
    rid = draft(conn, users, sb.build_workbook(s), title="許容差")
    to_state(conn, users, rid, config.CR_APPLIED)
    reason = conn.execute("SELECT reason FROM change_request_events WHERE request_id = ? AND action = '適用'",
                          (rid,)).fetchone()[0]
    assert reason == "本番の版: 版 1 -> 版 2"
    for table, col in (("change_request_events", "reason"), ("audit_log", "detail")):
        assert not [r[0] for r in conn.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE '%None%'")], table


def test_the_changed_rows_name_each_column_as_the_header_of_its_sheet(conn, users):
    """Every internal field of every sheet is shown under the header of that column in the workbook, so that a person
    who wrote 期間の名前 / 勘定コード / 種類 reads the same word on the screen. (pass rules: the headers are the
    English words date_window and amount_tolerance_minor in the workbook itself.)"""
    from close_kit import workbook
    cfg = workbook.parse_workbook(WB()).config
    heads = {"description": workbook.SHEETS["説明"]}
    for key, (sheet, _ident) in changeinfo.SHEETS.items():
        heads[key] = workbook.SHEETS[sheet]
    heads = {k: {c[0] for c in v} for k, v in heads.items()}
    for key, labels in changeinfo.FIELD_LABELS.items():
        rows = [cfg[key]] if key == "description" else cfg[key]
        assert rows, key
        row = rows[0]
        seen: set[str] = set()
        for field in changeinfo._flat(row):
            other = {**changeinfo._flat(row)}
            other[field] = "x" if not isinstance(row.get(field), (bool, int)) else (not other[field])
            got = changeinfo._row_changes(changeinfo._flat(row), other, labels)
            assert len(got) == 1, (key, field)
            seen.add(got[0]["field"])
        assert seen == heads[key], (key, seen ^ heads[key])


def test_a_changed_tolerance_reads_under_its_header_in_the_detail_of_the_request(conn, users):
    put_in_production(conn, WB(), users.admin, users.admin2, users.approver)
    s = make_samples.close_sheets()
    s["パスルール"][0][7] = 0
    rid = draft(conn, users, sb.build_workbook(s), title="許容差")
    changes.submit(conn, users.admin, rid)
    d = changes.detail(conn, users.auditor, rid)
    rule = [x for x in d["diff"] if x["sheet"] == "パスルール" and x["change"] == "変更"]
    assert rule and rule[0]["fields"] == [{"field": "amount_tolerance_minor", "old": "100", "new": "0"}]
