"""Screens of the change requests (list, make, one request with its parts, test result, approvals, history, and the
buttons of the step the request is at). Registered by `web.create_app`; every POST goes through the same Origin /
session / CSRF / environment check as the rest, and `changes.py` checks the role and the person again.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from . import changecheck, changes, config, envs, roles
from .money import display_multiplier
from .roles import PermissionDenied


def register(app: FastAPI, scoped, *, open_conn: Callable, render: Callable, current: Callable, post_form: Callable,
             deny: Callable) -> None:

    def version_choices(conn) -> list[dict[str, Any]]:
        """Versions that can be proposed: schema 2, not the one in production now."""
        prod = changes.production_version(conn)
        return [dict(v) for v in conn.execute(
            "SELECT v.id, v.sha256, v.loaded_at, u.name AS loaded_by FROM config_versions v"
            " JOIN users u ON u.id = v.loaded_by WHERE v.schema_version = ? ORDER BY v.id DESC", (config.SCHEMA_VERSION,))
            if v["id"] != prod]

    def list_ctx(conn, user, message="") -> dict:
        return {"requests": changes.list_requests(conn, user), "message": message,
                "production": changes.production_version(conn)}

    def detail_ctx(conn, user, rid: int, message="", status=200) -> dict:
        d = changes.detail(conn, user, rid)
        to_cfg = changecheck.cfg_of_version(conn, d["req"]["to_version_id"]) or {}
        return {"d": d, "actions": changes.allowed_actions(user, d), "message": message,
                "mult": display_multiplier(to_cfg), "CLASS_ORDER": changecheck.CLASS_ORDER}

    @scoped.get("/changes", response_class=HTMLResponse)
    def changes_page(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            return render(request, "changes.html", list_ctx(conn, user), user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.get("/changes.csv")
    def changes_csv(request: Request):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            try:
                text = changes.to_csv(conn, user)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（管理者か監査人）")
            return Response(text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": 'attachment; filename="change_requests.csv"'})
        finally:
            conn.close()

    @scoped.get("/changes/new", response_class=HTMLResponse)
    def change_new(request: Request, rollback: int = 0):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            roles.require(conn, user, "view")
            return render(request, "change_new.html", {"versions": version_choices(conn), "values": {},
                                                      "message": "", "rollback": bool(rollback),
                                                      "production": changes.production_version(conn)},
                          user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/changes/new", response_class=HTMLResponse)
    async def change_new_post(request: Request):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            values = {k: str(v) for k, v in form.items() if k not in ("csrf", envs.FIELD)}
            rollback = values.get("kind") == "rollback"

            def again(msg: str, status: int) -> Response:
                return render(request, "change_new.html", {"versions": version_choices(conn), "values": values,
                                                          "message": msg, "rollback": rollback,
                                                          "production": changes.production_version(conn)},
                              status, user, csrf)
            try:
                to_id = int(values.get("to_version_id", "") or 0)
                back = values.get("rollback_version_id", "").strip()
                if rollback:
                    rid = changes.create_rollback(conn, user, to_id, title=values.get("title", ""),
                                                  justification=values.get("justification", ""))
                else:
                    rid = changes.create_draft(
                        conn, user, title=values.get("title", ""), to_version_id=to_id,
                        justification=values.get("justification", ""),
                        functional_impact=values.get("functional_impact", ""),
                        impact_if_not=values.get("impact_if_not", ""),
                        rollback_version_id=int(back) if back else None)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない（管理者だけ）")
            except (changes.ChangeError, ValueError) as e:
                return again(str(e), 403 if isinstance(e, changes.NotAllowedToChange) else 400)
            return render(request, "change.html", detail_ctx(
                conn, user, rid, "下書きを作った。内容と、自動で出た「変わる部分」を確かめて「申請する」。"),
                200, user, csrf)
        finally:
            conn.close()

    @scoped.get("/changes/{rid}", response_class=HTMLResponse)
    def change_detail(request: Request, rid: int):
        conn = open_conn()
        try:
            user, csrf = current(conn, request)
            try:
                ctx = detail_ctx(conn, user, rid)
            except changes.ChangeError as e:
                return deny(request, user, csrf, str(e), 404)
            return render(request, "change.html", ctx, user=user, csrf=csrf)
        finally:
            conn.close()

    @scoped.post("/changes/{rid}/{action}", response_class=HTMLResponse)
    async def change_action(request: Request, rid: int, action: str):
        conn = open_conn()
        try:
            form, user, csrf, _ = await post_form(conn, request)
            reason = str(form.get("reason", ""))
            if action not in ACTIONS:
                return deny(request, user, csrf, "操作が違う", 404)

            def page(msg: str, status: int = 200) -> Response:
                try:
                    return render(request, "change.html", detail_ctx(conn, user, rid, msg), status, user, csrf)
                except changes.ChangeError as e:
                    return deny(request, user, csrf, str(e), 404)
            try:
                done = ACTIONS[action](conn, user, rid, form, reason)
            except PermissionDenied:
                return deny(request, user, csrf, "この操作はあなたの役割には許可されていない")
            except changes.NotAllowedToChange as e:
                return page(str(e), 403)
            except (changes.StaleTest, changes.NotLatestResult) as e:
                return page(str(e), 409)
            except changes.ChangeError as e:
                return page(str(e), 400)
            except ValueError:                # a number field that is not a number (a hand-made POST)
                return page("送られた番号が数ではない（画面を開き直す）", 400)
            except changecheck.ToolError as e:
                return page(f"試験の道具が止まったので、試験済みにしていない（「試験をやり直す」で流し直せる）: {e}", 500)
            if isinstance(done, int):         # a rollback draft: show the new request
                return render(request, "change.html", detail_ctx(conn, user, done, "戻しの申請の下書きを作った。"),
                              200, user, csrf)
            return page(done)
        finally:
            conn.close()


def _v(form, key: str) -> int | None:
    t = str(form.get(key, "")).strip()
    return int(t) if t else None


# action name -> function(conn, user, rid, form, reason) -> message (or the id of a new request)
ACTIONS: dict[str, Callable[..., Any]] = {
    "submit": lambda c, u, rid, f, r: (changes.submit(c, u, rid), "申請した。事前承認を待つ。")[1],
    "withdraw": lambda c, u, rid, f, r: (changes.withdraw(c, u, rid, r), "取り下げた（記録は残る）。")[1],
    "pre_approve": lambda c, u, rid, f, r: "事前承認した。試験を流した（結果は下の「試験の結果」）。"
    if changes.pre_decide(c, u, rid, True) == config.CR_TESTED else "事前承認した。",
    "pre_reject": lambda c, u, rid, f, r: (changes.pre_decide(c, u, rid, False, r), "事前却下した。")[1],
    "retest": lambda c, u, rid, f, r: (changes.retest(c, u, rid), "試験を流し直した。前の結果への承認は数えない。")[1],
    # the screen sends the number of the test result it shows: an approval counts only for the result that was read
    # (0 when a POST does not send it, and the approval is refused)
    "result_approve": lambda c, u, rid, f, r: (
        "結果を承認した。2 人そろったので、本番に適用できる。"
        if changes.decide_result(c, u, rid, True, test_id=_v(f, "test_id") or 0) == config.CR_RESULT_OK
        else "結果を承認した。もう 1 人（承認者と管理者の両方がそろうまで）の承認を待つ。"),
    "result_reject": lambda c, u, rid, f, r: (changes.decide_result(c, u, rid, False, r, test_id=_v(f, "test_id")),
                                              "結果を却下した。")[1],
    "apply": lambda c, u, rid, f, r: (changes.apply(c, u, rid, _v(f, "version_id")),
                                      "本番に適用した。申請者が「確認した」を押すと閉じる。")[1],
    "confirm": lambda c, u, rid, f, r: (changes.confirm(c, u, rid, True, r), "確認した。この申請を閉じた。")[1],
    "cannot_confirm": lambda c, u, rid, f, r: (changes.confirm(c, u, rid, False, r),
                                               "確認できないとして、閉じずに置いた。")[1],
    "rollback": lambda c, u, rid, f, r: changes.draft_rollback_of(c, u, rid, r or "確認できなかったので前の版に戻す"),
}
