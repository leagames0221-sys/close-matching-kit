"""The change requests and the two environments over HTTP: the screens, direct POSTs of people who have no circle in the
table, the environment in the URL and in the hidden field of every POST, and the month-end path in production."""
import re
import types

import pytest

from close_kit import config, db, versions, web_close
from close_kit.web import create_app
from conftest import make_user
from close_helpers import MS, PROD, put_in_production
from test_changes import draft, to_state
from test_web import ORIGIN, login, page_token, post
from tools import make_samples, sample_builders as sb

PEOPLE = {
    "demo-admin": ["管理者"], "demo-admin2": ["管理者"], "demo-maker": ["作成者"], "demo-approver": ["承認者"],
    "demo-approver2": ["承認者"], "demo-auditor": ["監査人"], "both": ["作成者", "承認者"], "super": ["管理者", "承認者"],
}
NO_ADMIN = ["demo-maker", "demo-approver", "demo-auditor", "both"]          # nobody here holds the administrator role


@pytest.fixture()
def cenv(tmp_path):
    path = tmp_path / "web.sqlite3"
    app = create_app(path)
    conn = db.connect(path)
    people = {name: make_user(conn, name, roles) for name, roles in PEOPLE.items()}
    users = types.SimpleNamespace(admin=people["demo-admin"], admin2=people["demo-admin2"], maker=people["demo-maker"],
                                  approver=people["demo-approver"], approver2=people["demo-approver2"],
                                  auditor=people["demo-auditor"])
    clients = {}

    def client(name):
        if name not in clients:
            clients[name] = login(app, name)
        return clients[name]
    yield types.SimpleNamespace(app=app, conn=conn, users=users, people=people, client=client)
    conn.close()


def one(conn, sql, *a):
    return conn.execute(sql, a).fetchone()[0]


def act(c, rid, action, **data):
    return post(c, f"/production/changes/{rid}/{action}", data)


def shown_test_id(c, rid) -> str:
    """The number of the test result that the page of the request shows in its approval form."""
    page = c.get(f"/production/changes/{rid}").text
    m = re.search(r'/result_approve" class="inline">.*?name="test_id" value="(\d+)"', page, flags=re.S)
    assert m, page[-2000:]
    return m.group(1)


def snapshot(conn):
    return tuple(one(conn, f"SELECT COUNT(*) FROM {t}") for t in
                 ("change_requests", "change_request_events", "change_tests", "change_approvals", "environment_log",
                  "config_versions")) + (one(conn, "SELECT version_id FROM environments WHERE name = 'production'"),)


# ---- the whole way, on the screens ---------------------------------------------------------------------------------------------

