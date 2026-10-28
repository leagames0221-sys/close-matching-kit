import re

import pytest
from fastapi.testclient import TestClient

from close_kit import auth, config, db
from close_kit.web import create_app
from conftest import make_user
from tools import make_samples, sample_builders as sb

ORIGIN = {"Origin": "http://127.0.0.1:8765"}


@pytest.fixture()
def env(tmp_path):
    path = tmp_path / "web.sqlite3"
    app = create_app(path)
    conn = db.connect(path)
    users = {
        "admin": make_user(conn, "admin", ["管理者"]),
        "maker": make_user(conn, "maker", ["作成者"]),
        "approver": make_user(conn, "approver", ["承認者"]),
        "auditor": make_user(conn, "auditor", ["監査人"]),
        "both": make_user(conn, "both", ["作成者", "承認者"]),
    }
    yield app, conn, users
    conn.close()


def new_client(app):
    return TestClient(app, base_url="http://127.0.0.1:8765", follow_redirects=False)


def token(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, html[:300]
    return m.group(1)


def login(app, name, password=None):
    c = new_client(app)
    page = c.get("/login")
    r = c.post("/login", data={"csrf": token(page.text), "name": name, "password": password or "password-" + name},
               headers=ORIGIN)
    assert r.status_code == 303, r.text
    return c


def page_token(c, path="/"):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return token(r.text)


def env_of(path):
    """The environment a path is about (the head of the URL; no head = sandbox)."""
    return "production" if path.startswith("/production") else "sandbox"


def post(c, path, data=None, files=None, path_for_token="/"):
    data = dict(data or {})
    data.setdefault("csrf", page_token(c, path_for_token))
    data.setdefault("env", env_of(path))      # the hidden field of every form that is about an environment
    return c.post(path, data=data, files=files, headers=ORIGIN)


# ---- login, session fixation, logout ------------------------------------------------------------------

def test_login_destroys_the_pre_login_session_and_issues_a_new_id(env):
    app, conn, _ = env
    c = new_client(app)
    page = c.get("/login")
    pre = c.cookies.get("ck_sid")
    assert pre and "HttpOnly" in page.headers["set-cookie"] and "SameSite=strict" in page.headers["set-cookie"]
    r = c.post("/login", data={"csrf": token(page.text), "name": "maker", "password": "password-maker"}, headers=ORIGIN)
    assert r.status_code == 303
    post_id = c.cookies.get("ck_sid")
    assert post_id and post_id != pre
    assert auth.lookup_session(conn, pre) is None                 # the pre-login id is no longer valid
    s = auth.lookup_session(conn, post_id)
    assert s is not None and s[0].name == "maker"
    # replaying the old id does not give a session
    old = new_client(app)
    old.cookies.set("ck_sid", pre)
    assert old.get("/").status_code == 303


def test_wrong_password_keeps_the_pre_session_and_is_logged(env):
    app, conn, _ = env
    c = new_client(app)
    page = c.get("/login")
    r = c.post("/login", data={"csrf": token(page.text), "name": "maker", "password": "wrong"}, headers=ORIGIN)
    assert r.status_code == 400 and "違う" in r.text
    row = conn.execute("SELECT outcome FROM audit_log WHERE action='login' ORDER BY id DESC").fetchone()
    assert row["outcome"] == "拒否"


def test_login_failures_are_limited_to_ten(env):
    app, conn, _ = env
    c = new_client(app)
    page = c.get("/login")
    t = token(page.text)
    for _ in range(config.LOGIN_FAIL_LIMIT):
        assert c.post("/login", data={"csrf": t, "name": "maker", "password": "bad"}, headers=ORIGIN).status_code == 400
    r = c.post("/login", data={"csrf": t, "name": "maker", "password": "password-maker"}, headers=ORIGIN)
    assert r.status_code == 400


def test_logout_removes_the_session(env):
    app, conn, _ = env
    c = login(app, "maker")
    sid = c.cookies.get("ck_sid")
    assert auth.lookup_session(conn, sid) is not None
    r = post(c, "/logout")
    assert r.status_code == 303
    assert auth.lookup_session(conn, sid) is None
    old = new_client(app)
    old.cookies.set("ck_sid", sid)
    assert old.get("/").status_code == 303 and old.get("/").headers["location"] == "/login"


# ---- CSRF, Origin, Host ---------------------------------------------------------------------------------

def test_post_without_token_wrong_token_wrong_or_missing_origin_is_refused(env):
    app, conn, _ = env
    c = login(app, "admin")
    t = page_token(c)
    files = {"file": ("w.xlsx", make_samples.workbook())}
    assert c.post("/workbook", data={"env": "sandbox"}, files=files, headers=ORIGIN).status_code == 403   # no token
    assert c.post("/workbook", data={"csrf": "x" * 43, "env": "sandbox"}, files=files, headers=ORIGIN).status_code == 403   # wrong token
    assert c.post("/workbook", data={"csrf": t, "env": "sandbox"}, files=files,
                  headers={"Origin": "http://evil.example"}).status_code == 403                      # wrong origin
    assert c.post("/workbook", data={"csrf": t, "env": "sandbox"}, files=files,
                  headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403
    assert c.post("/workbook", data={"csrf": t, "env": "sandbox"}, files=files).status_code == 403                     # no origin
    assert c.post("/workbook", data={"csrf": t, "env": "sandbox"}, files=files,
                  headers={"Origin": "null"}).status_code == 403                                     # null is not allowed
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 0
    ok = c.post("/workbook", data={"csrf": t, "env": "sandbox"}, files=files, headers={"Origin": "http://localhost:8765"})
    assert ok.status_code == 200 and "読み込んだ" in ok.text


def test_referrer_policy_lets_the_browser_send_the_real_origin_on_form_posts(env):
    # Fetch standard, "append a request Origin header": for a non-cors POST under "no-referrer" the browser sends
    # `Origin: null`, which the Origin check refuses. "same-origin" sends the real origin to this site only.
    app, _, _ = env
    r = new_client(app).get("/login")
    assert r.headers["Referrer-Policy"] in ("same-origin", "strict-origin", "strict-origin-when-cross-origin")


def test_token_of_another_session_is_refused(env):
    app, conn, _ = env
    a, b = login(app, "admin"), login(app, "maker")
    tb = page_token(b)
    r = a.post("/workbook", data={"csrf": tb}, files={"file": ("w.xlsx", make_samples.workbook())}, headers=ORIGIN)
    assert r.status_code == 403


def test_login_form_post_needs_its_token_too(env):
    app, _, _ = env
    c = new_client(app)
    c.get("/login")
    r = c.post("/login", data={"csrf": "nope", "name": "maker", "password": "password-maker"}, headers=ORIGIN)
    assert r.status_code == 403


def test_other_host_is_refused(env):
    app, _, _ = env
    c = TestClient(app, base_url="http://evil.example:8765", follow_redirects=False)
    assert c.get("/login").status_code == 400
    c2 = new_client(app)
    assert c2.get("/login", headers={"Host": "evil.example"}).status_code == 400
    assert c2.get("/login", headers={"Host": "localhost:8765"}).status_code == 200


def test_security_headers_and_no_open_docs(env):
    app, _, _ = env
    c = new_client(app)
    r = c.get("/login")
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/docs").status_code == 404


# ---- roles on direct POST -------------------------------------------------------------------------------

DIRECT = {
    # path -> (extra form data, files, roles that may pass the role check)
    "/workbook": ({}, lambda: {"file": ("w.xlsx", make_samples.workbook())}, {"admin"}),
    "/ingest": ({"source": "帳簿"}, lambda: {"file": ("l.csv", make_samples.ledger_csv())}, {"admin", "maker", "both"}),
    "/match/run": ({"match_set": "預金照合"}, lambda: None, {"admin", "maker", "both"}),
    "/manual/create": ({"match_set": "預金照合", "left": ["1"], "right": ["2"]}, lambda: None, {"maker", "both"}),
    "/manual/1/approve": ({"match_set": "預金照合"}, lambda: None, {"approver", "both"}),
    "/manual/1/reject": ({"match_set": "預金照合"}, lambda: None, {"approver", "both"}),
}


@pytest.mark.parametrize("who", ["admin", "maker", "approver", "auditor", "both"])
def test_direct_post_without_the_role_is_refused_on_the_server(env, who):
    app, conn, users = env
    # give the system a configuration so that refusals can only come from the role check
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("w.xlsx", make_samples.workbook())})
    c = login(app, who)
    for path, (data, files, allowed) in DIRECT.items():
        before = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], \
            conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0]
        r = post(c, path, data={k: v for k, v in data.items()}, files=files())
        if who in allowed:
            assert r.status_code != 403 or "役割" not in r.text, (who, path)
        else:
            assert r.status_code == 403 and "役割" in r.text, (who, path, r.status_code)
            after = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0], \
                conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0]
            assert before == after
    csv_ok = c.get("/audit.csv").status_code
    assert (csv_ok == 200) == (who in {"admin", "auditor"})


