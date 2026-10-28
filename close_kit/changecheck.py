"""The test of a change request: the proposed settings version is tried on throw-away databases, never on the live ones.

Where: temporary SQLite files (the shape of `tools/run_splink.py` and `tools/make_samples.py`). The data of production is
copied with the standard `sqlite3.Connection.backup`. The sandbox and production databases are only read; nothing in this
module writes to the connection it is given, and no process-wide value (for example the scrypt cost in `auth`) is changed:
the people of the throw-away database are rows with a password hash that no password matches.

What is tried (docs/adr/0008-change-requests.md):
 1. production's data as it is now, matched again under the version in production and under the proposed one: the
    number of rows per class before and after, and for every row whose class changes, the row;
 2. the sample path of `samples/` (git) under the proposed version: the fee pair's journal (drafted by the tool when rule 10
    names an account for the difference, else made with a reason code), journals, an adjusting item, the next
    month's statements, and the difference of the balance statement;
 3. what the changed sheets do: the people who may approve at the edge of every amount range (and the journals waiting in
    production), the due date and completion condition of the tasks not finished in production, the pass rules (step 1).

What the test tries is the author's design decision (NIST SP 800-128, the closest source, says to test a change for
its security and functional impacts, not what to try; ADR 0008).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import (balance, changeinfo, config, db, ingest, journals, manual, matchrun, posting, reasons, roles,
               tasks, versions, workbook)
from .roles import User
from .timeutil import now_iso

SAMPLES_DIR = config.REPO_ROOT / "samples"
SAMPLE_WORKBOOK = "sample_workbook.xlsx"
SAMPLE_FILES = {"ledger_oct": "ledger_2026-10.csv", "bank_oct": "bank_2026-10_zengin.txt",
                "ledger_nov": "ledger_2026-11.csv", "bank_nov": "bank_2026-11_zengin.txt"}
SAMPLE_MATCH_SET = "預金照合"
SAMPLE_CODES = {"fee": "R01", "interest": "R03", "receipt": "R04"}
SAMPLE_APPROVER = "demo-approver"       # the sample's approver; a version that no longer names him fails the path
CLASS_ORDER = ("自動", "候補", "未照合", "重複", "手", "処理済み", "転記済み")
STATUS_OK = "成功"
STATUS_FAIL = "失敗（変更の影響）"

# the refusals of the domain that mean "this version does not let the path through" (not a fault of the tool)
IMPACT_ERRORS = (roles.PermissionDenied, journals.JournalError, reasons.ReasonError, manual.ManualError,
                 tasks.TaskError, posting.PostingError, matchrun.MatchSetNotFound, versions.NoConfig,
                 versions.SchemaTooOld, versions.VersionRejected)


class ToolError(Exception):
    """The tool itself failed (a bug, a full disk). The request is not moved to 試験済み."""


class StepFailed(Exception):
    def __init__(self, step: str, reason: str):
        self.step, self.reason = step, reason
        super().__init__(f"{step}: {reason}")


@dataclass
class Outcome:
    status: str
    result: dict[str, Any]
    prod_copy_at: str
    samples: dict[str, str]
    base_sha256: str
    to_sha256: str


def sha_of_version(conn: sqlite3.Connection, version_id: int | None) -> str:
    """sha256 of the normalised JSON of a version, computed again from the stored text (not read from the column)."""
    if version_id is None:
        return ""
    row = conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (version_id,)).fetchone()
    if row is None:
        raise ToolError(f"設定の版 {version_id} が無い")
    return hashlib.sha256(row["normalized_json"].encode("utf-8")).hexdigest()


def cfg_of_version(conn: sqlite3.Connection, version_id: int | None) -> dict[str, Any] | None:
    if version_id is None:
        return None
    row = conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (version_id,)).fetchone()
    return json.loads(row["normalized_json"]) if row else None


def samples_digest(samples_dir: Path = SAMPLES_DIR) -> dict[str, str]:
    """sha256 of every file in `samples/` (the files that git holds)."""
    if not samples_dir.is_dir():
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(samples_dir.iterdir()) if p.is_file()}


# ---- 1. production's data, before and after ----------------------------------------------------------------------

def _copy_of(src: sqlite3.Connection, path: Path) -> sqlite3.Connection:
    raw = sqlite3.connect(str(path))
    try:
        src.backup(raw)
    finally:
        raw.close()
    return db.connect(path)


def _classify(src: sqlite3.Connection, work: Path, tag: str, user: User, vid: int, cfg: dict[str, Any],
              env: str) -> tuple[dict[tuple[str, int], str], dict[str, dict[str, int]], list[str], sqlite3.Row | None]:
    """Match production's copy again under version `vid`; returns the class of every row, the counts per match set."""
    conn = _copy_of(src, work / f"prod_{tag}.sqlite3")
    try:
        conn.execute("UPDATE environments SET version_id = ? WHERE name = ?", (vid, env))
        conn.commit()
        notes: list[str] = []
        for ms in cfg["match_sets"]:
            if not ms["enabled"]:
                continue
            n = conn.execute("SELECT COUNT(*) FROM transactions WHERE env = ? AND source IN (?, ?)",
                             (env, ms["left"], ms["right"])).fetchone()[0]
            if n == 0:
                continue
            res = matchrun.run_match(conn, user, ms["name"], env=env)
            if res.skipped_bundles:
                notes.append(f"{ms['name']}: 上限を超えた束 {len(res.skipped_bundles)} 件は束にせず残りに戻った")
        klass = {(r["match_set"], r["txn_id"]): r["klass"] for r in conn.execute(
            "SELECT match_set, txn_id, klass FROM match_rows WHERE env = ?", (env,))}
        per_set: dict[str, dict[str, int]] = {}
        for (ms_name, _t), k in klass.items():
            per_set.setdefault(ms_name, Counter())[k] += 1
        return klass, {k: dict(v) for k, v in per_set.items()}, notes, None
    finally:
        conn.close()