def test_the_whole_way_on_the_screens_and_the_requester_is_refused_at_every_step(cenv):
    conn, admin, admin2, approver, auditor = (cenv.conn, cenv.client("demo-admin"), cenv.client("demo-admin2"),
                                              cenv.client("demo-approver"), cenv.client("demo-auditor"))
    assert post(admin, "/workbook", files={"file": ("w.xlsx", make_samples.close_workbook())}).status_code == 200
    vid = one(conn, "SELECT id FROM config_versions")
    form = admin.get("/production/changes/new")
    assert form.status_code == 200 and f'value="{vid}"' in form.text and 'name="env" value="production"' in form.text
    r = post(admin, "/production/changes/new", {"to_version_id": str(vid), "title": "最初の本番", "justification": "本番を始める"})
    assert r.status_code == 200 and "下書きを作った" in r.text
    rid = one(conn, "SELECT id FROM change_requests")
    assert f"/production/changes/{rid}/submit" in r.text and f"/pre_approve" not in r.text                    # a draft offers the requester his own button only
    assert "事前承認待ち" in act(admin, rid, "submit").text
    before = snapshot(conn)
    for action, data in (("pre_approve", {}), ("pre_reject", {"reason": "x"})):
        r = act(admin, rid, action, **data)                                       # the requester, by a POST the screen did not offer
        assert r.status_code == 403 and "申請者は" in r.text
    assert snapshot(conn) == before
    r = act(admin2, rid, "pre_approve")
    assert r.status_code == 200 and "試験の結果" in r.text and "成功" in r.text and "見本の道筋" in r.text
    assert "対象外" in r.text                                                    # the first request: no data, no earlier version
    assert one(conn, "SELECT COUNT(*) FROM change_tests") == 1
    before = snapshot(conn)
    for action in ("result_approve", "result_reject"):
        assert act(admin, rid, action, reason="x").status_code == 403
    assert snapshot(conn) == before
    r = act(approver, rid, "result_approve", test_id=shown_test_id(approver, rid))  # the result on his screen
    assert r.status_code == 200 and "もう 1 人" in r.text
    assert act(admin2, rid, "apply").status_code == 400                           # one approval is not enough
    r = act(admin2, rid, "result_approve", test_id=shown_test_id(admin2, rid))
    assert r.status_code == 200 and "2 人そろった" in r.text
    assert act(admin, rid, "apply").status_code == 403 and one(conn, "SELECT version_id FROM environments WHERE name = 'production'") is None
    r = act(admin2, rid, "apply")
    assert r.status_code == 200 and one(conn, "SELECT version_id FROM environments WHERE name = 'production'") == vid
    assert act(admin2, rid, "confirm").status_code == 403                         # only the requester looks at the result
    r = act(admin, rid, "confirm")
    assert r.status_code == 200 and "確認済み" in r.text
    page = auditor.get(f"/production/changes/{rid}").text
    for needle in ("demo-approver", "demo-admin2", "状態の履歴", "利用者の作成日", "本番に適用済み", "確認済み"):
        assert needle in page
    assert "<form" not in page.split("この段の操作")[1].split("<p><a")[0]       # the auditor has no button
    csv = auditor.get("/production/changes.csv").text
    assert "最初の本番" in csv and "確認済み" in csv


def test_a_second_request_is_tested_against_production_and_shows_the_rows_that_change_class(cenv):
    conn, u = cenv.conn, cenv.users
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    from close_kit import ingest
    assert ingest.ingest(conn, u.admin, "帳簿", "l.csv", make_samples.close_ledger_oct(), PROD).ok
    assert ingest.ingest(conn, u.admin, "銀行", "b.txt", make_samples.close_bank_oct(), PROD).ok
    s = make_samples.close_sheets()
    s["パスルール"][0][7] = 0                                                      # the tolerance of the first rule: 100 -> 0
    s["承認ルート"][0][1] = 200000
    s["承認ルート"][1][0] = 200000
    admin, admin2 = cenv.client("demo-admin"), cenv.client("demo-admin2")
    assert post(admin, "/workbook", files={"file": ("w.xlsx", sb.build_workbook(s))}).status_code == 200
    vid = one(conn, "SELECT MAX(id) FROM config_versions")
    post(admin, "/production/changes/new", {"to_version_id": str(vid), "title": "許容差と閾値", "justification": "見直し"})
    rid = one(conn, "SELECT MAX(id) FROM change_requests")
    page = act(admin, rid, "submit").text
    assert "パスルール" in page and "承認ルート" in page and "amount_tolerance_minor" in page
    page = act(admin2, rid, "pre_approve").text
    assert "1 本番のデータで前と後" in page and "区分が変わった行：2 件" in page and "INV-2610-003" in page
    assert "99,999" in page and "100,000" in page and "199,999" in page          # the edges of the ranges
    assert "承認できる人 前" in page


# ---- a person who has no circle in the table is refused by the server, with or without the screen ----------------------------------

