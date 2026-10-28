"""Roles of the second release (the table, cell by cell, on the domain functions and on direct POSTs) and the screens."""
import json

import pytest

from close_kit import balance, journals, posting, reasons, roles, tasks
from test_web import ORIGIN, env, login, new_client, page_token, post  # noqa: F401  (env is a fixture)
from tools import make_samples
from close_helpers import MS

# the design's table (section 4), written out cell by cell: operation -> roles that have a circle
TABLE2 = {
    "reason_apply": {"作成者"},
    "journal_create": {"作成者"},
    "journal_decide": {"承認者"},
    "adjust_decide": {"承認者"},
    "posting_export": {"管理者"},
    "task_generate": {"管理者"},
    "task_prepare": {"作成者"},
    "task_review": {"承認者"},
}
# the change requests (operation x role): written out cell by cell as well
TABLE3 = {
    "change_create": {"管理者"},
    "change_withdraw": {"管理者"},
    "change_pre_decide": {"管理者"},
    "change_retest": {"管理者"},
    "change_result_decide": {"承認者", "管理者"},
    "change_apply": {"管理者"},
    "change_confirm": {"管理者"},
}
ALL_ROLES = ["作成者", "承認者", "管理者", "監査人"]


def test_the_table_of_the_second_release_is_exactly_this_and_the_first_one_is_untouched():
    assert {k: set(v) for k, v in roles.PERMISSIONS_CLOSE.items()} == TABLE2
    assert {k: set(v) for k, v in roles.PERMISSIONS_CHANGE.items()} == TABLE3
    assert set(roles.PERMISSIONS) & set(roles.PERMISSIONS_CLOSE) == set()
    assert set(roles.PERMISSIONS_CHANGE) & (set(roles.PERMISSIONS) | set(roles.PERMISSIONS_CLOSE)) == set()
    assert {k: set(v) for k, v in roles.ALL_PERMISSIONS.items()} == \
        {**{k: set(v) for k, v in roles.PERMISSIONS.items()}, **TABLE2, **TABLE3}


def attempts(conn, user):
    """Every changing operation of the second release as a callable, with made-up ids (a refusal for another reason
    is fine; only a PermissionDenied tells about the role)."""
    return {
        "reason_apply": [lambda: reasons.apply_reason(conn, user, MS, "R01", group_id=999999)],
        "journal_create": [
            lambda: journals.create_manual(conn, user, "2026-10-15", "", []),
            lambda: journals.import_csv(conn, user, b"x"),
            lambda: journals.submit(conn, user, 999999),
            lambda: journals.withdraw(conn, user, 999999)],
        "journal_decide": [lambda: journals.decide(conn, user, 999999, True)],
        "adjust_decide": [lambda: reasons.decide_item(conn, user, 999999, True)],
        "posting_export": [lambda: posting.export(conn, user), lambda: posting.release(conn, user, 999999, "x")],
        "task_generate": [lambda: tasks.generate(conn, user, "2026-10")],
        "task_prepare": [lambda: tasks.prepare(conn, user, 999999)],
        "task_review": [lambda: tasks.complete(conn, user, 999999), lambda: tasks.send_back(conn, user, 999999, "x")],
    }


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_operation_by_every_role_is_checked_on_the_server(conn, oct_world, role):
    from conftest import make_user
    user = make_user(conn, "u-" + role, [role])
    for op, calls in attempts(conn, user).items():
        for call in calls:
            if role in TABLE2[op]:
                try:
                    call()
                except roles.PermissionDenied:
                    pytest.fail(f"{role} should be allowed to {op}")
                except Exception:
                    pass
            else:
                with pytest.raises(roles.PermissionDenied):
                    call()
        if role not in TABLE2[op]:
            row = conn.execute("SELECT outcome FROM audit_log WHERE user_id = ? AND action = ? ORDER BY id DESC",
                               (user.id, op)).fetchone()
            assert row["outcome"] == "拒否"


@pytest.mark.parametrize("role", ALL_ROLES)
def test_viewing_is_open_to_all_four_roles(conn, oct_world, role):
    from conftest import make_user
    user = make_user(conn, "v-" + role, [role])
    journals.list_journals(conn, user)
    reasons.list_items(conn, user)
    posting.list_log(conn, user)
    tasks.list_instances(conn, user)
    balance.statement_for(conn, user, MS, "2026-10")
    journals.to_csv(conn, user)


def test_a_person_with_two_roles_gets_both_sets(conn, oct_world):
    from conftest import make_user
    both = make_user(conn, "both2", ["作成者", "承認者"])
    assert roles.allowed(both, "journal_create") and roles.allowed(both, "journal_decide")
    assert not roles.allowed(both, "posting_export")


