"""Web screens (Jinja2, Japanese). All state lives on the server; the cookie holds only a random session id.

Every POST: the Origin header must be one of the two local origins and the hidden form token must equal the
token of the server-side session. Host is limited to 127.0.0.1 / localhost. The role check is done by the
domain functions (roles.require), so a direct POST without the screen is refused in the same way.
"""
from __future__ import annotations

import hmac
from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import (audit, auth, config, db, envs, ingest, manual, matchrun, metrics, queries, roles, safefile, versions,
               workbook)
from .money import fmt_amount, set_multipliers
from .roles import PermissionDenied, User

HERE = Path(__file__).resolve().parent
COOKIE = "ck_sid"
MAX_BODY = 25 * 1024 * 1024
KLASSES = ("自動", "候補", "未照合", "重複", "手", "処理済み", "転記済み")


class NeedLogin(Exception):
    pass


class ScopedRouter:
    """Registers a route under every environment head (`""` = sandbox as before, `/sandbox`, `/production`).

    The handler asks `envs.env_of(request)` which one it was reached by; nothing else decides the environment.
    """

    def __init__(self, app: FastAPI):
        self.app = app

    def _add(self, methods: list[str], path: str, **kw):
        def deco(fn):
            for head in envs.HEADS:
                full = (head + "/") if path == "/" and head else head + path
                self.app.add_api_route(full, fn, methods=methods, include_in_schema=False, **kw)
            return fn
        return deco

    def get(self, path: str, **kw):
        return self._add(["GET"], path, **kw)

    def post(self, path: str, **kw):
        return self._add(["POST"], path, **kw)


# the pages that need a settings version of the environment they are opened in (the login and the change requests,
# the operation log and the version list of the workbook page do not)
GATED = ("/config", "/ingest", "/match", "/manual", "/reasons", "/adjusting", "/journals", "/reconcile", "/posting",
         "/tasks")


class BodyLimitMiddleware:
    """Refuse a request body over `limit` bytes while it is being received.

    The Content-Length header is checked first; a body sent without it (chunked) is counted as it arrives, so the
    multipart parser never spools more than the limit to disk.
    """

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cl = dict(scope.get("headers") or []).get(b"content-length", b"")
        if cl.isdigit() and int(cl) > self.limit:
            await PlainTextResponse("送られたデータが大きすぎる", status_code=413)(scope, receive, send)
            return
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    raise StarletteHTTPException(413, "送られたデータが大きすぎる")
            return message

        await self.app(scope, limited_receive, send)


