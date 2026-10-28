"""What a change request says about the change itself, computed from two normalised settings (the version in
production and the version proposed). Nothing here writes to the database.

 - `diff_configs`          the rows that differ, sheet by sheet (item "what changes" of the request form)
 - `impact_on_open_items`  the parts of production this version touches: journals waiting for approval whose approvers
                           change, unfinished tasks whose completion condition or due date change, the match sets used
 - `security_impact`       the controls that change: the approval route, who prepares and who checks a task, closing
 - `widened_approvers`     people who can approve a wider range of amounts afterwards (the screen marks them)

The form of the request is NIST SP 800-128 appendix E (items numbered there); the automatic ones are marked in
docs/adr/0008-change-requests.md.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from . import holidays
from .journals import route_for

# normalised key -> (sheet name of the workbook, function that gives the identity of a row)
SHEETS: dict[str, tuple[str, Any]] = {
    "periods": ("会計期間", lambda r: r["name"]),
    "accounts": ("勘定科目", lambda r: r["code"]),
    "sources": ("データ源", lambda r: r["name"]),
    "match_sets": ("照合セット", lambda r: r["name"]),
    "display_fields": ("表示項目", lambda r: f"{r['match_set']} / {r['field']}"),
    "subtotal_fields": ("小計項目", lambda r: f"{r['match_set']} / {r['field']}"),
    "pass_rules": ("パスルール", lambda r: f"{r['match_set']} / {r['order']}"),
    "reason_codes": ("理由コード", lambda r: r["code"]),
    "approval_routes": ("承認ルート", lambda r: f"{r['lower']} 以上"),
    "tasks": ("作業一覧", lambda r: r["id"]),
    "company_holidays": ("会社の休日", lambda r: r["date"]),
}
DESCRIPTION = "説明"

# For each key of the normalised settings: the header of the column of the workbook behind each internal field name,
# as the person wrote it in that sheet (workbook.SHEETS has the headers; a test checks every label is one of them).
_CSV_HEADERS = {"date": "CSV日付列", "amount": "CSV金額列", "reference": "CSV参照列", "memo": "CSV摘要列",
                "balance": "CSV残高列"}
FIELD_LABELS: dict[str, dict[str, str]] = {
    "description": {"schema_version": "schemaの版", "company": "会社名", "author": "作成者", "updated": "更新日",
                    "company_code": "会社コード", "journal_type": "仕訳の種類",
                    "task_offset_min": "期日の日数の下限", "task_offset_max": "期日の日数の上限"},
    "periods": {"name": "期間の名前", "start": "開始日", "end": "終了日", "closed": "締め"},
    "accounts": {"code": "勘定コード", "name": "名前", "class": "区分"},
    "sources": {"name": "名前", "format": "形式", "account": "口座", "direction": "向き", "multiplier": "最小単位の倍率",
                "calendar": "日付の暦", "row_key_cols": "行のキーの列",
                **{f"csv.{k}": v for k, v in _CSV_HEADERS.items()}},
    "match_sets": {"name": "名前", "left": "左のデータ源", "right": "右のデータ源", "enabled": "有効",
                   "bank_side": "銀行の側", "deposit_account": "預金の勘定", "reconcile": "残高一致に使う"},
    "display_fields": {"match_set": "照合セット", "field": "項目", "order": "順番"},
    "subtotal_fields": {"match_set": "照合セット", "field": "項目"},
    "pass_rules": {"match_set": "照合セット", "order": "順番", "name": "名前", "kind": "種別", "type": "型",
                   "keys": "一致させる項目", "date_window": "date_window",
                   "amount_tolerance_minor": "amount_tolerance_minor", "left_bundle": "左の束ねるキー",
                   "right_bundle": "右の束ねるキー", "pair_key": "束どうしを組にするキー",
                   "bundle_limit": "1束の行の上限", "enabled": "有効", "diff_account": "差額の相手勘定"},
    "reason_codes": {"code": "コード", "name": "名前", "action": "処理", "counter_account": "相手勘定",
                     "enabled": "有効"},
    "approval_routes": {"lower": "下限", "upper": "上限", "approvers": "承認できる人"},
    "tasks": {"id": "作業ID", "name": "名前", "type": "種類", "period": "対象の期間", "counting": "数え方",
              "offset": "期末からの日数", "predecessors": "前の作業", "preparer": "作成者", "reviewer": "確認者",
              "condition": "完了の条件", "condition_match_set": "条件の照合セット", "enabled": "有効"},
    "company_holidays": {"date": "日付", "name": "名前"},
}


def _show(v: Any) -> str:
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if v is None:
        return "(空)"
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def _flat(row: dict[str, Any]) -> dict[str, Any]:
    """The row with the CSV columns of a data source one by one (`csv.date` ...), so each shows under its own header."""
    out = {}
    for k, v in row.items():
        if isinstance(v, dict) and k == "csv":
            out.update({f"csv.{kk}": vv for kk, vv in v.items()})
        else:
            out[k] = v
    return out


def _row_changes(old: dict[str, Any], new: dict[str, Any], labels: dict[str, str]) -> list[dict[str, str]]:
    old, new = _flat(old), _flat(new)
    out = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            out.append({"field": labels.get(k, k), "old": _show(old.get(k)) if k in old else "(無い)",
                        "new": _show(new.get(k)) if k in new else "(無い)"})
    return out


def diff_configs(old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    """Every row that differs: {"sheet", "key", "change": 追加|削除|変更, "fields": [{"field","old","new"}]}.
    With no old settings (nothing in production yet) every row of the workbook is 追加."""
    old = old or {}
    out: list[dict[str, Any]] = []
    if (old.get("description") or {}) != (new.get("description") or {}):
        out.append({"sheet": DESCRIPTION, "key": "(1 行だけ)", "change": "変更" if old.get("description") else "追加",
                    "fields": _row_changes(old.get("description") or {}, new.get("description") or {},
                                           FIELD_LABELS["description"])})
    for key, (sheet, ident) in SHEETS.items():
        o = {ident(r): r for r in old.get(key, [])}
        n = {ident(r): r for r in new.get(key, [])}
        for k in sorted(set(o) | set(n), key=str):
            if k not in o:
                out.append({"sheet": sheet, "key": str(k), "change": "追加", "fields": []})
            elif k not in n:
                out.append({"sheet": sheet, "key": str(k), "change": "削除", "fields": []})
            elif o[k] != n[k]:
                out.append({"sheet": sheet, "key": str(k), "change": "変更", "fields": _row_changes(o[k], n[k], FIELD_LABELS[key])})
    return out


def changed_sheets(diff: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for d in diff:
        if d["sheet"] not in seen:
            seen.append(d["sheet"])
    return seen


# ---- approval route ----------------------------------------------------------------------------------------------

def approvers_at(cfg: dict[str, Any] | None, amount: int) -> list[str] | None:
    """Names that may approve a journal with this debit total; None when there is no settings version or no range."""
    if not cfg:
        return None
    r = route_for(cfg, amount)
    return None if r is None else list(r["approvers"])


def boundary_amounts(old: dict[str, Any] | None, new: dict[str, Any]) -> list[int]:
    """The amounts just below and at every edge of the ranges of either route: 0, lower - 1, lower, upper - 1."""
    pts = {0}
    for cfg in (old or {}, new):
        for r in cfg.get("approval_routes", []):
            pts.add(r["lower"])
            if r["lower"] > 0:
                pts.add(r["lower"] - 1)
            if r["upper"] is not None:
                pts.add(r["upper"] - 1)
                pts.add(r["upper"])
    return sorted(pts)


def _ranges(cfg: dict[str, Any] | None) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = {}
    for r in (cfg or {}).get("approval_routes", []):
        for name in r["approvers"]:
            out.setdefault(name, []).append((r["lower"], float("inf") if r["upper"] is None else r["upper"]))
    return out


def _subtract(a: list[tuple[int, float]], b: list[tuple[int, float]]) -> list[tuple[int, float]]:
    """The parts of the intervals `a` that `b` does not cover (half-open intervals)."""
    out = sorted(a)
    for lo, hi in sorted(b):
        nxt: list[tuple[int, float]] = []
        for x, y in out:
            if hi <= x or lo >= y:
                nxt.append((x, y))
                continue
            if x < lo:
                nxt.append((x, lo))
            if hi < y:
                nxt.append((int(hi), y))
        out = nxt
    return out


def widened_approvers(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, list[str]]:
    """Name -> the ranges of amount that this person can approve after the change and could not before.
    A person who was not in the old route at all gets every range of the new one."""
    before, after = _ranges(old), _ranges(new)
    out: dict[str, list[str]] = {}
    for name, ranges in after.items():
        extra = _subtract(ranges, before.get(name, []))
        if extra:
            out[name] = [f"{lo:,} 以上" + ("" if hi == float("inf") else f" {int(hi):,} 未満") for lo, hi in extra]
    return out


# ---- what the change touches in production -----------------------------------------------------------------------

def impact_on_open_journals(conn: sqlite3.Connection, old: dict[str, Any] | None, new: dict[str, Any],
                            env: str) -> list[dict[str, Any]]:
    """Journals waiting for approval in `env` whose list of people who may approve differs between the two."""
    out = []
    for j in conn.execute("SELECT number, debit_total FROM journals WHERE env = ? AND status = '承認待ち'"
                          " ORDER BY id", (env,)):
        b, a = approvers_at(old, j["debit_total"]), approvers_at(new, j["debit_total"])
        if (None if b is None else sorted(b)) != (None if a is None else sorted(a)):
            out.append({"journal": j["number"], "debit_total": j["debit_total"],
                        "before": b, "after": a})
    return out


def impact_on_open_tasks(conn: sqlite3.Connection, old: dict[str, Any] | None, new: dict[str, Any],
                         env: str) -> list[dict[str, Any]]:
    """Unfinished tasks of `env` whose completion condition or due date would differ under the new settings. A task
    keeps the definition it was made with until the period's tasks are made again, so this lists what changes then."""
    out = []
    new_tasks = {t["id"]: t for t in new.get("tasks", []) if t["enabled"]}
    periods = {p["name"]: p for p in new.get("periods", [])}
    table = holidays.load()
    company = frozenset(date.fromisoformat(h["date"]) for h in new.get("company_holidays", []))
    for row in conn.execute("SELECT task_id, period, name, definition_json, due_date FROM task_instances"
                            " WHERE env = ? AND status <> '完了' ORDER BY id", (env,)):
        d = json.loads(row["definition_json"])
        t = new_tasks.get(row["task_id"])
        if t is None:
            out.append({"period": row["period"], "task_id": row["task_id"], "name": row["name"],
                        "condition_before": d["condition"], "condition_after": "(新しい版に無い)",
                        "due_before": row["due_date"] or "", "due_after": "(新しい版に無い)"})
            continue
        p = periods.get(row["period"])
        due_after = ""
        if p is not None:
            due = holidays.due_date(date.fromisoformat(p["end"]), t["counting"], t["offset"], table=table,
                                    company_holidays=company)
            due_after = due.date.isoformat() if due.date else due.note
        else:
            due_after = "(期間が新しい版に無い)"
        cond_b = f"{d['condition']}" + (f"（{d['condition_match_set']}）" if d.get("condition_match_set") else "")
        cond_a = f"{t['condition']}" + (f"（{t['condition_match_set']}）" if t.get("condition_match_set") else "")
        if cond_b != cond_a or (row["due_date"] or "") != due_after:
            out.append({"period": row["period"], "task_id": row["task_id"], "name": row["name"],
                        "condition_before": cond_b, "condition_after": cond_a,
                        "due_before": row["due_date"] or "", "due_after": due_after})
    return out