# ---- direct POSTs --------------------------------------------------------------------------------------------------------------

ONE = lambda: None  # noqa: E731
DIRECT = {
    # path -> (form data, files, who passes the role check)
    "/journals/new": ({"date": "2026-10-15", "memo": "x", "acc1": "6100", "dr1": "5", "acc2": "1100", "cr2": "5"}, ONE, {"maker", "both"}),
    "/journals/import": ({}, lambda: {"file": ("j.csv", make_samples.journals_import_bad())}, {"maker", "both"}),
    "/journals/1/submit": ({}, ONE, {"maker", "both"}),
    "/journals/1/withdraw": ({}, ONE, {"maker", "both"}),
    "/journals/1/approve": ({}, ONE, {"approver", "both"}),
    "/journals/1/reject": ({"note": "x"}, ONE, {"approver", "both"}),
    "/reasons/apply": ({"match_set": MS, "reason_code": "R01", "group_id": "1"}, ONE, {"maker", "both"}),
    "/adjusting/1/approve": ({}, ONE, {"approver", "both"}),
    "/adjusting/1/reject": ({"note": "x"}, ONE, {"approver", "both"}),
    "/posting/export": ({}, ONE, {"admin"}),
    "/posting/release/1": ({"note": "x"}, ONE, {"admin"}),
    "/tasks/generate": ({"period": "2026-10"}, ONE, {"admin"}),
    "/tasks/1/prepare": ({"period": "2026-10"}, ONE, {"maker", "both"}),
    "/tasks/1/complete": ({"period": "2026-10"}, ONE, {"approver", "both"}),
    "/tasks/1/return": ({"period": "2026-10", "reason": "x"}, ONE, {"approver", "both"}),
}
COUNTS = ("journals", "adjusting_items", "task_instances", "posting_files", "posting_log", "task_events")


def counts(conn):
    return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in COUNTS)


def names_workbook():
    return make_samples.close_workbook(preparer="maker", reviewer="approver", approvers=("approver", "both"),
                                       senior=("both",))


@pytest.mark.parametrize("who", ["admin", "maker", "approver", "auditor", "both"])
def test_direct_post_without_the_role_is_refused_on_the_server(env, who):
    app, conn, _users = env
    post(login(app, "admin"), "/workbook", files={"file": ("w.xlsx", names_workbook())})
    c = login(app, who)
    for path, (data, files, allowed) in DIRECT.items():
        before = counts(conn)
        r = post(c, path, data=dict(data), files=files())
        if who in allowed:
            assert r.status_code != 403 or "役割" not in r.text, (who, path, r.status_code)
        else:
            assert r.status_code == 403 and "役割" in r.text, (who, path, r.status_code)
            assert counts(conn) == before, (who, path)


def test_the_auditor_sees_every_screen_and_changes_nothing(env):
    app, conn, _users = env
    post(login(app, "admin"), "/workbook", files={"file": ("w.xlsx", names_workbook())})
    c = login(app, "auditor")
    for path in ("/journals", "/journals/new", "/journals/import", "/reasons", "/adjusting", "/reconcile", "/tasks",
                 "/journals.csv", "/audit", "/audit.csv", "/match", "/config", "/changes", "/changes.csv"):
        assert c.get(path).status_code == 200, path
    # the posting screen of the sandbox is a notice: the file comes from production only (the auditor reads it too)
    r = c.get("/posting")
    assert r.status_code == 409 and "試験用" in r.text and "production" in r.text
    assert c.get("/reconcile.csv?ms=" + MS + "&period=2026-10").status_code == 200
    for path, (data, files, _allowed) in DIRECT.items():
        assert post(c, path, data=dict(data), files=files()).status_code == 403, path


def test_the_posting_stop_of_the_sandbox_points_to_production_not_to_loading_a_workbook(env):
    app, conn, _users = env
    post(login(app, "admin"), "/workbook", files={"file": ("w.xlsx", names_workbook())})     # the sandbox has a version
    for who in ("admin", "auditor"):
        c = login(app, who)
        r = c.get("/posting")
        assert r.status_code == 409 and posting.SANDBOX_TEXT in r.text
        assert "版 2 の形のワークブック" not in r.text and "読み込む" not in r.text           # not the advice for a missing version
        assert "同じ道で、<a href=\"/production/\">production へ</a>" in r.text      # the way, besides the band's
    r = post(login(app, "admin"), "/posting/export")                                         # the POST stops the same way
    assert r.status_code == 409 and "版 2 の形のワークブック" not in r.text and "同じ道で、" in r.text