def test_pages_need_a_login(env):
    app, _, _ = env
    c = new_client(app)
    for p in ("/", "/config", "/ingest", "/match", "/manual", "/audit", "/audit.csv", "/workbook"):
        assert c.get(p).status_code == 303, p


# ---- the whole story through the screens ----------------------------------------------------------------------

def test_acceptance_flow_over_http(env):
    app, conn, _ = env
    admin = login(app, "admin")
    # a bad workbook is refused with sheet / row / column
    bad = post(admin, "/workbook", files={"file": ("bad.xlsx", make_samples.bad_workbook())}, path_for_token="/workbook")
    assert bad.status_code == 400 and "パスルール 行3 列「順番」" in bad.text and "照合セット 行2" in bad.text
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 0
    # the good one is read and shown
    ok = post(admin, "/workbook", files={"file": ("sample.xlsx", make_samples.workbook())}, path_for_token="/workbook")
    assert ok.status_code == 200
    cfg = admin.get("/config").text
    assert "預金照合" in cfg and "請求書ごとの合算" in cfg and "多対1" in cfg and "銀行" in cfg
    # statements: unbalanced zengin is refused, the right one accepted, the same range again refused
    z_bad = post(admin, "/ingest", {"source": "銀行"}, {"file": ("b.txt", make_samples.bank_zengin(closing_override=1))}, "/ingest")
    assert z_bad.status_code == 400 and "残高が合わない" in z_bad.text
    assert post(admin, "/ingest", {"source": "帳簿"}, {"file": ("l.csv", make_samples.ledger_csv())}, "/ingest").status_code == 200
    assert post(admin, "/ingest", {"source": "銀行"}, {"file": ("b.txt", make_samples.bank_zengin())}, "/ingest").status_code == 200
    again = post(admin, "/ingest", {"source": "銀行"}, {"file": ("b2.txt", make_samples.bank_zengin())}, "/ingest")
    assert again.status_code == 400 and "重なる" in again.text
    # match
    run = post(admin, "/match/run", {"match_set": "預金照合"}, path_for_token="/match")
    assert run.status_code == 200 and "照合を実行した" in run.text
    summary = admin.get("/match?ms=預金照合").text
    for k in ("自動", "候補", "未照合", "重複"):
        assert k in summary
    rows = admin.get("/match/rows", params={"ms": "預金照合", "klass": "重複"}).text
    assert "INV-2610-010" in rows
    # the answer-file box
    truth = "左の行,右の行\nT001,2\n".encode()
    m = post(admin, "/match/metrics", {"match_set": "預金照合"}, {"file": ("t.csv", truth)}, "/match")
    assert m.status_code == 200 and "自動の適合率" in m.text and "該当なし" not in m.text.split("自動の適合率")[1][:60]