def match_sets_used(conn: sqlite3.Connection, old: dict[str, Any] | None, new: dict[str, Any], env: str
                    ) -> list[dict[str, Any]]:
    """The match sets of the new version, with whether the old one had them and how many rows `env` holds for them."""
    old_names = {m["name"] for m in (old or {}).get("match_sets", [])}
    out = []
    for m in new.get("match_sets", []):
        n = conn.execute("SELECT COUNT(*) FROM transactions WHERE env = ? AND source IN (?, ?)",
                         (env, m["left"], m["right"])).fetchone()[0]
        out.append({"name": m["name"], "enabled": m["enabled"], "in_old": m["name"] in old_names, "rows": n})
    return out


def impact(conn: sqlite3.Connection, old: dict[str, Any] | None, new: dict[str, Any], env: str) -> dict[str, Any]:
    return {"open_journals": impact_on_open_journals(conn, old, new, env),
            "open_tasks": impact_on_open_tasks(conn, old, new, env),
            "match_sets": match_sets_used(conn, old, new, env)}


def security_impact(diff: list[dict[str, Any]], old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """The control-related changes: the approval route, preparer / reviewer of tasks, closing of periods."""
    route = [d for d in diff if d["sheet"] == "承認ルート"]
    people = []
    closing = []
    for d in diff:
        if d["sheet"] == "作業一覧":
            for f in d["fields"]:
                if f["field"] in ("作成者", "確認者"):
                    people.append({"task": d["key"], "field": f["field"], "old": f["old"], "new": f["new"]})
            if d["change"] != "変更":
                people.append({"task": d["key"], "field": "作業", "old": "(無い)" if d["change"] == "追加" else "",
                               "new": "追加" if d["change"] == "追加" else "削除"})
        if d["sheet"] == "会計期間":
            for f in d["fields"]:
                if f["field"] == "締め":
                    closing.append({"period": d["key"], "old": f["old"], "new": f["new"]})
            if d["change"] != "変更":
                closing.append({"period": d["key"], "old": "(無い)" if d["change"] == "追加" else "",
                                "new": d["change"]})
    return {"approval_route": route, "task_people": people, "closing": closing,
            "widened": widened_approvers(old, new),
            "any": bool(route or people or closing)}