def test_the_stop_for_a_sandbox_that_really_has_no_version_still_says_to_load_a_workbook(env):
    app, _conn, _users = env
    r = login(app, "admin").get("/posting")
    assert r.status_code == 409 and "版 2 の形のワークブック" in r.text and "同じ道で、" not in r.text


def test_pages_need_a_login(env):
    app, _, _ = env
    c = new_client(app)
    for p in ("/journals", "/journals/new", "/journals/import", "/journals/1", "/reasons", "/adjusting", "/reconcile",
              "/posting", "/posting/files/1", "/tasks", "/journals.csv", "/reconcile.csv?ms=a&period=b"):
        assert c.get(p).status_code == 303, p


def test_posts_need_the_token_and_the_origin_like_every_other_post(env):
    app, _, _ = env
    c = login(app, "maker")
    t = page_token(c)
    for path in ("/journals/new", "/reasons/apply", "/tasks/1/prepare", "/posting/export", "/changes/new",
                 "/changes/1/submit"):
        assert c.post(path, data={"csrf": t, "env": "sandbox"}).status_code == 403, path                 # no Origin
        assert c.post(path, data={"csrf": "x" * 43, "env": "sandbox"}, headers=ORIGIN).status_code == 403, path   # wrong token
        assert c.post(path, data={"csrf": t, "env": "sandbox"},
                      headers={"Origin": "http://evil.example"}).status_code == 403, path


def test_a_stored_version_of_schema_1_shows_a_stop_screen(env):
    app, conn, users = env
    old = {"description": {"schema_version": "1"}, "periods": [], "accounts": [], "sources": [], "match_sets": [],
           "reason_codes": [], "pass_rules": []}
    with conn:
        conn.execute("INSERT INTO config_versions(schema_version, normalized_json, sha256, source_bytes, loaded_by,"
                     " loaded_at) VALUES ('1', ?, 'h', x'00', ?, 'now')", (json.dumps(old), users["admin"].id))
        conn.execute("UPDATE environments SET version_id = 1 WHERE name = 'sandbox'")
    c = login(app, "maker")
    for p in ("/journals", "/reasons", "/adjusting", "/reconcile", "/posting", "/tasks", "/journals/new"):
        r = c.get(p)
        assert r.status_code == 409 and "ワークブックを版 2 で読み直す" in r.text, p
    r = post(c, "/journals/new", {"date": "2026-10-15"})
    assert r.status_code == 409 and "ワークブックを版 2 で読み直す" in r.text


# ---- the whole story through the screens (items 6 to 10 of the acceptance list) -----------------------------------------------------------------