def compare_on_production(src: sqlite3.Connection, user: User, base_vid: int | None, base_cfg: dict[str, Any] | None,
                          to_vid: int, to_cfg: dict[str, Any], env: str, work: Path) -> dict[str, Any]:
    rows = src.execute("SELECT COUNT(*) FROM transactions WHERE env = ?", (env,)).fetchone()[0]
    if base_vid is None or base_cfg is None:
        return {"status": "対象外", "note": "本番に設定の版がまだ無いので、前と後は比べない", "rows": rows}
    if rows == 0:
        return {"status": "データなし", "note": "本番にはまだ明細が取り込まれていない", "rows": 0}
    before, b_sets, b_notes, _ = _classify(src, work, "before", user, base_vid, base_cfg, env)
    try:
        after, a_sets, a_notes, _ = _classify(src, work, "after", user, to_vid, to_cfg, env)
    except IMPACT_ERRORS as e:     # the proposed version cannot even be matched against production's data
        return {"status": STATUS_FAIL, "rows": rows, "note": f"申請の版で照合を流し直せない: {e}"}
    keys = sorted(set(before) | set(after))
    changed = [(k, before.get(k, "(なし)"), after.get(k, "(なし)")) for k in keys if before.get(k) != after.get(k)]
    detail = []
    if changed:
        ids = [k[1] for k, _b, _a in changed[:config.CR_ROW_LIMIT_SHOWN]]
        info = {r["id"]: r for r in src.execute(
            f"SELECT id, source, booking_date, amount_minor, reference FROM transactions WHERE id IN ({','.join('?' * len(ids))})",
            ids)}
        for (ms_name, tid), b, a in changed[:config.CR_ROW_LIMIT_SHOWN]:
            t = info[tid]
            detail.append({"match_set": ms_name, "txn_id": tid, "source": t["source"], "date": t["booking_date"],
                           "amount_minor": t["amount_minor"], "reference": t["reference"], "before": b, "after": a})

    def total(per_set: dict[str, dict[str, int]]) -> dict[str, int]:
        c: Counter = Counter()
        for v in per_set.values():
            c.update(v)
        return {k: c.get(k, 0) for k in CLASS_ORDER}

    return {"status": "実施", "rows": rows, "before": total(b_sets), "after": total(a_sets),
            "before_by_set": b_sets, "after_by_set": a_sets, "changed_count": len(changed), "changed": detail,
            "shown_limit": config.CR_ROW_LIMIT_SHOWN, "notes": b_notes + a_notes}