def create_app(db_path: Path | str | None = None, today: Callable[[], date] | None = None) -> FastAPI:
    """`today` gives the date the screens count with (overdue tasks, the age of adjusting items); the machine's date
    when it is not given."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = Path(db_path) if db_path is not None else config.DEFAULT_DB
    app.state.today = today or date.today
    db.connect(app.state.db_path).close()  # create the tables once
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.ALLOWED_HOSTS)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))  # autoescape on; no |safe anywhere
    templates.env.filters["amount"] = fmt_amount

    app.add_middleware(BodyLimitMiddleware, limit=MAX_BODY)
    scoped = ScopedRouter(app)

    @app.middleware("http")
    async def gate(request: Request, call_next):
        """A page of an environment that has no settings version yet stops here, before any state is touched."""
        env, _head, rest = envs.split(request.url.path)
        if env == config.ENV_PRODUCTION and any(rest == g or rest.startswith(g + "/") or rest.startswith(g + ".")
                                                for g in GATED):
            conn = open_conn()
            try:
                if versions.env_version_id(conn, env) is None:
                    sid, s = session_of(conn, request)
                    if s is not None and s[0] is not None:
                        return render(request, "stop.html", {"message": versions.NO_VERSION_TEXT[env]}, 409,
                                      s[0], s[1])
            finally:
                conn.close()
        return await call_next(request)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        # not no-referrer: with it a browser sends `Origin: null` on its own form POSTs (Fetch, "append a request
        # Origin header"), and the Origin check below would refuse every form. same-origin keeps the real Origin.
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(NeedLogin)
    async def need_login(request: Request, exc: NeedLogin):
        return RedirectResponse("/login", status_code=303)

    # ---- helpers -------------------------------------------------------------------------------
    def open_conn():
        return db.connect(app.state.db_path, init=False)

    def render(request: Request, name: str, ctx: dict[str, Any], status: int = 200,
               user: User | None = None, csrf: str = "") -> Response:
        ctx = {**ctx, "user": user, "csrf": csrf, "ops": roles.PERMISSIONS, "KLASSES": KLASSES,
               "env": envs.env_of(request), "base": envs.base_of(request)}
        return templates.TemplateResponse(request, name, ctx, status_code=status)

    def session_of(conn, request: Request):
        sid = request.cookies.get(COOKIE)
        return sid, auth.lookup_session(conn, sid)

    def current(conn, request: Request) -> tuple[User, str]:
        sid, s = session_of(conn, request)
        if s is None or s[0] is None:
            raise NeedLogin()
        return s[0], s[1]

    def deny(request: Request, user: User | None, csrf: str, msg: str, status: int = 403) -> Response:
        return render(request, "error.html", {"message": msg}, status, user, csrf)

    async def post_form(conn, request: Request, need_user: bool = True, scoped_env: bool = True):
        """Origin check, session lookup, form parse, CSRF token check, and (for every POST that is about an environment)
        the check that the hidden field `env` is the environment of the URL. Returns (form, user, csrf, sid)."""
        origin = request.headers.get("origin")
        if origin not in config.ALLOWED_ORIGINS:
            raise HTTPException(403, "Origin が許可されていない")
        sid, s = session_of(conn, request)
        if s is None:
            raise HTTPException(403, "セッションが無い（画面を開き直す）")
        user, csrf = s
        if need_user and user is None:
            raise HTTPException(403, "ログインしていない")
        form = await request.form()
        token = form.get("csrf")
        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), csrf.encode("utf-8")):
            raise HTTPException(403, "CSRF トークンが違う")
        if scoped_env and form.get(envs.FIELD) != envs.env_of(request):
            raise HTTPException(403, "画面の環境と URL の環境が違う（画面を開き直す）")
        return form, user, csrf, sid

    async def read_upload(form, field: str, limit: int) -> tuple[str, bytes]:
        up = form.get(field)
        if up is None or isinstance(up, str) or not getattr(up, "filename", None):
            raise safefile.FileRejected("ファイルが選ばれていない")
        data = safefile.read_limited(up.file, limit, "ファイル")
        return up.filename or "", data

    # ---- login / logout ------------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        conn = open_conn()
        try:
            sid, s = session_of(conn, request)
            if s is not None and s[0] is not None:
                return RedirectResponse("/", status_code=303)
            new = None
            if s is None:  # pre-login session: carries the CSRF token of the login form
                new, csrf = auth.new_session(conn, None)
            else:
                csrf = s[1]
            resp = render(request, "login.html", {"error": ""}, csrf=csrf)
            if new:
                resp.set_cookie(COOKIE, new, httponly=True, samesite="strict", path="/",
                                max_age=config.SESSION_SECONDS)
            return resp
        finally:
            conn.close()

    @app.post("/login")
    async def login(request: Request):
        conn = open_conn()
        try:
            form, _u, csrf, sid = await post_form(conn, request, need_user=False, scoped_env=False)
            name, password = str(form.get("name", "")), str(form.get("password", ""))
            user = auth.authenticate(conn, name, password)
            if user is None:
                return render(request, "login.html", {"error": "名前かパスワードが違う、またはログインの失敗が続いたため止めている"},
                              400, csrf=csrf)
            auth.delete_session(conn, sid)           # session fixation: the pre-login id is destroyed ...
            new_sid, _ = auth.new_session(conn, user.id)  # ... and a new id is issued
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, new_sid, httponly=True, samesite="strict", path="/",
                            max_age=config.SESSION_SECONDS)
            return resp
        finally:
            conn.close()

    @app.post("/logout")
    async def logout(request: Request):
        conn = open_conn()
        try:
            _form, user, _csrf, sid = await post_form(conn, request, scoped_env=False)
            auth.delete_session(conn, sid)
            audit.log(conn, user_id=user.id, user_name=user.name, action="logout", outcome=audit.OUTCOME_OK)
            resp = RedirectResponse("/login", status_code=303)
            resp.delete_cookie(COOKIE, path="/")
            return resp
        finally:
            conn.close()

    # ---- pages ----------------------------------------------------------------------------------
    @scoped.get("/", response_class=HTMLResponse)
    def index(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            ov = queries.overview(conn, user, env)
            cfgv = queries.config_view(conn, user, env)
            return render(request, "index.html", {"ov": ov, "cfg": cfgv[1] if cfgv else None}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/workbook", response_class=HTMLResponse)
    def workbook_form(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "workbook.html", {"errors": None, "saved": None,
                                                    "versions": versions.list_versions(conn)}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/workbook", response_class=HTMLResponse)
    async def workbook_upload(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            try:
                roles.require(conn, user, "workbook_load", env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（管理者だけ）")
            try:
                name, data = await read_upload(form, "file", config.LIMIT_XLSX)
            except safefile.FileRejected as e:
                return render(request, "workbook.html", {"errors": [str(e)], "saved": None,
                                                        "versions": versions.list_versions(conn)}, 400, user, csrf)
            res = workbook.parse_workbook(data, name)
            if not res.ok:
                audit.log(conn, user_id=user.id, user_name=user.name, action="workbook_load",
                          outcome=audit.OUTCOME_DENIED, env=env,
                          detail=f"{len(res.errors)} 件の誤りで全部拒否: {name[:100]}")
                return render(request, "workbook.html", {"errors": [str(e) for e in res.errors], "saved": None,
                                                        "versions": versions.list_versions(conn)}, 400, user, csrf)
            try:
                vid = versions.save_version(conn, user, res, data, env=env)
            except (versions.VersionRejected, ValueError) as e:   # e.g. it reopens a closed period (logged by
                # save_version), or the environment is production (only a change request puts a version there)
                return render(request, "workbook.html", {"errors": [str(e)], "saved": None,
                                                        "versions": versions.list_versions(conn)}, 400, user, csrf)
            return render(request, "workbook.html", {"errors": None, "saved": vid,
                                                    "versions": versions.list_versions(conn)}, 200, user, csrf)
        finally:
            conn.close()

    @scoped.get("/config", response_class=HTMLResponse)
    def config_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            cv = queries.config_view(conn, user, envs.env_of(request))
            return render(request, "config.html", {"cv": cv}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/ingest", response_class=HTMLResponse)
    def ingest_form(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            cv = queries.config_view(conn, user, env)
            ov = queries.overview(conn, user, env)
            return render(request, "ingest.html", {"cfg": cv[1] if cv else None, "result": None, "ov": ov},
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/ingest", response_class=HTMLResponse)
    async def ingest_upload(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            try:
                roles.require(conn, user, "ingest", env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（作成者か管理者）")
            source = str(form.get("source", ""))
            try:
                name, data = await read_upload(form, "file", config.LIMIT_FIXED_OR_CSV)
                result = ingest.ingest(conn, user, source, name, data, env=env)
            except safefile.FileRejected as e:
                result = ingest.IngestResult(ok=False, reasons=[str(e)])
            cv = queries.config_view(conn, user, env)
            return render(request, "ingest.html", {"cfg": cv[1] if cv else None, "result": result,
                                                   "ov": queries.overview(conn, user, env)},
                          200 if result.ok else 400, user, csrf)
        finally:
            conn.close()

    def match_context(conn, user, match_set: str | None, env: str, extra: dict | None = None) -> dict:
        cv = queries.config_view(conn, user, env)
        cfg = cv[1] if cv else None
        sets = [m for m in cfg["match_sets"]] if cfg else []
        chosen = match_set or (sets[0]["name"] if sets else None)
        ctx: dict[str, Any] = {"cfg": cfg, "sets": sets, "ms": chosen, "summary": None, "metrics": None,
                               "message": "", "mult": {}}
        if cfg and chosen:
            ctx["summary"] = queries.match_summary(conn, user, chosen, env)
            ctx["mult"] = {s["name"]: s["multiplier"] for s in cfg["sources"]}
        if extra:
            ctx.update(extra)
        return ctx

    @scoped.get("/match", response_class=HTMLResponse)
    def match_page(request: Request, ms: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "match.html", match_context(conn, user, ms, envs.env_of(request)),
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/match/run", response_class=HTMLResponse)
    async def match_run(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            ms = str(form.get("match_set", ""))
            try:
                s = matchrun.run_match(conn, user, ms, env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（作成者か管理者）")
            except (matchrun.MatchSetNotFound, versions.NoConfig) as e:
                return render(request, "match.html", match_context(conn, user, None, env, {"message": str(e)}), 400, user, csrf)
            msg = (f"照合を実行した（実行 {s.run_id}）。" + (f" 上限を超えた束: {len(s.skipped_bundles)} 件。" if s.skipped_bundles else "")
                   + (f" 許容差の中の差額の仕訳を {s.drafted} 件、下書きに作った（作った人は{config.SYSTEM_USER_NAME}。申請と承認は仕訳の画面）。" if s.drafted else "")
                   + "".join(f" {n}。" for n in s.notes))
            return render(request, "match.html", match_context(conn, user, ms, env, {"message": msg}), 200, user, csrf)
        finally:
            conn.close()

    @scoped.post("/match/metrics", response_class=HTMLResponse)
    async def match_metrics(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            ms = str(form.get("match_set", ""))
            try:
                roles.require(conn, user, "view")
                _n, data = await read_upload(form, "file", config.LIMIT_FIXED_OR_CSV)
                truth = metrics.parse_truth_csv(data)
                m = metrics.from_database(conn, env, ms, truth)
            except (safefile.FileRejected, metrics.TruthError) as e:
                return render(request, "match.html", match_context(conn, user, ms, env, {"message": str(e)}), 400, user, csrf)
            return render(request, "match.html", match_context(conn, user, ms, env, {"metrics": m}), 200, user, csrf)
        finally:
            conn.close()

    @scoped.get("/match/rows", response_class=HTMLResponse)
    def match_rows(request: Request, ms: str, klass: str, offset: int = 0):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            if klass not in KLASSES:
                return deny(request, user, csrf, "区分が違う", 400)
            env = envs.env_of(request)
            rows = queries.rows_in_class(conn, user, ms, klass, env=env, offset=max(offset, 0))
            cv = queries.config_view(conn, user, env)
            mult = {s["name"]: s["multiplier"] for s in cv[1]["sources"]} if cv else {}
            return render(request, "rows.html", {"rows": rows, "ms": ms, "klass": klass, "offset": max(offset, 0),
                                                 "page": queries.PAGE, "mult": mult}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/match/groups", response_class=HTMLResponse)
    def match_groups(request: Request, ms: str, kind: str | None = None, state: str | None = None, offset: int = 0):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            gs = queries.groups(conn, user, ms, kind or None, state or None, env=env, offset=max(offset, 0))
            detail = {g["id"]: queries.group_rows(conn, user, g["id"], env) for g in gs[:50]}
            cv = queries.config_view(conn, user, env)
            mult = {s["name"]: s["multiplier"] for s in cv[1]["sources"]} if cv else {}
            lmult, rmult = set_multipliers(cv[1], ms) if cv else (1, 1)       # for the totals of a group
            return render(request, "groups.html", {"groups": gs, "detail": detail, "ms": ms, "kind": kind or "",
                                                   "state": state or "", "offset": max(offset, 0),
                                                   "page": queries.PAGE, "mult": mult, "lmult": lmult,
                                                   "rmult": rmult}, user=user, csrf=csrf)
        finally:
            conn.close()

    # ---- manual match ---------------------------------------------------------------------------------
    def manual_context(conn, user, ms: str | None, env: str, message: str = "") -> dict:
        cv = queries.config_view(conn, user, env)
        cfg = cv[1] if cv else None
        sets = cfg["match_sets"] if cfg else []
        chosen = ms or (sets[0]["name"] if sets else None)
        ctx: dict[str, Any] = {"sets": sets, "ms": chosen, "message": message, "left": [], "right": [],
                               "pending": [], "mult": {}, "lmult": 1, "rmult": 1}
        if cfg and chosen:
            ctx["mult"] = {s["name"]: s["multiplier"] for s in cfg["sources"]}
            ctx["lmult"], ctx["rmult"] = set_multipliers(cfg, chosen)
            for klass in ("候補", "未照合", "重複"):
                for r in queries.rows_in_class(conn, user, chosen, klass, env=env):
                    (ctx["left"] if r["side"] == "L" else ctx["right"]).append(r)
            ctx["pending"] = queries.groups(conn, user, chosen, kind="手", state="承認待ち", env=env)
            ctx["detail"] = {g["id"]: queries.group_rows(conn, user, g["id"], env) for g in ctx["pending"]}
        return ctx

    @scoped.get("/manual", response_class=HTMLResponse)
    def manual_page(request: Request, ms: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "manual.html", manual_context(conn, user, ms, envs.env_of(request)),
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/manual/create", response_class=HTMLResponse)
    async def manual_create(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            ms = str(form.get("match_set", ""))
            try:
                left = [int(x) for x in form.getlist("left")]
                right = [int(x) for x in form.getlist("right")]
                gid = manual.create_manual(conn, user, ms, left, right, str(form.get("note", "")), env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（作成者だけ）")
            except (manual.ManualError, ValueError) as e:
                return render(request, "manual.html", manual_context(conn, user, ms, env, str(e)), 400, user, csrf)
            return render(request, "manual.html", manual_context(
                conn, user, ms, env, f"手の照合を作った（グループ {gid}）。別の承認者が承認するまで完了にならない。"),
                200, user, csrf)
        finally:
            conn.close()

    @scoped.post("/manual/{gid}/{action}", response_class=HTMLResponse)
    async def manual_decide(request: Request, gid: int, action: str):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            ms = str(form.get("match_set", ""))
            if action not in ("approve", "reject"):
                return deny(request, user, csrf, "操作が違う", 404)
            try:
                state = manual.decide(conn, user, gid, action == "approve", env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（承認者だけ）")
            except manual.SelfApproval as e:
                return render(request, "manual.html", manual_context(conn, user, ms, env, str(e)), 403, user, csrf)
            except manual.ManualError as e:
                return render(request, "manual.html", manual_context(conn, user, ms, env, str(e)), 400, user, csrf)
            msg = f"グループ {gid} を{'承認' if action == 'approve' else '却下'}した（状態: {state}）。"
            return render(request, "manual.html", manual_context(conn, user, ms, env, msg), 200, user, csrf)
        finally:
            conn.close()

    # ---- audit ------------------------------------------------------------------------------------------
    @scoped.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "audit.html", {"entries": queries.audit_entries(conn, user)}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/audit.csv")
    def audit_csv(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            try:
                text = queries.audit_csv(conn, user)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（管理者か監査人）")
            return Response(text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="audit_log.csv"'})
        finally:
            conn.close()

    from . import web_changes, web_close
    web_changes.register(app, scoped, open_conn=open_conn, render=render, current=current, post_form=post_form,
                         deny=deny)
    web_close.register(app, scoped, open_conn=open_conn, render=render, current=current, post_form=post_form,
                       read_upload=read_upload, deny=deny, session_of=session_of, today=lambda: app.state.today())

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code)

    return app
