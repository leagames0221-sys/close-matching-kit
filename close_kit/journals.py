"""Journal entries: validation, drafts, submission, the approval route and the approval.

A journal is a header (number JE-000001, date, period, memo, who made it, how, what it settles, environment) and
lines (account code, debit, credit, memo). Amounts are integers in the smallest unit.

The same `validate` runs at three moments - when a draft is saved, when it is submitted, and when it is approved
(a period may have been closed, or an account removed, in between). It returns every problem with the rule number,
the line and the value; nothing is stored while one exists.

Approving needs all three: the role 承認者, a name in the "承認できる人" of the amount range that holds the debit total,
and being a different person from the one who made the journal (checked with the user id).
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import audit, config, roles, safefile, versions
from .formats.base import parse_amount, parse_date_text
from .money import display_multiplier, fmt_amount
from .roles import User
from .timeutil import now_iso

FORMULA_START = audit.FORMULA_START  # = + - @ TAB CR LF and the full-width variants
METHOD_TOOL = "照合の差額"   # a journal the tool drafted for the difference of a pair (its maker is config.SYSTEM_USER_NAME)
IMPORT_COLUMNS = ["伝票No.", "取引日付", "借方勘定科目", "借方金額", "貸方勘定科目", "貸方金額", "摘要"]


class JournalError(Exception):
    """The request cannot be carried out (the message is shown to the person)."""


class JournalInvalid(JournalError):
    def __init__(self, issues: list["Issue"]):
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues))


class NotAllowedToDecide(JournalError):
    pass


@dataclass
class Issue:
    rule: int
    line: int | None
    message: str

    def __str__(self) -> str:
        where = f" 行{self.line}" if self.line else ""
        return f"規則{self.rule}{where}: {self.message}"


# ---- validation ----------------------------------------------------------------------------------------------

def period_of(cfg: dict[str, Any], iso_date: str) -> dict[str, Any] | None:
    for p in cfg.get("periods", []):
        if p["start"] <= iso_date <= p["end"]:
            return p
    return None


def validate(cfg: dict[str, Any], entry_date: str, memo: str, lines: list[dict[str, Any]]) -> list[Issue]:
    """The five rules. `lines` = [{"account": str, "debit": int, "credit": int, "memo": str}]."""
    issues: list[Issue] = []
    m = display_multiplier(cfg)                           # amounts in the reasons are written like the screens write them
    # 1. debit total = credit total (integers in the smallest unit)
    ints = all(isinstance(x["debit"], int) and isinstance(x["credit"], int) for x in lines)
    if ints:
        d, c = sum(x["debit"] for x in lines), sum(x["credit"] for x in lines)
        if d != c:
            issues.append(Issue(1, None, f"借方の合計 {fmt_amount(d, m)} と貸方の合計 {fmt_amount(c, m)} が合わない（差 {fmt_amount(d - c, m)}）"))
    # 2. 2..999 lines; every line has exactly one positive side
    n = len(lines)
    if n < 2:
        issues.append(Issue(2, None, f"明細は 2 行以上にする（今は {n} 行）"))
    if n > config.JOURNAL_MAX_LINES:
        issues.append(Issue(2, None, f"明細は {config.JOURNAL_MAX_LINES} 行までにする（今は {n} 行）"))
    for i, x in enumerate(lines, start=1):
        dr, cr = x["debit"], x["credit"]
        if not isinstance(dr, int) or not isinstance(cr, int):
            issues.append(Issue(2, i, f"金額が整数ではない（借方 {dr!r}、貸方 {cr!r}）"))
        elif dr < 0 or cr < 0:
            issues.append(Issue(2, i, f"負の金額は使えない（借方 {fmt_amount(dr, m)}、貸方 {fmt_amount(cr, m)}）"))
        elif dr > 0 and cr > 0:
            issues.append(Issue(2, i, f"借方と貸方の両方に金額がある（借方 {fmt_amount(dr, m)}、貸方 {fmt_amount(cr, m)}）"))
        elif dr == 0 and cr == 0:
            issues.append(Issue(2, i, "金額が 0"))
    # 3. account codes exist in the chart of accounts of the version in use
    codes = {a["code"] for a in cfg.get("accounts", [])}
    for i, x in enumerate(lines, start=1):
        if x["account"] not in codes:
            issues.append(Issue(3, i, f"勘定科目に無いコード: {x['account']!r}"))
    # 4. the date is inside a period and that period is not closed
    try:
        date.fromisoformat(entry_date)
        p = period_of(cfg, entry_date)
        if p is None:
            issues.append(Issue(4, None, f"日付 {entry_date} はどの会計期間にも入らない"))
        elif p["closed"]:
            issues.append(Issue(4, None, f"日付 {entry_date} の期間 {p['name']} は締めてある"))
    except ValueError:
        issues.append(Issue(4, None, f"日付が読めない: {entry_date!r}"))
    # 5. no memo may start like a spreadsheet formula (the posting file cannot change values)
    if memo.startswith(FORMULA_START):
        issues.append(Issue(5, None, f"摘要が数式になる文字で始まる: {memo[:20]!r}"))
    for i, x in enumerate(lines, start=1):
        if (x.get("memo") or "").startswith(FORMULA_START):
            issues.append(Issue(5, i, f"摘要が数式になる文字で始まる: {x['memo'][:20]!r}"))
    return issues


def first_open_period_start(cfg: dict[str, Any], after: str) -> str | None:
    for p in sorted(cfg.get("periods", []), key=lambda p: p["start"]):
        if not p["closed"] and p["start"] > after:
            return p["start"]
    return None


def effective_date(cfg: dict[str, Any], iso_date: str) -> tuple[str, str]:
    """A date inside a closed period moves to the first day of the first open period after it (and says so)."""
    p = period_of(cfg, iso_date)
    if p is None or not p["closed"]:
        return iso_date, ""
    nxt = first_open_period_start(cfg, iso_date)
    if nxt is None:
        raise JournalError(f"日付 {iso_date} の期間 {p['name']} は締めてあり、その後に締めていない期間が無い")
    return nxt, f"日付 {iso_date} の期間 {p['name']} は締めてあるので、締めていない最初の期間の初日 {nxt} にした"


# ---- approval route -------------------------------------------------------------------------------------------

def route_for(cfg: dict[str, Any], amount: int) -> dict[str, Any] | None:
    """The amount range that holds `amount` (lower <= amount < upper; no upper = no limit)."""
    for r in cfg.get("approval_routes", []):
        if amount >= r["lower"] and (r["upper"] is None or amount < r["upper"]):
            return r
    return None


def can_decide(cfg: dict[str, Any], user: User, created_by: int, debit_total: int) -> str | None:
    """None if the user may approve / reject, else the reason (role is checked by roles.require before)."""
    if created_by == user.id:
        return "自分が作った仕訳は自分では承認も却下もできない（別の承認者が行う）"
    if "承認者" not in user.roles:
        return "役割「承認者」が無い"
    r = route_for(cfg, debit_total)
    m = display_multiplier(cfg)
    if r is None:
        return f"借方合計 {fmt_amount(debit_total, m)} に当たる承認ルートの幅が無い"
    if user.name not in r["approvers"]:
        return (f"借方合計 {fmt_amount(debit_total, m)} の幅（{fmt_amount(r['lower'], m)} 以上"
                + (f" {fmt_amount(r['upper'], m)} 未満" if r["upper"] is not None else "")
                + f"）の承認できる人に {user.name} が無い（{'、'.join(r['approvers'])}）")
    return None


# ---- storage ------------------------------------------------------------------------------------------------

def _line_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [{"account": r["account_code"], "debit": r["debit"], "credit": r["credit"], "memo": r["memo"]}
            for r in rows]


def _insert(conn: sqlite3.Connection, user: User, env: str, vid: int, cfg: dict[str, Any], entry_date: str,
            memo: str, lines: list[dict[str, Any]], method: str, *, link_group_id: int | None = None,
            link_txn_id: int | None = None, match_set: str = "", reason_code: str = "", slip_no: str = "") -> int:
    """Insert header and lines inside the caller's transaction. The caller has validated."""
    p = period_of(cfg, entry_date)
    debit_total = sum(x["debit"] for x in lines)
    cur = conn.execute(
        "INSERT INTO journals(env, number, entry_date, period, memo, method, status, debit_total, created_by,"
        " created_at, version_id, reason_code, link_group_id, link_txn_id, match_set, slip_no)"
        " VALUES (?, ?, ?, ?, ?, ?, '下書き', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (env, "tmp-" + now_iso(), entry_date, p["name"], memo, method, debit_total, user.id, now_iso(), vid,
         reason_code, link_group_id, link_txn_id, match_set, slip_no))
    jid = int(cur.lastrowid)
    conn.execute("UPDATE journals SET number = ? WHERE id = ?", (f"JE-{jid:06d}", jid))
    conn.executemany(
        "INSERT INTO journal_lines(journal_id, line_no, account_code, debit, credit, memo) VALUES (?, ?, ?, ?, ?, ?)",
        [(jid, i, x["account"], x["debit"], x["credit"], x.get("memo") or "") for i, x in enumerate(lines, 1)])
    return jid