# ---- 2. the sample path ------------------------------------------------------------------------------------------

@dataclass
class WalkResult:
    statements: dict[str, Any] = field(default_factory=dict)       # period -> balance.Statement
    approved_journals: int = 0
    adjusting_items: int = 0
    steps: list[str] = field(default_factory=list)


def sample_shape_mismatch(cfg: dict[str, Any], samples_dir: Path = SAMPLES_DIR) -> str:
    """"" when the data sources of `cfg` have the shape that the files of `samples/` need, else why not."""
    path = samples_dir / SAMPLE_WORKBOOK
    if not path.is_file():
        return f"samples/{SAMPLE_WORKBOOK} が無い"
    ref = workbook.parse_workbook(path.read_bytes(), SAMPLE_WORKBOOK)
    if not ref.ok or ref.config is None:
        return f"samples/{SAMPLE_WORKBOOK} が読めない"
    mine = {s["name"]: s for s in cfg.get("sources", [])}
    for s in ref.config["sources"]:
        o = mine.get(s["name"])
        if o is None:
            return f"データ源 {s['name']} が無い"
        for k in ("format", "account", "multiplier", "calendar", "row_key_cols", "csv", "direction"):
            if o.get(k) != s.get(k):
                return f"データ源 {s['name']} の {k} が見本と違う"
    if not any(m["name"] == SAMPLE_MATCH_SET and m["enabled"] for m in cfg.get("match_sets", [])):
        return f"照合セット {SAMPLE_MATCH_SET} が無い（か無効）"
    codes = {r["code"] for r in cfg.get("reason_codes", []) if r["enabled"]}
    missing = [c for c in SAMPLE_CODES.values() if c not in codes]
    if missing:
        return "理由コード " + "・".join(missing) + " が無い"
    names = {p["name"] for p in cfg.get("periods", [])}
    if not {"2026-10", "2026-11"} <= names:
        return "会計期間 2026-10・2026-11 が無い"
    return ""


def _make_user(conn: sqlite3.Connection, name: str, role_names: list[str]) -> User:
    """A row of the throw-away database. The hash is not a valid scrypt string, so no password opens it; no scrypt
    work is done and no global value is touched."""
    cur = conn.execute("INSERT INTO users(name, pw_hash, roles, created_at) VALUES (?, '!', ?, ?)",
                       (name, ",".join(sorted(role_names)), now_iso()))
    conn.commit()
    return User(int(cur.lastrowid), name, frozenset(role_names))