def db_one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def test_the_month_end_story_over_http(env):
    app, conn, users = env
    admin, maker, approver, both, auditor = (login(app, n) for n in ("admin", "maker", "approver", "both", "auditor"))
    assert post(admin, "/workbook", files={"file": ("w.xlsx", names_workbook())}).status_code == 200
    for src, name, data in (("帳簿", "l.csv", make_samples.close_ledger_oct()), ("銀行", "b.txt", make_samples.close_bank_oct())):
        assert post(admin, "/ingest", {"source": src}, {"file": (name, data)}, "/ingest").status_code == 200
    assert post(admin, "/match/run", {"match_set": MS}, path_for_token="/match").status_code == 200

    # 6. a reason code on the difference group makes the fee journal; the screen lists the group
    gid = db_one(conn, "SELECT id FROM match_groups WHERE state = '差額の処理待ち'")
    page = maker.get("/reasons?ms=" + MS).text
    assert "INV-2610-003" in page and f'name="group_id" value="{gid}"' in page
    r = post(maker, "/reasons/apply", {"match_set": MS, "reason_code": "R01", "group_id": str(gid)}, path_for_token="/reasons")
    assert r.status_code == 200 and "JE-000001" in r.text
    assert db_one(conn, "SELECT state FROM match_groups WHERE id = ?", gid) == "仕訳の承認待ち"
    assert post(maker, "/journals/1/submit", path_for_token="/journals").status_code == 200

    # 8. the author cannot approve, with or without the screen; a person with both roles cannot approve their own
    assert post(maker, "/journals/1/approve", path_for_token="/journals").status_code == 403
    r = post(both, "/journals/new", {"date": "2026-10-20", "memo": "自分の仕訳", "acc1": "6100", "dr1": "700", "acc2": "1100", "cr2": "700"},
             path_for_token="/journals/new")
    assert r.status_code == 200
    assert post(both, "/journals/2/submit", path_for_token="/journals").status_code == 200
    r = post(both, "/journals/2/approve", path_for_token="/journals")
    assert r.status_code == 403 and "自分が作った" in r.text
    assert db_one(conn, "SELECT status FROM journals WHERE id = 2") == "承認待ち"
    assert db_one(conn, "SELECT state FROM match_groups WHERE id = ?", gid) == "仕訳の承認待ち"
    r = post(approver, "/journals/1/approve", path_for_token="/journals")
    assert r.status_code == 200 and "承認済み" in r.text
    assert db_one(conn, "SELECT state FROM match_groups WHERE id = ?", gid) == "完了"           # 7-2 step 1

    # the amount range decides who may approve: 99,999 or less by `approver`, 100,000 and over only by `both`
    post(maker, "/journals/new", {"date": "2026-10-20", "acc1": "6100", "dr1": "100000", "acc2": "1200", "cr2": "100000"},
         path_for_token="/journals/new")
    post(maker, "/journals/3/submit", path_for_token="/journals")
    r = post(approver, "/journals/3/approve", path_for_token="/journals")
    assert r.status_code == 403 and "承認できる人" in r.text
    assert post(both, "/journals/3/approve", path_for_token="/journals").status_code == 200

    # 7. broken journals are not stored, from the screen and from a file
    n = db_one(conn, "SELECT COUNT(*) FROM journals")
    r = post(maker, "/journals/new", {"date": "2026-10-20", "acc1": "6100", "dr1": "10", "acc2": "1100", "cr2": "9"},
             path_for_token="/journals/new")
    assert r.status_code == 400 and "規則1" in r.text
    r = post(maker, "/journals/new", {"date": "2026-09-30", "acc1": "9999", "dr1": "10", "acc2": "1100", "cr2": "10"},
             path_for_token="/journals/new")
    assert r.status_code == 400 and "規則3" in r.text and "規則4" in r.text
    r = post(maker, "/journals/import", files={"file": ("bad.csv", make_samples.journals_import_bad())}, path_for_token="/journals/import")
    assert r.status_code == 400 and "取り込まなかった" in r.text
    assert db_one(conn, "SELECT COUNT(*) FROM journals") == n
    r = post(maker, "/journals/import", files={"file": ("ok.csv", make_samples.journals_import_good())}, path_for_token="/journals/import")
    assert r.status_code == 200 and "2 件の仕訳を下書きに保存" in r.text

    # 6/9. interest by a journal, the month-end receipt by an adjusting item (it needs another person), the statement
    interest = db_one(conn, "SELECT id FROM transactions WHERE source = '銀行' AND amount_minor = 37")
    receipt = db_one(conn, "SELECT id FROM transactions WHERE reference = 'INV-2610-009' AND source = '帳簿'")
    post(maker, "/reasons/apply", {"match_set": MS, "reason_code": "R03", "txn_id": str(interest)}, path_for_token="/reasons")
    r = post(maker, "/reasons/apply", {"match_set": MS, "reason_code": "R04", "txn_id": str(receipt)}, path_for_token="/reasons")
    assert r.status_code == 200 and "調整項目" in r.text
    jid = db_one(conn, "SELECT id FROM journals WHERE link_txn_id = ?", interest)
    post(maker, f"/journals/{jid}/submit", path_for_token="/journals")
    assert post(approver, f"/journals/{jid}/approve", path_for_token="/journals").status_code == 200
    item = db_one(conn, "SELECT id FROM adjusting_items")
    assert post(maker, f"/adjusting/{item}/approve", path_for_token="/adjusting").status_code == 403
    assert post(approver, f"/adjusting/{item}/approve", path_for_token="/adjusting").status_code == 200
    page = auditor.get("/reconcile?ms=" + MS + "&period=2026-10").text
    assert "原因の分からない差" in page and "41,000" in page                  # the payment of 10-29 is still unexplained

    # 9. the task whose condition is "difference 0" cannot be completed while there is a difference
    assert post(admin, "/tasks/generate", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    t = {x["task_id"]: x["id"] for x in conn.execute("SELECT id, task_id FROM task_instances")}
    assert post(maker, f"/tasks/{t['T01']}/prepare", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    assert post(approver, f"/tasks/{t['T01']}/complete", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    r = post(maker, f"/tasks/{t['T02']}/prepare", {"period": "2026-10"}, path_for_token="/tasks")
    assert r.status_code == 400 and "残高一致の差" in r.text
    # the statements of the next month pair the payment: the difference is 0 and the task can go on (7-2 steps 2 and 3)
    for src, name, data in (("銀行", "b2.txt", make_samples.close_bank_nov()), ("帳簿", "l2.csv", make_samples.close_ledger_nov())):
        assert post(admin, "/ingest", {"source": src}, {"file": (name, data)}, "/ingest").status_code == 200
    post(admin, "/match/run", {"match_set": MS}, path_for_token="/match")
    page = auditor.get("/reconcile?ms=" + MS + "&period=2026-10").text
    assert "= 0。原因の分からない差は無い" in page and "1,503,987" in page
    assert post(maker, f"/tasks/{t['T02']}/prepare", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    assert post(approver, f"/tasks/{t['T02']}/complete", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    assert db_one(conn, "SELECT status FROM task_instances WHERE task_id = 'T02'") == "完了"
    # nobody but the named reviewer completes a task
    assert post(maker, f"/tasks/{t['T03']}/prepare", {"period": "2026-10"}, path_for_token="/tasks").status_code == 200
    assert post(both, f"/tasks/{t['T03']}/complete", {"period": "2026-10"}, path_for_token="/tasks").status_code == 400

    # 10. the auditor reads every screen and the files; the operation log has who / when / what / which version
    assert "JE-000001" in auditor.get("/journals.csv").text
    assert "原因の分からない差" in auditor.get("/reconcile.csv?ms=" + MS + "&period=2026-10").text
    # the posting file is written from production only: the sandbox screen stops with a notice and writes nothing (the
    # production version of this part of the story is test_the_posting_file_over_http_in_production)
    r = post(admin, "/posting/export")
    assert r.status_code == 409 and "試験用" in r.text and "production" in r.text
    assert db_one(conn, "SELECT COUNT(*) FROM posting_files") == 0
    csv_log = auditor.get("/audit.csv").text
    assert "journal_decide" in csv_log and "reason_apply" in csv_log and "task_prepare" in csv_log
    assert db_one(conn, "SELECT COUNT(*) FROM audit_log WHERE action = 'journal_decide' AND outcome = '拒否'") >= 2
    assert db_one(conn, "SELECT COUNT(*) FROM audit_log WHERE version_id IS NOT NULL AND action = 'journal_decide'") >= 2
    assert posting.pending_journals(conn, "sandbox") is not None


def test_an_overdue_task_is_marked_on_the_screen(env):
    from datetime import date
    from close_kit.web import create_app
    from tools import sample_builders as sb
    first, conn, _ = env
    app = create_app(first.state.db_path, today=lambda: date(2026, 11, 1))     # the date the screen counts with
    sheets = make_samples.close_sheets(preparer="maker", reviewer="approver", approvers=("approver",), senior=("approver",))
    sheets["作業一覧"] = [["A", "期日が過ぎた作業", "繰り返し", "", "暦日", -20, "", "maker", "approver", "なし", "", "はい"],
                      ["B", "まだ先の作業", "繰り返し", "", "暦日", 40, "", "maker", "approver", "なし", "", "はい"]]
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("w.xlsx", sb.build_workbook(sheets))})
    post(admin, "/tasks/generate", {"period": "2026-10"}, path_for_token="/tasks")
    page = admin.get("/tasks?period=2026-10").text
    # A is due 2026-10-11 and B 2026-12-10; on 2026-11-01 only A is overdue
    assert page.count('<tr class="overdue">') == 1 and page.count("期日を過ぎている") == 1
    assert page.index("期日が過ぎた作業") < page.index("期日を過ぎている") < page.index("まだ先の作業")
    # the day after B's due date both are overdue; on A's own due date A is not overdue yet
    late = create_app(first.state.db_path, today=lambda: date(2026, 12, 11))
    assert login(late, "admin").get("/tasks?period=2026-10").text.count('<tr class="overdue">') == 2
    on_due = create_app(first.state.db_path, today=lambda: date(2026, 10, 11))
    assert login(on_due, "admin").get("/tasks?period=2026-10").text.count('<tr class="overdue">') == 0


def test_a_workbook_that_reopens_a_closed_period_is_refused_on_the_screen_with_its_reason(env):
    from tools import sample_builders as sb
    app, conn, _ = env
    sheets = make_samples.close_sheets(preparer="maker", reviewer="approver", approvers=("approver",), senior=("approver",))
    admin = login(app, "admin")
    assert post(admin, "/workbook", files={"file": ("w.xlsx", sb.build_workbook(sheets))}).status_code == 200
    sheets["会計期間"][0][3] = "いいえ"                                   # 2026-09 was closed
    r = post(admin, "/workbook", files={"file": ("w2.xlsx", sb.build_workbook(sheets))})
    assert r.status_code == 400 and "締めた期間を戻す版は読み込めない" in r.text and "2026-09" in r.text
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 1