def _denied(conn: sqlite3.Connection, user: User, action: str, env: str, vid: int | None, detail: str) -> None:
    audit.log(conn, user_id=user.id, user_name=user.name, action=action, outcome=audit.OUTCOME_DENIED, env=env,
              version_id=vid, detail=detail)


def _cfg(conn: sqlite3.Connection, env: str) -> tuple[int, dict[str, Any]]:
    vid, cfg = versions.load_config(conn, env)
    versions.require_current_schema(cfg)
    return vid, cfg


def create_manual(conn: sqlite3.Connection, user: User, entry_date: str, memo: str, lines: list[dict[str, Any]],
                  env: str = config.ENV_SANDBOX) -> int:
    """Save a journal typed on the screen as a draft. Refuses (and stores nothing) while a rule is broken."""
    roles.require(conn, user, "journal_create", env=env)
    vid, cfg = _cfg(conn, env)
    issues = validate(cfg, entry_date, memo, lines)
    if issues:
        _denied(conn, user, "journal_create", env, vid, "保存できない: " + " / ".join(str(i) for i in issues)[:3000])
        raise JournalInvalid(issues)
    with conn:
        jid = _insert(conn, user, env, vid, cfg, entry_date, memo, lines, "手入力")
    audit.log(conn, user_id=user.id, user_name=user.name, action="journal_create", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"仕訳 JE-{jid:06d} を下書きに保存（手入力）")
    return jid