def walk_sample(workbook_bytes: bytes, files: dict[str, bytes], work: Path, today: date = date(2026, 11, 30)
                ) -> WalkResult:
    """The sample path on a throw-away database under the settings in `workbook_bytes`. Raises StepFailed when the
    settings do not let a step through; any other exception is the tool's own."""
    conn = db.connect(work / "walk.sqlite3")
    out = WalkResult()
    try:
        admin = _make_user(conn, "walk-admin", ["管理者"])
        maker = _make_user(conn, "walk-maker", ["作成者"])
        approver = _make_user(conn, SAMPLE_APPROVER, ["承認者"])

        def step(text: str, fn):
            try:
                value = fn()
            except IMPACT_ERRORS as e:
                raise StepFailed(text, str(e)) from None
            out.steps.append(text)
            return value

        res = workbook.parse_workbook(workbook_bytes, "version.xlsx")
        if not res.ok:
            raise StepFailed("設定の読み込み", "; ".join(str(e) for e in res.errors[:3]))
        step("設定の版を読み込む", lambda: versions.save_version(conn, admin, res, workbook_bytes))

        def do_ingest(source: str, name: str, key: str) -> None:
            r = ingest.ingest(conn, admin, source, name, files[key])
            if not r.ok:
                raise ingest_refused(r.reasons)

        def ingest_refused(reasons_: list[str]) -> Exception:
            return reasons.ReasonError("取り込みを拒否: " + " / ".join(reasons_)[:300])

        step("10月の帳簿を取り込む", lambda: do_ingest("帳簿", SAMPLE_FILES["ledger_oct"], "ledger_oct"))
        step("10月の銀行明細を取り込む", lambda: do_ingest("銀行", SAMPLE_FILES["bank_oct"], "bank_oct"))
        step("照合を実行する", lambda: matchrun.run_match(conn, admin, SAMPLE_MATCH_SET))

        def txn(reference: str, source: str) -> int:
            row = conn.execute("SELECT id FROM transactions WHERE reference = ? AND source = ?",
                               (reference, source)).fetchone()
            if row is None:
                raise reasons.ReasonError(f"見本の行 {reference} が無い")
            return int(row[0])

        def group_of(reference: str) -> int:
            row = conn.execute("SELECT group_id FROM match_rows WHERE txn_id = ?", (txn(reference, "帳簿"),)).fetchone()
            if row is None or row[0] is None:
                raise reasons.ReasonError(f"見本の行 {reference} は組になっていない")
            return int(row[0])

        def bank_only_row() -> int:
            row = conn.execute("SELECT id FROM transactions WHERE source = '銀行' AND reference = ''").fetchone()
            if row is None:
                raise reasons.ReasonError("見本の銀行だけにある行が無い")
            return int(row[0])

        def fee_journal():
            """The fee pair is 完了 with a journal the tool drafted when its rule names an account for the difference;
            under settings without that, it waits in 差額の処理待ち and the reason code R01 makes the journal."""
            gid = group_of("INV-2610-003")
            row = conn.execute("SELECT id, number FROM journals WHERE link_group_id = ? AND status = '下書き'",
                               (gid,)).fetchone()
            if row is not None:
                return reasons.Applied("仕訳", int(row["id"]), row["number"])
            return reasons.apply_reason(conn, maker, SAMPLE_MATCH_SET, SAMPLE_CODES["fee"], group_id=gid)

        fee = step("手数料の差の組の仕訳を用意する（規則が相手勘定を持てば道具の下書き、持たなければ理由コード R01）",
                   fee_journal)
        interest = step("理由コード R03（受取利息）を銀行だけの行に付ける", lambda: reasons.apply_reason(
            conn, maker, SAMPLE_MATCH_SET, SAMPLE_CODES["interest"], txn_id=bank_only_row()))
        item = step("理由コード R04（未達の入金）を月末の入金に付ける", lambda: reasons.apply_reason(
            conn, maker, SAMPLE_MATCH_SET, SAMPLE_CODES["receipt"], txn_id=txn("INV-2610-009", "帳簿")))

        def approve(j) -> None:
            journals.submit(conn, maker, j.id)
            if journals.decide(conn, approver, j.id, True) != "承認済み":
                raise journals.JournalError("承認済みにならなかった")

        step(f"仕訳 {fee.number} を申請して承認する", lambda: approve(fee))
        step(f"仕訳 {interest.number} を申請して承認する", lambda: approve(interest))
        step("調整項目を承認する", lambda: reasons.decide_item(conn, approver, item.id, True))
        step("11月の銀行明細を取り込む", lambda: do_ingest("銀行", SAMPLE_FILES["bank_nov"], "bank_nov"))
        step("11月の帳簿を取り込む", lambda: do_ingest("帳簿", SAMPLE_FILES["ledger_nov"], "ledger_nov"))
        step("照合をもう一度実行する", lambda: matchrun.run_match(conn, admin, SAMPLE_MATCH_SET))
        _vid, cfg = versions.load_config(conn, config.ENV_SANDBOX)
        for period in ("2026-10", "2026-11"):
            out.statements[period] = step(f"残高一致表 {period}", lambda p=period: balance.statement(
                conn, config.ENV_SANDBOX, cfg, SAMPLE_MATCH_SET, p, today))
        out.approved_journals = conn.execute("SELECT COUNT(*) FROM journals WHERE status = '承認済み'").fetchone()[0]
        out.adjusting_items = conn.execute(
            "SELECT COUNT(*) FROM adjusting_items WHERE status IN ('未解消', '解消')").fetchone()[0]
        return out
    finally:
        conn.close()


def statement_figures(st: Any) -> dict[str, Any]:
    return {"known": st.known, "why_unknown": st.why_unknown, "bank_end": st.bank_end, "transit_in": st.transit_in,
            "outstanding_out": st.outstanding_out, "adjusted_bank": st.adjusted_bank, "book_end": st.book_end,
            "book_movements": st.book_movements, "adjusted_book": st.adjusted_book, "difference": st.difference}