MATRIX = [
    # (state, action, data, people who must be refused)
    (config.CR_DRAFT, "submit", {}, NO_ADMIN + ["demo-admin2", "super"]),
    (config.CR_DRAFT, "withdraw", {}, NO_ADMIN + ["demo-admin2", "super"]),
    (config.CR_WAIT_PRE, "pre_approve", {}, NO_ADMIN + ["demo-admin"]),
    (config.CR_WAIT_PRE, "pre_reject", {"reason": "x"}, NO_ADMIN + ["demo-admin"]),
    (config.CR_WAIT_PRE, "withdraw", {}, NO_ADMIN + ["demo-admin2"]),
    (config.CR_TESTED, "retest", {}, NO_ADMIN),
    (config.CR_TESTED, "result_approve", {}, ["demo-maker", "demo-auditor", "demo-admin"]),
    (config.CR_TESTED, "result_reject", {"reason": "x"}, ["demo-maker", "demo-auditor", "demo-admin"]),
    (config.CR_TESTED, "withdraw", {}, NO_ADMIN + ["demo-admin2"]),
    (config.CR_RESULT_OK, "apply", {}, NO_ADMIN + ["demo-admin"]),
    (config.CR_RESULT_OK, "withdraw", {}, NO_ADMIN + ["demo-admin2"]),
    (config.CR_APPLIED, "confirm", {}, NO_ADMIN + ["demo-admin2", "super"]),
    (config.CR_APPLIED, "cannot_confirm", {"reason": "x"}, NO_ADMIN + ["demo-admin2", "super"]),
    (config.CR_CANNOT_CONFIRM, "rollback", {"reason": "x"}, NO_ADMIN),
    (config.CR_CANNOT_CONFIRM, "confirm", {}, NO_ADMIN + ["demo-admin2", "super"]),
]


@pytest.mark.parametrize("state,action,data,refused", MATRIX, ids=[f"{m[0]}-{m[1]}" for m in MATRIX])
def test_every_empty_cell_of_the_table_is_refused_on_a_direct_post(cenv, state, action, data, refused):
    conn, u = cenv.conn, cenv.users
    rid = draft(conn, u)
    if state == config.CR_CANNOT_CONFIRM:
        to_state(conn, u, rid, config.CR_APPLIED)
        from close_kit import changes
        changes.confirm(conn, u.admin, rid, False, "確かめられない")
    else:
        to_state(conn, u, rid, state)
    for who in refused:
        before = snapshot(conn)
        r = act(cenv.client(who), rid, action, **data)
        assert r.status_code == 403, (state, action, who, r.status_code)
        assert snapshot(conn) == before, (state, action, who)
    from close_kit import changes
    assert changes.state_of(conn, rid) == (config.CR_CANNOT_CONFIRM if state == config.CR_CANNOT_CONFIRM else state)


def test_only_an_administrator_makes_a_request_and_only_administrators_and_auditors_read_the_csv(cenv):
    conn = cenv.conn
    cenv.client("demo-admin")
    post(cenv.client("demo-admin"), "/workbook", files={"file": ("w.xlsx", make_samples.close_workbook())})
    vid = one(conn, "SELECT id FROM config_versions")
    for who in NO_ADMIN:
        before = snapshot(conn)
        r = post(cenv.client(who), "/production/changes/new", {"to_version_id": str(vid), "title": "t", "justification": "r"})
        assert r.status_code == 403, who
        r = post(cenv.client(who), "/production/changes/new", {"kind": "rollback", "to_version_id": str(vid), "justification": "r"})
        assert r.status_code == 403, who
        assert snapshot(conn) == before
    assert one(conn, "SELECT COUNT(*) FROM change_requests") == 0
    for who, code in (("demo-admin", 200), ("demo-auditor", 200), ("demo-maker", 403), ("demo-approver", 403), ("both", 403)):
        assert cenv.client(who).get("/production/changes.csv").status_code == code, who
    for who in PEOPLE:                                                           # everyone reads the list and the screens
        assert cenv.client(who).get("/production/changes").status_code == 200, who


# ---- the environment is in the URL and in the hidden field, and the server checks that they are the same ----------------------

def post_routes(app):
    out = []
    for r in app.routes:
        if "POST" in getattr(r, "methods", ()) and r.path not in ("/login", "/logout"):
            out.append(re.sub(r"\{\w+\}", "1", r.path))
    return sorted(set(out))


def test_every_post_of_the_screens_refuses_a_hidden_field_that_names_the_other_environment(cenv):
    u = cenv.users
    put_in_production(cenv.conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)    # both have a version
    admin = cenv.client("demo-admin")
    token = page_token(admin)
    paths = post_routes(cenv.app)
    assert len(paths) >= 45 and {"/production/ingest", "/ingest", "/sandbox/ingest", "/production/changes/new"} <= set(paths)
    before = snapshot(cenv.conn)
    for path in paths:
        env = "production" if path.startswith("/production") else "sandbox"
        other = "sandbox" if env == "production" else "production"
        for field in ({"env": other}, {}, {"env": ""}, {"env": "Production" if env == "sandbox" else "Sandbox"}):
            r = admin.post(path, data={"csrf": token, **field}, headers=ORIGIN)
            assert r.status_code == 403 and "環境が違う" in r.text, (path, field, r.status_code)
        r = admin.post(path, data={"csrf": token, "env": env}, headers=ORIGIN)           # the same environment gets further
        assert "環境が違う" not in r.text, path
    assert snapshot(cenv.conn) == before


