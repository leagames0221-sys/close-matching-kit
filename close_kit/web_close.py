"""Screens of the period-end features: journals, reason codes, adjusting items, the balance statement, the posting file
and the task list. Registered by `web.create_app`. Every POST goes through the same Origin / session / CSRF check as
the rest; the domain functions check the role and the person (maker / approver / preparer / reviewer) again.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from . import balance, config, envs, journals, posting, queries, reasons, roles, safefile, tasks, versions
from .money import display_multiplier
from .roles import PermissionDenied

FORBIDDEN = (journals.NotAllowedToDecide, reasons.SelfDecision)
USER_ERRORS = (journals.JournalError, reasons.ReasonError, posting.PostingError, tasks.TaskError, ValueError)
FORM_LINES = 8


def _int(text: Any) -> int:
    t = str(text or "").strip().replace(",", "")
    if not t:
        return 0
    try:
        return int(t)
    except ValueError:
        raise ValueError(f"金額は整数で書く: {t!r}") from None


def _decision_route(text: str) -> dict[str, Any] | None:
    """The route stored with a decision (JSON: the version and the range with its approvers), for the screen."""
    try:
        d = json.loads(text) if text else None
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def register(app: FastAPI, scoped, *, open_conn: Callable, render: Callable, current: Callable, post_form: Callable,
             read_upload: Callable, deny: Callable, session_of: Callable,
             today: Callable[[], date] = date.today) -> None:
    """`today` is the date the screens count with (overdue tasks, ages); given by the app so it can be fixed."""

    def cfg_of(conn, user, env: str) -> tuple[int, dict[str, Any]]:
        """The configuration in use in `env` for these screens; raises NoConfig / SchemaTooOld (shown as a stop screen)."""
        roles.require(conn, user, "view")
        vid, cfg = versions.load_config(conn, env)
        versions.require_current_schema(cfg)
        return vid, cfg

    def stop_page(request: Request, exc: Exception) -> Response:
        conn = open_conn()
        try:
            _sid, s = session_of(conn, request)
            user, csrf = (s[0], s[1]) if s else (None, "")
            return render(request, "stop.html", {"message": str(exc)}, 409, user, csrf)
        finally:
            conn.close()

    @app.exception_handler(versions.SchemaTooOld)
    async def schema_old(request: Request, exc: versions.SchemaTooOld):
        return stop_page(request, exc)

    @app.exception_handler(versions.NoConfig)
    async def no_config(request: Request, exc: versions.NoConfig):
        return stop_page(request, exc)

    def fail(request: Request, user, csrf, exc: Exception, page: Callable[[str], Response]) -> Response:
        """Map a refused request to a status; the page is shown again with the reason."""
        if isinstance(exc, PermissionDenied):
            return deny(request, user, csrf, "この操作はあなたの役割には許可されていない")
        if isinstance(exc, journals.JournalInvalid):
            msg = "保存・申請・承認できない: " + " ／ ".join(str(i) for i in exc.issues)
            return page(msg, 400)
        return page(str(exc), 403 if isinstance(exc, FORBIDDEN) else 400)

    # ---------------------------------------------------------------- journals
    def journals_ctx(conn, user, env, message="", status=None, **extra) -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        return {"journals": journals.list_journals(conn, user, env, status=status), "status": status or "",
                "mult": display_multiplier(cfg), "message": message, **extra}

    @scoped.get("/journals", response_class=HTMLResponse)
    def journals_page(request: Request, status: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "journals.html", journals_ctx(conn, user, envs.env_of(request), status=status or None),
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/journals.csv")
    def journals_csv(request: Request):
        conn = open_conn()
        try:
            user, _csrf = current(conn, request)
            env = envs.env_of(request)
            cfg_of(conn, user, env)
            text = journals.to_csv(conn, user, env)
            return Response(text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="journals.csv"'})
        finally:
            conn.close()

    def form_ctx(conn, user, env, message="", values=None) -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        return {"message": message, "values": values or {}, "n": FORM_LINES, "accounts": cfg["accounts"],
                "mult": display_multiplier(cfg)}

    @scoped.get("/journals/new", response_class=HTMLResponse)
    def journal_new(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "journal_form.html", form_ctx(conn, user, envs.env_of(request)), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/journals/new", response_class=HTMLResponse)
    async def journal_new_post(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            values = {k: str(v) for k, v in form.items() if k not in ("csrf", envs.FIELD)}

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "journal_form.html", form_ctx(conn, user, env, msg, values), status, user, csrf)
            try:
                lines = []
                for i in range(1, FORM_LINES + 1):
                    acc, dr, cr, lm = (str(form.get(f"{k}{i}", "")).strip() for k in ("acc", "dr", "cr", "lm"))
                    if not (acc or dr or cr or lm):
                        continue
                    lines.append({"account": acc, "debit": _int(dr), "credit": _int(cr), "memo": lm})
                jid = journals.create_manual(conn, user, str(form.get("date", "")).strip(),
                                             str(form.get("memo", "")), lines, env=env)
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return render(request, "journals.html", journals_ctx(
                conn, user, env, f"仕訳 JE-{jid:06d} を下書きに保存した。内容を確かめて「申請」する。"), 200, user, csrf)
        finally:
            conn.close()

    @scoped.get("/journals/import", response_class=HTMLResponse)
    def journal_import_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            cfg_of(conn, user, envs.env_of(request))
            return render(request, "journal_import.html", {"result": None, "columns": journals.IMPORT_COLUMNS},
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/journals/import", response_class=HTMLResponse)
    async def journal_import_post(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            try:
                roles.require(conn, user, "journal_create")
                _n, data = await read_upload(form, "file", config.LIMIT_FIXED_OR_CSV)
                result = journals.import_csv(conn, user, data, env=env)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（作成者だけ）")
            except safefile.FileRejected as e:
                result = journals.ImportResult(False, errors=[str(e)])
            return render(request, "journal_import.html", {"result": result, "columns": journals.IMPORT_COLUMNS},
                          200 if result.ok else 400, user, csrf)
        finally:
            conn.close()

    def detail_ctx(conn, user, env, jid: int, message="") -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        j, lines = journals.journal_detail(conn, user, jid, env)
        route = journals.route_for(cfg, j["debit_total"])
        return {"j": j, "lines": lines, "route": route, "mult": display_multiplier(cfg), "message": message,
                "decision": _decision_route(j["decision_route"]),
                "account_names": {a["code"]: a["name"] for a in cfg["accounts"]},
                "why_not": journals.can_decide(cfg, user, j["created_by"], j["debit_total"])
                if "承認者" in user.roles and j["status"] == "承認待ち" else None}

    @scoped.get("/journals/{jid}", response_class=HTMLResponse)
    def journal_detail(request: Request, jid: int):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            try:
                ctx = detail_ctx(conn, user, envs.env_of(request), jid)
            except journals.JournalError as e:
                return deny(request, user, csrf, str(e), 404)
            return render(request, "journal.html", ctx, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/journals/{jid}/{action}", response_class=HTMLResponse)
    async def journal_action(request: Request, jid: int, action: str):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            if action not in ("submit", "withdraw", "approve", "reject"):
                return deny(request, user, csrf, "操作が違う", 404)

            def page(msg: str, status: int = 200) -> Response:
                try:
                    return render(request, "journal.html", detail_ctx(conn, user, env, jid, msg), status, user, csrf)
                except journals.JournalError as e:
                    return deny(request, user, csrf, str(e), 404)
            try:
                if action == "submit":
                    journals.submit(conn, user, jid, env)
                    done = "申請した。承認ルートの承認者が決める。"
                elif action == "withdraw":
                    journals.withdraw(conn, user, jid, env)
                    done = "取り下げた（記録は残る。結び付いた物は元に戻った）。"
                else:
                    new = journals.decide(conn, user, jid, action == "approve", str(form.get("note", "")), env)
                    done = f"{'承認' if action == 'approve' else '却下'}した（状態: {new}）。"
            except (PermissionDenied, *FORBIDDEN, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return page(done)
        finally:
            conn.close()

    # ------------------------------------------------------- reason codes and adjusting items
    def reasons_ctx(conn, user, env, ms: str | None, message="") -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        sets = cfg["match_sets"]
        chosen = ms if ms and any(m["name"] == ms for m in sets) else (sets[0]["name"] if sets else None)
        ctx: dict[str, Any] = {"sets": sets, "ms": chosen, "message": message, "groups": [], "rows": [], "detail": {},
                               "reason_codes": [r for r in cfg["reason_codes"] if r["enabled"]],
                               "mult": display_multiplier(cfg), "bank_side": ""}
        if chosen:
            m = reasons.find_match_set(cfg, chosen)
            ctx["bank_side"] = reasons.bank_letter(m)
            ctx["groups"] = [g for g in queries.groups(conn, user, chosen, state="差額の処理待ち", env=env)
                             if g["kind"] in ("自動", "手")]
            ctx["detail"] = {g["id"]: queries.group_rows(conn, user, g["id"], env) for g in ctx["groups"][:50]}
            ctx["rows"] = queries.rows_in_class(conn, user, chosen, "未照合", env=env)
        return ctx

    @scoped.get("/reasons", response_class=HTMLResponse)
    def reasons_page(request: Request, ms: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "reasons.html", reasons_ctx(conn, user, envs.env_of(request), ms), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/reasons/apply", response_class=HTMLResponse)
    async def reasons_apply(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            ms = str(form.get("match_set", ""))

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "reasons.html", reasons_ctx(conn, user, env, ms, msg), status, user, csrf)
            try:
                gid, tid = str(form.get("group_id", "")).strip(), str(form.get("txn_id", "")).strip()
                res = reasons.apply_reason(conn, user, ms, str(form.get("reason_code", "")),
                                           group_id=int(gid) if gid else None, txn_id=int(tid) if tid else None,
                                           env=env)
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            note = f"（{res.note}）" if res.note else ""
            return page(f"{res.kind} {res.number} を作った{note}。" + (
                "申請して、承認ルートの承認者の承認を待つ。" if res.kind == "仕訳" else
                "作った人とは別の承認者が承認すると、残高一致表に入る。"))
        finally:
            conn.close()

    def items_ctx(conn, user, env, message="") -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        now = today()
        age = {p["name"]: max((now - date.fromisoformat(p["end"])).days, 0) for p in cfg["periods"]}
        return {"items": reasons.list_items(conn, user, env), "message": message, "mult": display_multiplier(cfg),
                "age": age}

    @scoped.get("/adjusting", response_class=HTMLResponse)
    def adjusting_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "adjusting.html", items_ctx(conn, user, envs.env_of(request)), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/adjusting/{item_id}/{action}", response_class=HTMLResponse)
    async def adjusting_decide(request: Request, item_id: int, action: str):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            if action not in ("approve", "reject"):
                return deny(request, user, csrf, "操作が違う", 404)

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "adjusting.html", items_ctx(conn, user, env, msg), status, user, csrf)
            try:
                new = reasons.decide_item(conn, user, item_id, action == "approve", str(form.get("note", "")), env)
            except (PermissionDenied, *FORBIDDEN, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return page(f"調整項目 {item_id} を{'承認' if action == 'approve' else '却下'}した（状態: {new}）。")
        finally:
            conn.close()

    # ------------------------------------------------------------- balance statement
    @scoped.get("/reconcile", response_class=HTMLResponse)
    def reconcile_page(request: Request, ms: str | None = None, period: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            _vid, cfg = cfg_of(conn, user, env)
            sets = [m for m in cfg["match_sets"] if m["reconcile"]]
            chosen = ms if ms and any(m["name"] == ms for m in sets) else (sets[0]["name"] if sets else None)
            names = [p["name"] for p in cfg["periods"]]
            per = period if period in names else (next((p["name"] for p in cfg["periods"] if not p["closed"]),
                                                       names[-1] if names else None))
            st = (balance.statement(conn, env, cfg, chosen, per, today())
                  if chosen and per else None)
            return render(request, "reconcile.html", {"sets": sets, "ms": chosen, "periods": names, "period": per,
                                                      "st": st, "mult": display_multiplier(cfg)}, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/reconcile.csv")
    def reconcile_csv(request: Request, ms: str, period: str):
        conn = open_conn()
        try:
            user, _csrf = current(conn, request)
            try:
                st = balance.statement_for(conn, user, ms, period, env=envs.env_of(request), today=today())
            except (ValueError, reasons.ReasonError) as e:
                return Response(str(e), status_code=400, media_type="text/plain; charset=utf-8")
            return Response(balance.to_csv(st).encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="reconciliation.csv"'})
        finally:
            conn.close()

    # --------------------------------------------------------------------- posting file
    # The posting file is written from production only. In the sandbox the screen stops with a notice (a test file must
    # not be mistaken for a posting); `posting.export` refuses the same in the domain, so a direct POST is refused too.
    def posting_stop(request: Request, user, csrf) -> Response:
        return render(request, "stop.html", {"message": posting.SANDBOX_TEXT, "to_production": True}, 409, user, csrf)

    def posting_ctx(conn, user, env, message="", result=None) -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        return {"pending": posting.pending_journals(conn, env), "log": posting.list_log(conn, user, env),
                "message": message, "result": result, "mult": display_multiplier(cfg),
                "mult_ok": posting.multipliers_ok(cfg)}

    @scoped.get("/posting", response_class=HTMLResponse)
    def posting_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            cfg_of(conn, user, env)              # no settings version / a version of an old schema stops first
            if env != config.ENV_PRODUCTION:
                return posting_stop(request, user, csrf)
            return render(request, "posting.html", posting_ctx(conn, user, env), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/posting/export", response_class=HTMLResponse)
    async def posting_export(request: Request):
        conn = open_conn()
        try:
            _form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            if env != config.ENV_PRODUCTION:
                try:
                    posting.export(conn, user, env)       # the domain refuses (and writes the refusal to the log)
                except PermissionDenied:
                    return deny(request, user, csrf, "この操作はあなたの役割には許可されていない")
                except posting.PostingError:
                    pass
                return posting_stop(request, user, csrf)

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "posting.html", posting_ctx(conn, user, env, msg), status, user, csrf)
            try:
                res = posting.export(conn, user, env)
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return render(request, "posting.html", posting_ctx(conn, user, env, f"{res.count} 件の仕訳を出力した。", res),
                          200, user, csrf)
        finally:
            conn.close()

    @scoped.post("/posting/release/{jid}", response_class=HTMLResponse)
    async def posting_release(request: Request, jid: int):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            if env != config.ENV_PRODUCTION:
                try:
                    roles.require(conn, user, "posting_export", env=env)
                except PermissionDenied:
                    return deny(request, user, csrf, "この操作はあなたの役割には許可されていない")
                return posting_stop(request, user, csrf)

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "posting.html", posting_ctx(conn, user, env, msg), status, user, csrf)
            try:
                posting.release(conn, user, jid, str(form.get("note", "")), env)
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return page(f"仕訳 {jid} を外した。次の出力でもう一度出る。")
        finally:
            conn.close()

    @scoped.get("/posting/files/{fid}")
    def posting_file(request: Request, fid: int):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            env = envs.env_of(request)
            if env != config.ENV_PRODUCTION:
                roles.require(conn, user, "view")
                return posting_stop(request, user, csrf)
            try:
                f = posting.get_file(conn, user, fid, env)
            except posting.PostingError as e:
                return deny(request, user, csrf, str(e), 404)
            return Response(bytes(f["content"]), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{f["name"]}"'})
        finally:
            conn.close()

    # ------------------------------------------------------------------------- tasks
    def tasks_ctx(conn, user, env, period: str | None, message="", generated=None) -> dict:
        _vid, cfg = cfg_of(conn, user, env)
        names = [p["name"] for p in cfg["periods"]]
        per = period if period in names else next((p["name"] for p in cfg["periods"] if not p["closed"]),
                                                  names[-1] if names else None)
        now = today()
        return {"periods": names, "period": per, "items": tasks.list_instances(conn, user, env, period=per, today=now),
                "message": message, "generated": generated, "today": now.isoformat()}

    @scoped.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request, period: str | None = None):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "tasks.html", tasks_ctx(conn, user, envs.env_of(request), period), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/tasks/generate", response_class=HTMLResponse)
    async def tasks_generate(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            period = str(form.get("period", ""))

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "tasks.html", tasks_ctx(conn, user, env, period, msg), status, user, csrf)
            try:
                res = tasks.generate(conn, user, period, env)
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return render(request, "tasks.html", tasks_ctx(
                conn, user, env, period, f"作業を {len(res.created)} 件作った（すでにあった物 {res.existing} 件）。", res),
                200, user, csrf)
        finally:
            conn.close()

    @scoped.post("/tasks/{iid}/{action}", response_class=HTMLResponse)
    async def tasks_action(request: Request, iid: int, action: str):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            env = envs.env_of(request)
            period = str(form.get("period", ""))
            if action not in ("prepare", "complete", "return"):
                return deny(request, user, csrf, "操作が違う", 404)

            def page(msg: str, status: int = 200) -> Response:
                return render(request, "tasks.html", tasks_ctx(conn, user, env, period, msg), status, user, csrf)
            try:
                if action == "prepare":
                    tasks.prepare(conn, user, iid, env)
                    done = "作成完了にした。確認者が完了にする。"
                elif action == "complete":
                    tasks.complete(conn, user, iid, env)
                    done = "完了にした。"
                else:
                    tasks.send_back(conn, user, iid, str(form.get("reason", "")), env)
                    done = "差し戻した。"
            except (PermissionDenied, *USER_ERRORS) as e:
                return fail(request, user, csrf, e, page)
            return page(done)
        finally:
            conn.close()