# ---- CSV import -----------------------------------------------------------------------------------------------

@dataclass
class ImportResult:
    ok: bool
    journal_ids: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise JournalError("文字コードは UTF-8 か Shift_JIS（cp932）だけ受ける")


def _int_amount(text: str) -> int:
    d = parse_amount(text)
    if d != d.to_integral_value():
        raise ValueError(f"小数は使えない（最小単位の整数にする）: {text!r}")
    return int(d)


def parse_import(data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Group the rows by 伝票No. into slips. Returns ([{slip_no, date, memo, lines, rows}], errors)."""
    errors: list[str] = []
    try:
        safefile.check_text_file(data)
        text = _decode(data)
    except (safefile.FileRejected, JournalError) as e:
        return [], [str(e)]
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return [], ["ファイルが空"]
    idx = {h: i for i, h in enumerate(header)}
    missing = [c for c in IMPORT_COLUMNS if c not in idx]
    if missing:
        return [], ["見出しに無い列: " + "、".join(missing) + "（" + ",".join(IMPORT_COLUMNS) + "）"]
    slips: dict[str, dict[str, Any]] = {}
    line_no = 1
    for rec in reader:
        line_no += 1
        if not rec or all(not c.strip() for c in rec):
            continue
        if len(rec) < len(header):
            errors.append(f"{line_no} 行目: 列の数が見出しより少ない")
            continue
        g = {c: rec[idx[c]].strip() for c in IMPORT_COLUMNS}
        if not g["伝票No."]:
            errors.append(f"{line_no} 行目: 伝票No. が空")
            continue
        try:
            d = parse_date_text(g["取引日付"]).isoformat()
        except (ValueError, TypeError):
            errors.append(f"{line_no} 行目: 取引日付が読めない（西暦の年月日にする）: {g['取引日付']!r}")
            continue
        slip = slips.setdefault(g["伝票No."], {"slip_no": g["伝票No."], "date": d, "memo": g["摘要"],
                                                "lines": [], "rows": [], "first_row": line_no})
        if slip["date"] != d:
            errors.append(f"{line_no} 行目: 伝票No. {g['伝票No.']} の取引日付が行ごとに違う（{slip['date']} と {d}）")
            continue
        added = 0
        for side, acc_col, amt_col in (("debit", "借方勘定科目", "借方金額"), ("credit", "貸方勘定科目", "貸方金額")):
            acc, amt = g[acc_col], g[amt_col]
            if not acc and not amt:
                continue
            if not acc or not amt:
                errors.append(f"{line_no} 行目: {acc_col} と {amt_col} は両方書くか両方空にする")
                continue
            try:
                value = _int_amount(amt)
            except ValueError as e:
                errors.append(f"{line_no} 行目: {amt_col} が読めない（{e}）")
                continue
            slip["lines"].append({"account": acc, "debit": value if side == "debit" else 0,
                                  "credit": value if side == "credit" else 0, "memo": g["摘要"]})
            slip["rows"].append(line_no)
            added += 1
        if added == 0 and not any(e.startswith(f"{line_no} 行目") for e in errors):
            errors.append(f"{line_no} 行目: 借方も貸方も空")
    return list(slips.values()), errors


def import_csv(conn: sqlite3.Connection, user: User, data: bytes, env: str = config.ENV_SANDBOX) -> ImportResult:
    """All or nothing: one error anywhere and no journal is stored."""
    roles.require(conn, user, "journal_create", env=env)
    vid, cfg = _cfg(conn, env)
    slips, errors = parse_import(data)
    if not errors and not slips:
        errors.append("仕訳の行が 1 行も無い")
    if not errors:
        for s in slips:
            for i in validate(cfg, s["date"], s["memo"], s["lines"]):
                row = f"{s['rows'][i.line - 1]} 行目" if i.line else f"伝票No. {s['slip_no']}（{s['first_row']} 行目から）"
                errors.append(f"{row}: 規則{i.rule}: {i.message}")
    if errors:
        _denied(conn, user, "journal_import", env, vid, f"{len(errors)} 件の誤りで全部取り込まない: "
                + " / ".join(errors)[:3000])
        return ImportResult(False, errors=errors)
    with conn:
        ids = [_insert(conn, user, env, vid, cfg, s["date"], s["memo"], s["lines"], "取り込み", slip_no=s["slip_no"])
               for s in slips]
    audit.log(conn, user_id=user.id, user_name=user.name, action="journal_import", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"{len(ids)} 件の仕訳を下書きに保存（CSV の取り込み）")
    return ImportResult(True, journal_ids=ids)


# ---- life cycle -----------------------------------------------------------------------------------------------

def get(conn: sqlite3.Connection, jid: int, env: str) -> sqlite3.Row:
    j = conn.execute("SELECT * FROM journals WHERE id = ? AND env = ?", (jid, env)).fetchone()
    if j is None:
        raise JournalError(f"仕訳 {jid} は無い")
    return j


def lines_of(conn: sqlite3.Connection, jid: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM journal_lines WHERE journal_id = ? ORDER BY line_no", (jid,)).fetchall()


def release_links(conn: sqlite3.Connection, j: sqlite3.Row) -> None:
    """A withdrawn / rejected journal gives the group back to 差額の処理待ち (a row simply loses its mark). The group of a
    journal the tool drafted was already 完了 (the pair is matched); it goes back too, so that a person can deal with the
    difference by a reason code."""
    if j["link_group_id"] is not None:
        conn.execute("UPDATE match_groups SET state = '差額の処理待ち' WHERE id = ? AND state = '仕訳の承認待ち'",
                     (j["link_group_id"],))
        if j["method"] == METHOD_TOOL:
            conn.execute("UPDATE match_groups SET state = '差額の処理待ち' WHERE id = ? AND state = '完了'",
                         (j["link_group_id"],))


def _after(conn: sqlite3.Connection, env: str) -> None:
    from . import reasons, tasks
    reasons.refresh_adjusting_items(conn, env)
    tasks.recheck(conn, env)


def submit(conn: sqlite3.Connection, user: User, jid: int, env: str = config.ENV_SANDBOX) -> None:
    roles.require(conn, user, "journal_create", env=env)
    vid, cfg = _cfg(conn, env)
    j = get(conn, jid, env)
    if j["created_by"] != user.id and j["method"] != METHOD_TOOL:
        _denied(conn, user, "journal_submit", env, vid, f"仕訳 {j['number']}: 作った人だけが申請できる")
        raise JournalError("申請できるのは、その仕訳を作った人だけ（道具が下書きを作った仕訳は、役割「作成者」の誰でも）")
    if j["status"] != "下書き":
        raise JournalError(f"仕訳 {j['number']} は {j['status']} で、申請できる状態ではない")
    issues = validate(cfg, j["entry_date"], j["memo"], _line_dicts(lines_of(conn, jid)))
    if issues:
        _denied(conn, user, "journal_submit", env, vid, f"仕訳 {j['number']} を申請できない: "
                + " / ".join(str(i) for i in issues)[:3000])
        raise JournalInvalid(issues)
    with conn:
        conn.execute("UPDATE journals SET status = '承認待ち', submitted_at = ? WHERE id = ?", (now_iso(), jid))
    audit.log(conn, user_id=user.id, user_name=user.name, action="journal_submit", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"仕訳 {j['number']} を申請")


def withdraw(conn: sqlite3.Connection, user: User, jid: int, env: str = config.ENV_SANDBOX) -> None:
    roles.require(conn, user, "journal_create", env=env)
    j = get(conn, jid, env)
    if j["created_by"] != user.id and j["method"] != METHOD_TOOL:
        _denied(conn, user, "journal_withdraw", env, j["version_id"], f"仕訳 {j['number']}: 作った人だけが取り下げられる")
        raise JournalError("取り下げできるのは、その仕訳を作った人だけ（道具が下書きを作った仕訳は、役割「作成者」の誰でも）")
    if j["status"] not in ("下書き", "承認待ち"):
        raise JournalError(f"仕訳 {j['number']} は {j['status']} で、取り下げできる状態ではない")
    with conn:
        conn.execute("UPDATE journals SET status = '取り下げ' WHERE id = ?", (jid,))
        release_links(conn, j)
    audit.log(conn, user_id=user.id, user_name=user.name, action="journal_withdraw", outcome=audit.OUTCOME_OK,
              env=env, version_id=j["version_id"], detail=f"仕訳 {j['number']} を取り下げ（消さずに残す）")
    _after(conn, env)


def decide(conn: sqlite3.Connection, user: User, jid: int, approve: bool, note: str = "",
           env: str = config.ENV_SANDBOX) -> str:
    """Approve or reject a journal that is waiting. Returns the new status."""
    op = "承認" if approve else "却下"
    roles.require(conn, user, "journal_decide", env=env)
    vid, cfg = _cfg(conn, env)
    j = get(conn, jid, env)
    if j["status"] != "承認待ち":
        raise JournalError(f"仕訳 {j['number']} は {j['status']} で、{op}できる状態ではない")
    why = can_decide(cfg, user, j["created_by"], j["debit_total"])
    if why:
        _denied(conn, user, "journal_decide", env, vid, f"仕訳 {j['number']} を{op}できない: {why}")
        raise NotAllowedToDecide(why)
    if not approve and not note.strip():
        raise JournalError("却下には理由の文が要る")
    if approve:
        issues = validate(cfg, j["entry_date"], j["memo"], _line_dicts(lines_of(conn, jid)))
        if issues:  # period closed or account removed since the submission
            _denied(conn, user, "journal_decide", env, vid, f"仕訳 {j['number']} を承認できない: "
                    + " / ".join(str(i) for i in issues)[:3000])
            raise JournalInvalid(issues)
    route = route_for(cfg, j["debit_total"])
    new = "承認済み" if approve else "却下"
    with conn:
        conn.execute(
            "UPDATE journals SET status = ?, decided_by = ?, decided_at = ?, decision_version_id = ?,"
            " decision_route = ?, decision_note = ? WHERE id = ?",
            (new, user.id, now_iso(), vid, json.dumps({"version_id": vid, **(route or {})}, ensure_ascii=False),
             note.strip()[:500], jid))
        if approve:
            if j["link_group_id"] is not None:
                conn.execute("UPDATE match_groups SET state = '完了', decided_by = ?, decided_at = ? WHERE id = ?",
                             (user.id, now_iso(), j["link_group_id"]))
            if j["link_txn_id"] is not None:
                conn.execute("UPDATE match_rows SET klass = '処理済み', group_id = NULL WHERE env = ? AND match_set = ?"
                             " AND txn_id = ?", (env, j["match_set"], j["link_txn_id"]))
        else:
            release_links(conn, j)
    audit.log(conn, user_id=user.id, user_name=user.name, action="journal_decide", outcome=audit.OUTCOME_OK,
              env=env, version_id=vid, detail=f"仕訳 {j['number']}: {op} -> {new}"
              + ("" if approve else f"（理由: {note.strip()[:200]}）"))
    _after(conn, env)
    return new


# ---- reading --------------------------------------------------------------------------------------------------

def list_journals(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX,
                  status: str | None = None) -> list[sqlite3.Row]:
    roles.require(conn, user, "view", env=env)
    where, args = ["j.env = ?"], [env]
    if status:
        where.append("j.status = ?")
        args.append(status)
    return conn.execute(
        "SELECT j.*, cu.name AS created_name, du.name AS decided_name FROM journals j"
        " LEFT JOIN users cu ON cu.id = j.created_by LEFT JOIN users du ON du.id = j.decided_by"
        f" WHERE {' AND '.join(where)} ORDER BY j.id DESC", args).fetchall()


def journal_detail(conn: sqlite3.Connection, user: User, jid: int, env: str = config.ENV_SANDBOX
                   ) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    roles.require(conn, user, "view", env=env)
    j = conn.execute(
        "SELECT j.*, cu.name AS created_name, du.name AS decided_name FROM journals j"
        " LEFT JOIN users cu ON cu.id = j.created_by LEFT JOIN users du ON du.id = j.decided_by"
        " WHERE j.id = ? AND j.env = ?", (jid, env)).fetchone()
    if j is None:
        raise JournalError(f"仕訳 {jid} は無い")
    return j, lines_of(conn, jid)


CSV_HEADER = ["仕訳番号", "日付", "期間", "状態", "作った人", "作った方法", "借方合計", "摘要", "理由コード",
              "結び付いた物", "承認・却下した人", "決めた日時", "行", "勘定コード", "借方", "貸方", "行の摘要", "勘定科目名"]


def to_csv(conn: sqlite3.Connection, user: User, env: str = config.ENV_SANDBOX) -> str:
    roles.require(conn, user, "view", env=env)
    try:                                   # the name beside the code, from the chart of accounts now in use
        names = {a["code"]: a["name"] for a in versions.load_config(conn, env)[1].get("accounts", [])}
    except versions.NoConfig:
        names = {}
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    w.writerow(CSV_HEADER)
    for j in list_journals(conn, user, env)[::-1]:
        link = (f"グループ {j['link_group_id']}" if j["link_group_id"] else
                (f"行 {j['link_txn_id']}" if j["link_txn_id"] else ""))
        for ln in lines_of(conn, j["id"]):
            w.writerow([audit._cell(v) for v in (
                j["number"], j["entry_date"], j["period"], j["status"], j["created_name"], j["method"],
                j["debit_total"], j["memo"], j["reason_code"], link, j["decided_name"] or "", j["decided_at"] or "",
                ln["line_no"], ln["account_code"], ln["debit"], ln["credit"], ln["memo"],
                names.get(ln["account_code"], ""))])
    return out.getvalue()