def test_a_post_made_in_one_environment_never_changes_a_row_of_the_other(cenv):
    conn, u = cenv.conn, cenv.users
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)     # both environments have a version
    admin = cenv.client("demo-admin")

    def rows(env):
        return one(conn, "SELECT COUNT(*) FROM transactions WHERE env = ?", env)
    files = {"file": ("l.csv", make_samples.close_ledger_oct())}
    assert (rows("sandbox"), rows("production")) == (0, 0)
    r = post(admin, "/production/ingest", {"source": "帳簿"}, files, "/production/ingest")
    assert r.status_code == 200 and rows("production") > 0 and rows("sandbox") == 0
    n_prod = rows("production")
    r = post(admin, "/ingest", {"source": "銀行"}, {"file": ("b.txt", make_samples.close_bank_oct())}, "/ingest")
    assert r.status_code == 200 and rows("sandbox") > 0 and rows("production") == n_prod
    # the page of one environment, the field of the other: refused, nothing stored
    n_sand = rows("sandbox")
    r = admin.post("/production/ingest", data={"csrf": page_token(admin), "env": "sandbox", "source": "銀行"},
                   files={"file": ("b.txt", make_samples.close_bank_oct())}, headers=ORIGIN)
    assert r.status_code == 403
    r = admin.post("/ingest", data={"csrf": page_token(admin), "env": "production", "source": "帳簿"},
                   files={"file": ("l2.csv", make_samples.close_ledger_nov())}, headers=ORIGIN)
    assert r.status_code == 403
    assert (rows("sandbox"), rows("production")) == (n_sand, n_prod)
    # a match run in each: the groups of one environment are the groups of that one only
    post(admin, "/match/run", {"match_set": MS}, path_for_token="/match")
    assert one(conn, "SELECT COUNT(*) FROM match_rows WHERE env = 'production'") == 0
    post(admin, "/production/match/run", {"match_set": MS}, path_for_token="/production/match")
    assert one(conn, "SELECT COUNT(*) FROM match_rows WHERE env = 'production'") > 0
    assert one(conn, "SELECT COUNT(DISTINCT env) FROM runs") == 2


def test_production_without_a_version_stops_every_page_that_needs_one(cenv):
    admin = cenv.client("demo-admin")
    post(admin, "/workbook", files={"file": ("w.xlsx", make_samples.close_workbook())})      # the sandbox has a version
    for path in ("/config", "/ingest", "/match", "/manual", "/reasons", "/adjusting", "/journals", "/reconcile", "/posting",
                 "/tasks", "/journals/new"):
        r = admin.get("/production" + path)
        assert r.status_code == 409 and versions.NO_VERSION_TEXT["production"] in r.text, path
        assert admin.get(path).status_code in (200, 409) and "本番の版が無い" not in admin.get(path).text, path
    n = one(cenv.conn, "SELECT COUNT(*) FROM transactions")
    r = post(admin, "/production/ingest", {"source": "帳簿"}, {"file": ("l.csv", make_samples.close_ledger_oct())},
             "/production/changes")
    assert r.status_code == 409 and one(cenv.conn, "SELECT COUNT(*) FROM transactions") == n
    assert post(admin, "/production/posting/export", path_for_token="/production/changes").status_code == 409
    for path in ("/production/", "/production/changes", "/production/audit", "/production/workbook"):
        assert admin.get(path).status_code == 200, path                                          # these do not need a version
    assert "変更の申請" in admin.get("/production/reconcile").text                              # and the stop says what to do