def sample_path(version_source: bytes, to_cfg: dict[str, Any], work: Path) -> dict[str, Any]:
    why = sample_shape_mismatch(to_cfg)
    if why:
        return {"status": "対象外", "note": f"見本とデータ源の形が違う版なので流さない（{why}）"}
    files = {k: (SAMPLES_DIR / name).read_bytes() for k, name in SAMPLE_FILES.items()}
    try:
        w = walk_sample(version_source, files, work)
    except StepFailed as e:
        return {"status": STATUS_FAIL, "step": e.step, "note": e.reason}
    return {"status": "実施", "steps": w.steps, "approved_journals": w.approved_journals,
            "adjusting_items": w.adjusting_items,
            "statements": {p: statement_figures(st) for p, st in w.statements.items()}}


# ---- 3. what the changed sheets do ---------------------------------------------------------------------------------

def sheet_effects(src: sqlite3.Connection, base_cfg: dict[str, Any] | None, to_cfg: dict[str, Any], env: str,
                  diff: list[dict[str, Any]], step1: dict[str, Any]) -> dict[str, Any]:
    if base_cfg is None:
        return {"status": "対象外", "note": "本番に設定の版がまだ無いので、変わったシートごとの効き目は見ない",
                "changed_sheets": changeinfo.changed_sheets(diff)}
    sheets = changeinfo.changed_sheets(diff)
    out: dict[str, Any] = {"status": "実施", "changed_sheets": sheets}
    out["approval_route"] = None
    if "承認ルート" in sheets:
        out["approval_route"] = {
            "boundaries": [{"amount": a, "before": changeinfo.approvers_at(base_cfg, a),
                            "after": changeinfo.approvers_at(to_cfg, a)}
                           for a in changeinfo.boundary_amounts(base_cfg, to_cfg)],
            "open_journals": changeinfo.impact_on_open_journals(src, base_cfg, to_cfg, env)}
    out["tasks"] = changeinfo.impact_on_open_tasks(src, base_cfg, to_cfg, env) if "作業一覧" in sheets else None
    if "パスルール" in sheets:
        out["pass_rules"] = {"note": "パスルールが変わった。区分ごとの件数の前と後は 1 の表",
                             "changed_count": step1.get("changed_count"), "step1_status": step1["status"]}
    else:
        out["pass_rules"] = None
    return out


# ---- the test -------------------------------------------------------------------------------------------------------

def run(src: sqlite3.Connection, user: User, to_version_id: int, env: str = config.ENV_PRODUCTION) -> Outcome:
    """Try version `to_version_id` against the live production in `src`. Only reads `src`."""
    try:
        base_vid = versions.env_version_id(src, env)
        base_cfg = cfg_of_version(src, base_vid)
        to_cfg = cfg_of_version(src, to_version_id)
        if to_cfg is None:
            raise ToolError(f"設定の版 {to_version_id} が無い")
        row = src.execute("SELECT source_bytes FROM config_versions WHERE id = ?", (to_version_id,)).fetchone()
        base_sha, to_sha = sha_of_version(src, base_vid), sha_of_version(src, to_version_id)
        diff = changeinfo.diff_configs(base_cfg, to_cfg)
        prod_copy_at = now_iso()
        with tempfile.TemporaryDirectory(prefix="close_kit_change_test_", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp)
            step1 = compare_on_production(src, user, base_vid, base_cfg, to_version_id, to_cfg, env, work)
            step2 = sample_path(bytes(row["source_bytes"]), to_cfg, work)
            step3 = sheet_effects(src, base_cfg, to_cfg, env, diff, step1)
        status = STATUS_FAIL if STATUS_FAIL in (step1["status"], step2["status"]) else STATUS_OK
        result = {"base_version_id": base_vid, "to_version_id": to_version_id, "step1": step1, "step2": step2,
                  "step3": step3}
        return Outcome(status, result, prod_copy_at, samples_digest(), base_sha, to_sha)
    except ToolError:
        raise
    except IMPACT_ERRORS as e:           # the production copy could not be matched under the version: its effect
        raise ToolError(f"試験の途中で拒否された: {e}") from e
    except Exception as e:  # noqa: BLE001 - the tool's own fault is reported as such, never as a result
        raise ToolError(f"{type(e).__name__}: {e}") from e