def test_manual_match_and_approval_over_http(env):
    app, conn, users = env
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("s.xlsx", make_samples.workbook())}, path_for_token="/workbook")
    post(admin, "/ingest", {"source": "帳簿"}, {"file": ("l.csv", make_samples.ledger_csv())}, "/ingest")
    post(admin, "/ingest", {"source": "銀行"}, {"file": ("b.txt", make_samples.bank_zengin())}, "/ingest")
    post(admin, "/match/run", {"match_set": "預金照合"}, path_for_token="/match")
    l = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='L'").fetchone()[0]
    r = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='R'").fetchone()[0]
    maker = login(app, "maker")
    created = post(maker, "/manual/create", {"match_set": "預金照合", "left": [str(l)], "right": [str(r)], "note": "相殺"},
                   path_for_token="/manual")
    assert created.status_code == 200 and "完了にならない" in created.text
    gid = conn.execute("SELECT id FROM match_groups WHERE kind='手'").fetchone()[0]
    assert conn.execute("SELECT state FROM match_groups WHERE id=?", (gid,)).fetchone()[0] == "承認待ち"
    # the maker (no approver role) cannot approve
    assert post(maker, f"/manual/{gid}/approve", {"match_set": "預金照合"}, path_for_token="/manual").status_code == 403
    # a person with both roles cannot approve their own group
    both = login(app, "both")
    own = post(both, "/manual/create", {"match_set": "預金照合", "left": [str(l)], "right": [str(r)]}, path_for_token="/manual")
    assert own.status_code == 400   # the rows already belong to the maker's group (class 手)
    # another approver can
    approver = login(app, "approver")
    done = post(approver, f"/manual/{gid}/approve", {"match_set": "預金照合"}, path_for_token="/manual")
    assert done.status_code == 200 and "承認した" in done.text
    assert conn.execute("SELECT state FROM match_groups WHERE id=?", (gid,)).fetchone()[0] in ("完了", "差額の処理待ち")