def test_the_workbook_cannot_be_loaded_into_production_and_loading_never_moves_production(cenv):
    conn, u = cenv.conn, cenv.users
    admin = cenv.client("demo-admin")
    r = post(admin, "/production/workbook", files={"file": ("w.xlsx", make_samples.close_workbook())},
             path_for_token="/production/workbook")
    assert r.status_code == 400 and "変更の申請" in r.text and one(conn, "SELECT COUNT(*) FROM config_versions") == 0
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    prod = versions.env_version_id(conn, PROD)
    s = make_samples.close_sheets()
    s["説明"][0][1] = "読み込むだけの会社（架空）"
    assert post(admin, "/workbook", files={"file": ("w.xlsx", sb.build_workbook(s))}).status_code == 200
    assert versions.env_version_id(conn, PROD) == prod and versions.env_version_id(conn, "sandbox") != prod


def test_the_band_names_the_environment_and_the_links_stay_in_it(cenv):
    u = cenv.users
    put_in_production(cenv.conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    admin = cenv.client("demo-admin")
    prod, sand = admin.get("/production/").text, admin.get("/").text
    assert 'class="env-production"' in prod and "（本番）" in prod and 'href="/production/journals"' in prod
    assert 'name="env" value="production"' in prod and 'href="/production/changes"' in prod
    assert 'class="env-sandbox"' in sand and "（試験用）" in sand and 'href="/journals"' in sand
    assert admin.get("/sandbox/").text.count('href="/sandbox/journals"') == 1


# ---- the month-end path in production -----------------------------------------------------------------------------------------------

def test_the_posting_file_over_http_in_production(cenv):
    """The month-end story of the sample, run in production (the version got there through a change request): the
    difference of the balance statement is 0 and the posting file comes out - and does not come out of the sandbox."""
    conn, u = cenv.conn, cenv.users
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    admin, maker, approver, auditor = (cenv.client(n) for n in ("demo-admin", "demo-maker", "demo-approver", "demo-auditor"))
    P = "/production"
    for src, name, data in (("帳簿", "l.csv", make_samples.close_ledger_oct()), ("銀行", "b.txt", make_samples.close_bank_oct())):
        assert post(admin, P + "/ingest", {"source": src}, {"file": (name, data)}, P + "/ingest").status_code == 200
    assert post(admin, P + "/match/run", {"match_set": MS}, path_for_token=P + "/match").status_code == 200
    gid = one(conn, "SELECT id FROM match_groups WHERE state = '差額の処理待ち' AND env = 'production'")
    r = post(maker, P + "/reasons/apply", {"match_set": MS, "reason_code": "R01", "group_id": str(gid)}, path_for_token=P + "/reasons")
    assert r.status_code == 200 and "JE-000001" in r.text
    interest = one(conn, "SELECT id FROM transactions WHERE source = '銀行' AND amount_minor = 37 AND env = 'production'")
    receipt = one(conn, "SELECT id FROM transactions WHERE reference = 'INV-2610-009' AND source = '帳簿' AND env = 'production'")
    assert post(maker, P + "/reasons/apply", {"match_set": MS, "reason_code": "R03", "txn_id": str(interest)},
                path_for_token=P + "/reasons").status_code == 200
    assert post(maker, P + "/reasons/apply", {"match_set": MS, "reason_code": "R04", "txn_id": str(receipt)},
                path_for_token=P + "/reasons").status_code == 200
    jids = [r[0] for r in conn.execute("SELECT id FROM journals WHERE env = 'production' ORDER BY id")]
    assert len(jids) == 2
    for jid in jids:
        assert post(maker, f"{P}/journals/{jid}/submit", path_for_token=P + "/journals").status_code == 200
        assert post(approver, f"{P}/journals/{jid}/approve", path_for_token=P + "/journals").status_code == 200
    item = one(conn, "SELECT id FROM adjusting_items WHERE env = 'production'")
    assert post(approver, f"{P}/adjusting/{item}/approve", path_for_token=P + "/adjusting").status_code == 200
    for src, name, data in (("銀行", "b2.txt", make_samples.close_bank_nov()), ("帳簿", "l2.csv", make_samples.close_ledger_nov())):
        assert post(admin, P + "/ingest", {"source": src}, {"file": (name, data)}, P + "/ingest").status_code == 200
    post(admin, P + "/match/run", {"match_set": MS}, path_for_token=P + "/match")
    page = auditor.get(f"{P}/reconcile?ms={MS}&period=2026-10").text
    assert "= 0。原因の分からない差は無い" in page and "1,503,987" in page                    # the statement: difference 0
    # the sandbox has none of it, and its posting screen is a notice
    assert one(conn, "SELECT COUNT(*) FROM journals WHERE env = 'sandbox'") == 0
    r = post(admin, "/posting/export")
    assert r.status_code == 409 and "試験用" in r.text and "production" in r.text
    assert one(conn, "SELECT COUNT(*) FROM posting_files") == 0
    # the file: heading, the environment in the first line, no ledger row, and the operation log
    r = post(admin, P + "/posting/export", path_for_token=P + "/posting")
    assert r.status_code == 200 and "journal_post_production_" in r.text
    fid = one(conn, "SELECT id FROM posting_files")
    body = auditor.get(f"{P}/posting/files/{fid}").text
    assert "BUKRS,BLART,BLDAT,BUDAT,WAERS,XBLNR,BKTXT,HKONT,WRSOL,WRHAB" in body and "環境: production" in body
    assert "ledger" not in body and "JE-000001" in body
    assert one(conn, "SELECT env FROM posting_log") == "production"
    csv_log = auditor.get("/audit.csv").text
    assert "journal_decide" in csv_log and "reason_apply" in csv_log and "posting_export" in csv_log
    assert one(conn, "SELECT COUNT(*) FROM audit_log WHERE action = 'journal_decide' AND env = 'production'") >= 2


# ---- an approval counts for the result on the person's screen only ----------------------------------------------------------------

def test_a_page_opened_before_the_test_ran_again_cannot_approve_the_new_result(cenv):
    conn, u = cenv.conn, cenv.users
    rid = draft(conn, u)
    to_state(conn, u, rid, config.CR_TESTED)
    approver, admin2 = cenv.client("demo-approver"), cenv.client("demo-admin2")
    seen = shown_test_id(approver, rid)                                          # he reads result 1 ...
    assert act(admin2, rid, "retest").status_code == 200                         # ... and the test runs again
    newest = one(conn, "SELECT MAX(id) FROM change_tests WHERE request_id = ?", rid)
    assert str(newest) != seen
    before = snapshot(conn)
    r = act(approver, rid, "result_approve", test_id=seen)
    assert r.status_code == 409 and "最新ではない" in r.text
    assert act(approver, rid, "result_approve").status_code == 409               # a POST that names no result
    assert snapshot(conn) == before and one(conn, "SELECT COUNT(*) FROM change_approvals") == 0
    assert one(conn, "SELECT COUNT(*) FROM audit_log WHERE action = 'change_result_decide' AND outcome = '拒否'"
               " AND user_id = ?", u.approver.id) == 2
    r = act(approver, rid, "result_reject", reason="古い画面", test_id=seen)       # a rejection of an old page too
    assert r.status_code == 409 and snapshot(conn) == before
    r = act(approver, rid, "result_approve", test_id=shown_test_id(approver, rid))  # the page opened again
    assert r.status_code == 200 and one(conn, "SELECT test_id FROM change_approvals") == newest


def test_a_number_field_that_is_not_a_number_is_refused_not_an_error(cenv):
    conn, u = cenv.conn, cenv.users
    rid = draft(conn, u)
    to_state(conn, u, rid, config.CR_RESULT_OK)
    admin2 = cenv.client("demo-admin2")
    before = snapshot(conn)
    for action, data in (("apply", {"version_id": "abc"}), ("result_approve", {"test_id": "x1"})):
        r = act(admin2, rid, action, **data)
        assert r.status_code == 400 and "数ではない" in r.text, action
    assert snapshot(conn) == before


def test_the_production_workbook_page_offers_no_upload_and_the_sandbox_page_does(cenv):
    admin = cenv.client("demo-admin")
    prod, sand = admin.get("/production/workbook").text, admin.get("/workbook").text
    assert 'action="/production/workbook"' not in prod and 'type="file"' not in prod and "変更の申請" in prod
    assert 'action="/workbook"' in sand and 'type="file"' in sand


# ---- the approver's how-to of the user manual names what the screens show ---------------------------------------------------------

def test_the_approvers_how_to_in_the_manual_names_what_the_screens_show(cenv):
    """Every 「quoted」 word of section 3.2.1 of the user manual (the steps an approver follows to approve a journal) is
    on the screens he goes through: the band, the list with its filter, the detail, the page after 承認 and after 却下,
    and the page of a journal he may not decide."""
    from close_kit import ingest, journals
    manual = (config.REPO_ROOT / "docs" / "deliverables" / "manual-users.md").read_text(encoding="utf-8")
    section = manual.split("#### 3.2.1", 1)[1].split("\n#### ", 1)[0]
    words = [w.rstrip("…").strip() for w in re.findall(r"「([^」]+)」", section)]
    assert len(words) >= 12
    conn, u = cenv.conn, cenv.users
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    assert ingest.ingest(conn, u.admin, "帳簿", "l.csv", make_samples.close_ledger_oct(), PROD).ok
    assert ingest.ingest(conn, u.admin, "銀行", "b.txt", make_samples.close_bank_oct(), PROD).ok
    lines = [{"account": "6100", "debit": 500, "credit": 0, "memo": ""}, {"account": "1200", "debit": 0, "credit": 500, "memo": ""}]
    maker, both = u.maker, cenv.people["both"]
    ids = [journals.create_manual(conn, maker, "2026-10-20", f"見本の仕訳 {i}", lines, env=PROD) for i in (1, 2)]
    own = journals.create_manual(conn, both, "2026-10-20", "自分の仕訳", lines, env=PROD)
    big_lines = [dict(lines[0], debit=120000), dict(lines[1], credit=120000)]       # figure 5: outside his range
    big = journals.create_manual(conn, maker, "2026-10-31", "備品の購入", big_lines, env=PROD)
    for jid, who in ((ids[0], maker), (ids[1], maker), (own, both), (big, maker)):
        journals.submit(conn, who, jid, PROD)
    approver = cenv.client("demo-approver")
    P = "/production"
    pages = [approver.get("/").text, approver.get(P + "/").text, approver.get(P + "/journals?status=承認待ち").text,
             approver.get(f"{P}/journals/{ids[0]}").text]
    pages.append(post(approver, f"{P}/journals/{ids[0]}/approve", path_for_token=P + "/journals").text)
    pages.append(post(approver, f"{P}/journals/{ids[1]}/reject", {"note": "金額が違う"}, path_for_token=P + "/journals").text)
    pages.append(cenv.client("both").get(f"{P}/journals/{own}").text)
    pages.append(approver.get(f"{P}/journals/{big}").text)
    seen = "\n".join(pages)
    missing = [w for w in words if w not in seen]
    assert missing == [], missing


def test_the_record_of_a_decision_reads_as_words_not_as_json(cenv):
    """承認の記録 on the detail of a decided journal: the version, the range and its approvers in words; a rejection
    whose amount has no range says so."""
    from close_kit import journals
    conn, u = cenv.conn, cenv.users
    put_in_production(conn, make_samples.close_workbook(), u.admin, u.admin2, u.approver)
    lines = [{"account": "6100", "debit": 3000, "credit": 0, "memo": ""}, {"account": "1100", "debit": 0, "credit": 3000, "memo": ""}]
    jid = journals.create_manual(conn, u.maker, "2026-10-31", "事務用品", lines, env=PROD)
    journals.submit(conn, u.maker, jid, PROD)
    approver = cenv.client("demo-approver")
    post(approver, f"/production/journals/{jid}/approve", path_for_token="/production/journals")
    row = approver.get(f"/production/journals/{jid}").text.split("<th>承認の記録</th>", 1)[1].split("</tr>", 1)[0]
    assert "決めた時の承認ルート（設定の版 1）：0 以上 100,000 未満、承認できる人 demo-approver、demo-approver2" in row
    assert "version_id" not in row and "approvers" not in row and "{" not in row
    assert web_close._decision_route('{"version_id": 3}') == {"version_id": 3}       # no range: the screen says so
    assert web_close._decision_route("not json") is None and web_close._decision_route("") is None


def test_the_history_of_the_first_request_on_the_screen_has_no_python_none(cenv):
    users = cenv.users
    put_in_production(cenv.conn, make_samples.close_workbook(), users.admin, users.admin2, users.approver)
    page = cenv.client("demo-auditor").get("/production/changes/1").text
    assert "状態の履歴" in page and "本番の版: 版なし -&gt; 版 1" in page and "None" not in page