def test_self_approval_with_both_roles_over_http(env):
    app, conn, users = env
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("s.xlsx", make_samples.workbook())}, path_for_token="/workbook")
    post(admin, "/ingest", {"source": "帳簿"}, {"file": ("l.csv", make_samples.ledger_csv())}, "/ingest")
    post(admin, "/ingest", {"source": "銀行"}, {"file": ("b.txt", make_samples.bank_zengin())}, "/ingest")
    post(admin, "/match/run", {"match_set": "預金照合"}, path_for_token="/match")
    l = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='L'").fetchone()[0]
    r = conn.execute("SELECT txn_id FROM match_rows WHERE klass='未照合' AND side='R'").fetchone()[0]
    both = login(app, "both")
    post(both, "/manual/create", {"match_set": "預金照合", "left": [str(l)], "right": [str(r)]}, path_for_token="/manual")
    gid = conn.execute("SELECT id FROM match_groups WHERE kind='手'").fetchone()[0]
    r = post(both, f"/manual/{gid}/approve", {"match_set": "預金照合"}, path_for_token="/manual")
    assert r.status_code == 403 and "自分が照合" in r.text
    assert conn.execute("SELECT state FROM match_groups WHERE id=?", (gid,)).fetchone()[0] == "承認待ち"


def test_imported_text_is_escaped_in_the_screens(env):
    app, conn, _ = env
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("s.xlsx", make_samples.workbook())}, path_for_token="/workbook")
    evil = sb.build_csv(["取引ID", "日付", "金額", "参照", "摘要"],
                        [["X1", "2026-10-03", 100, "", "<script>alert(1)</script>"]])
    assert post(admin, "/ingest", {"source": "帳簿"}, {"file": ("e.csv", evil)}, "/ingest").status_code == 200
    post(admin, "/match/run", {"match_set": "預金照合"}, path_for_token="/match")
    page = admin.get("/match/rows", params={"ms": "預金照合", "klass": "未照合"}).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page


def test_audit_log_page_and_csv(env):
    app, conn, _ = env
    admin = login(app, "admin")
    post(admin, "/workbook", files={"file": ("s.xlsx", make_samples.workbook())}, path_for_token="/workbook")
    page = admin.get("/audit").text
    assert "workbook_load" in page and "login" in page
    r = admin.get("/audit.csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "workbook_load" in r.text


def test_oversized_body_is_refused(env):
    app, _, _ = env
    c = login(app, "admin")
    r = c.post("/workbook", content=b"x", headers={**ORIGIN, "Content-Length": str(30 * 1024 * 1024),
                                                   "Content-Type": "application/octet-stream"})
    assert r.status_code == 413


def test_oversized_body_without_content_length_is_refused_while_it_arrives(tmp_path, monkeypatch):
    from close_kit import web
    monkeypatch.setattr(web, "MAX_BODY", 256 * 1024)
    path = tmp_path / "w.sqlite3"
    app = web.create_app(path)
    conn = db.connect(path)
    make_user(conn, "admin", ["管理者"])
    c = login(app, "admin")
    t = page_token(c, "/workbook")
    boundary = "b0undary"
    crlf = "\r\n"
    head = (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"csrf\"{crlf}{crlf}{t}{crlf}"
            f"--{boundary}{crlf}Content-Disposition: form-data; name=\"file\"; filename=\"w.xlsx\"{crlf}"
            f"Content-Type: application/octet-stream{crlf}{crlf}").encode()

    def chunks():                       # a generator body is sent chunked, with no Content-Length header
        yield head
        for _ in range(64):
            yield b"x" * 16 * 1024
        yield f"{crlf}--{boundary}--{crlf}".encode()

    r = c.post("/workbook", content=chunks(),
               headers={**ORIGIN, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413
    assert conn.execute("SELECT COUNT(*) FROM config_versions").fetchone()[0] == 0
    conn.close()


def test_csrf_token_with_non_ascii_characters_is_refused_not_an_error(env):
    app, _, _ = env
    c = login(app, "maker")
    r = c.post("/match/run", data={"csrf": "あいう", "match_set": "x", "env": "sandbox"}, headers=ORIGIN)
    assert r.status_code == 403
