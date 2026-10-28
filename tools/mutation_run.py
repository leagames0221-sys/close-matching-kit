"""Mutation tests: break the product code in 36 places (13 of the first release, 12 of the second, 6 of the third,
3 found when the change requests were reviewed, 2 added with the rules of 2026-10-26),
one at a time, run the tests, put the code back.

    .venv\\Scripts\\python tools\\mutation_run.py            (all sites; writes docs/validation/mutations.md)
    .venv\\Scripts\\python tools\\mutation_run.py 12,13,26-31   (only those sites; the other rows of the file are kept)

Each mutation is a one-place text replacement (the old text must occur exactly once). The file is restored
in a `finally` block and `git diff --stat -- close_kit` is checked at the end (it must print nothing).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (site name, file, old text, new text)
MUTATIONS = [
    ("1 自動の行を照合の残りから外す", "close_kit/matching.py",
     "        self.taken.update(ids)\n", "        pass  # MUTATION: automatic rows stay in the remainder\n"),
    ("2 候補を残す（後の規則に先の候補の行を使わせない）", "close_kit/matching.py",
     "        return [r for r in rows if r.id not in self.taken and r.id not in self.cands]\n",
     "        return [r for r in rows if r.id not in self.taken]\n"),
    ("3 差額 0 の判定（完了と差額の処理待ち）", "close_kit/matching.py",
     "        state = STATE_DONE if lt == rt or acct else STATE_DIFF\n", "        state = STATE_DONE\n"),
    ("4 許容差の境界（<= と <）", "close_kit/matching.py",
     "            amount_ok = abs(l.amount - r.amount) <= tol\n", "            amount_ok = abs(l.amount - r.amount) < tol\n"),
    ("5 相手が 2 つ以上の時に自動にしない", "close_kit/matching.py",
     '            elif len(ls) == 1 and len(rs) == 1 and c["kinds"] == ["full"]:\n',
     '            elif all(k == "full" for k in c["kinds"]):\n'),
    ("6 束ねる（束の合計で比べる）", "close_kit/matching.py",
     "            lt, rt = sum(r.amount for r in lrows), sum(r.amount for r in rrows)\n            amount_ok",
     "            lt, rt = lrows[0].amount, rrows[0].amount\n            amount_ok"),
    ("7 束の行の上限", "close_kit/matching.py",
     "            if len(rs) > limit:\n", "            if False:\n"),
    ("8 重複の行の扱い（照合に入れる）", "close_kit/matching.py",
     "        return [r for r in rows if r.id not in self.taken and r.id not in self.cands]\n",
     "        return [r for r in rows if r.id not in self.taken and r.id not in self.cands and r.id not in self.dups]\n"),
    ("9 残高の検算（全銀）", "close_kit/formats/zengin.py",
     "        if expected != after:\n", "        if False:\n"),
    ("10 取り込みの重複（行のキー）", "close_kit/ingest.py",
     "            dup = _existing_keys(conn, env, src[\"name\"], keys)\n", "            dup = set()\n"),
    ("11 本人の承認の拒否", "close_kit/manual.py",
     '    if g["created_by"] == user.id:\n', "    if False:\n"),
    ("12 役割の確かめ（サーバー）", "close_kit/roles.py",
     "    if not allowed(user, op):\n", "    if False:\n"),
    ("13 CSRF トークンの確かめ", "close_kit/web.py",
     '        if not isinstance(token, str) or not hmac.compare_digest(token.encode("utf-8"), csrf.encode("utf-8")):\n',
     "        if False:\n"),
    # ---- second release (sites 14-25)
    ("14 仕訳: 借方 = 貸方の確かめ", "close_kit/journals.py",
     "        if d != c:\n", "        if False:\n"),
    ("15 仕訳: 締めた期間の確かめ", "close_kit/journals.py",
     '        elif p["closed"]:\n', "        elif False:\n"),
    ("16 承認ルート: 閾値の境界（< と <=）", "close_kit/journals.py",
     '(r["upper"] is None or amount < r["upper"])', '(r["upper"] is None or amount <= r["upper"])'),
    ("17 仕訳: 作った本人の承認の拒否", "close_kit/journals.py",
     "    if created_by == user.id:\n", "    if False:\n"),
    ("18 承認ルート: 承認できる人の名前の確かめ", "close_kit/journals.py",
     '    if user.name not in r["approvers"]:\n', "    if False:\n"),
    ("19 作業: 前の作業の確かめ", "close_kit/tasks.py",
     "    if waiting:\n", "    if False:\n"),
    ("20 作業: 完了の条件", "close_kit/tasks.py",
     '    cond = d["condition"]\n    if cond == "なし":\n        return []\n',
     '    cond = d["condition"]\n    if True:\n        return []\n'),
    ("21 残高一致表: 符号（帳簿にあって銀行にまだ無い入金と出金）", "close_kit/balance.py",
     "    adj_bank = bank_end + transit_in - outstanding\n", "    adj_bank = bank_end + transit_in + outstanding\n"),
    ("22 残高一致表: 期末の時点の切り（日付 <= 期末日）", "close_kit/balance.py",
     'AND t.booking_date <= ? ORDER BY t.booking_date, t.id",', 'AND t.booking_date < ? ORDER BY t.booking_date, t.id",'),
    ("23 期日: 営業日の祝日の除き", "close_kit/holidays.py",
     "        if business and (d in table.names or d in company_holidays):\n",
     "        if business and (d in company_holidays):\n"),
    ("24 作業: 完了の取り消し", "close_kit/tasks.py",
     "        if not why:\n            continue\n", "        if True:\n            continue\n"),
    ("25 照合: 固定の状態に「仕訳の承認待ち」を入れる所", "close_kit/matchrun.py",
     'FIXED_STATES = ("完了", "差額の処理待ち", "承認待ち", "仕訳の承認待ち")',
     'FIXED_STATES = ("完了", "差額の処理待ち", "承認待ち")'),
    # ---- third release (sites 26-31): the change requests and the environments
    ("26 変更: 申請者の判定（自分の申請の承認・却下・適用の拒否）", "close_kit/changes.py",
     '    if req["requester_id"] == user.id:\n        msg = "申請者は', '    if False:\n        msg = "申請者は'),
    ("27 変更: 結果の承認の 2 人の数え（同じ人を 2 回数えない）", "close_kit/changes.py",
     '            if uid_b != uid_a and "管理者" in roles_b:\n', '            if "管理者" in roles_b:\n'),
    ("28 変更: 適用の WHERE version_id IS ?（試験の時の本番の版の条件）", "close_kit/versions.py",
     '"UPDATE environments SET version_id = ? WHERE name = ? AND version_id IS ?",',
     '"UPDATE environments SET version_id = ? WHERE name = ? AND (version_id IS ? OR 1)",'),
    ("29 版の読み込み: production への読み込みの拒否", "close_kit/versions.py",
     '    if env != config.ENV_SANDBOX:\n        raise ValueError("読み込み先は sandbox だけ', '    if False:\n        raise ValueError("読み込み先は sandbox だけ'),
    ("30 変更: 確認は申請者だけ", "close_kit/changes.py",
     '    req = _req(conn, rid)\n    if req["requester_id"] != user.id:\n        _denied(conn, user, "change_confirm"',
     '    req = _req(conn, rid)\n    if False:\n        _denied(conn, user, "change_confirm"'),
    ("31 転記ファイル: 環境の確かめ（production だけ）", "close_kit/posting.py",
     '    if env != config.ENV_PRODUCTION:\n        audit.log(conn, user_id=user.id, user_name=user.name, action="posting_export",',
     '    if False:\n        audit.log(conn, user_id=user.id, user_name=user.name, action="posting_export",'),
    # ---- found when the change requests were reviewed (sites 32-34)
    ("32 変更: 承認は画面の試験の結果に結ぶ（流し直した後の古い画面の承認の拒否）", "close_kit/changes.py",
     '    if test_id is not None and test_id != test["id"]:\n', "    if False:\n"),
    ("33 変更: 拒否の全部を操作の記録に残す（状態・空の欄・二度目の承認）", "close_kit/changes.py",
     "                if written is None:\n", "                if False:\n"),
    ("34 変更: 戻しの下書きは戻しの計画（15）の版へ", "close_kit/changes.py",
     '    back = req["rollback_version_id"] if req["rollback_version_id"] is not None else req["from_version_id"]\n',
     '    back = req["from_version_id"]\n'),
    # ---- added with the rules of 2026-10-26 (sites 35-36)
    ("35 束: 金額の許容差を使わない（Oracle: 1対多・多対1・多対多に許容差は無い）", "close_kit/matching.py",
     "            amount_ok = lt == rt\n", '            amount_ok = abs(lt - rt) <= rule["amount_tolerance_minor"]\n'),
    ("36 仕訳: 道具が下書きにした仕訳だけ、作った人以外も申請できる", "close_kit/journals.py",
     '    if j["created_by"] != user.id and j["method"] != METHOD_TOOL:\n        _denied(conn, user, "journal_submit"',
     '    if False:\n        _denied(conn, user, "journal_submit"'),
]


def site_number(name: str) -> int:
    return int(name.split(" ", 1)[0])


def wanted_sites(arg: str | None) -> set[int] | None:
    """"12,13,26-31" -> {12, 13, 26, ..., 31}; no argument = every site."""
    if not arg:
        return None
    out: set[int] = set()
    for part in arg.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


def kept_rows(path: Path) -> dict[int, str]:
    """The rows of the table of an earlier run, by site number."""
    rows: dict[int, str] = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\| (\d+) ", ln)
            if m:
                rows[int(m.group(1))] = ln
    return rows


def run_tests() -> tuple[int, list[str], str]:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--maxfail=80", "--no-header"],
                       cwd=ROOT, capture_output=True, text=True)
    out = p.stdout + p.stderr
    failed = re.findall(r"^FAILED (\S+)", out, flags=re.M)
    tail = [l for l in out.strip().splitlines() if l.strip()][-1]
    return p.returncode, failed, tail


def main(argv: list[str] | None = None) -> int:
    only = wanted_sites((argv if argv is not None else sys.argv[1:])[0] if (argv if argv is not None else sys.argv[1:]) else None)
    out_file = ROOT / "docs" / "validation" / "mutations.md"
    kept = kept_rows(out_file)
    code, failed, tail = run_tests()
    print("baseline:", tail)
    if code != 0:
        print("the tests are not green before the mutations; stop")
        return 1
    rows = []
    for name, rel, old, new in MUTATIONS:
        if only is not None and site_number(name) not in only:
            continue
        path = ROOT / rel
        raw = path.read_bytes()                        # bytes: line endings are restored exactly
        original = raw.decode("utf-8")
        crlf = "\r\n" in original
        norm = original.replace("\r\n", "\n")
        if norm.count(old) != 1:
            rows.append((name, rel, "(patch does not apply: old text occurs %d times)" % norm.count(old), []))
            continue
        mutated = norm.replace(old, new)
        try:
            path.write_bytes((mutated.replace("\n", "\r\n") if crlf else mutated).encode("utf-8"))
            code, failed, tail = run_tests()
        finally:
            path.write_bytes(raw)
        rows.append((name, rel, tail if code != 0 else "NOT DETECTED: tests stayed green", failed))
        print(name, "->", "red" if code else "GREEN (not detected)", len(failed), "failed")
    subprocess.run(["git", "update-index", "-q", "--refresh"], cwd=ROOT)
    status = subprocess.run(["git", "diff", "--stat", "--", "close_kit"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    lines = [
        "# Mutation tests", "",
        "Each row: the product code is broken in one place (`tools/mutation_run.py`), the whole test suite is run, the code is put back.",
        "A site counts as covered when at least one test goes red.", "",
        "| # / site | File | Result | Tests that went red (first 4 of N) |", "|---|---|---|---|"]
    fresh = {}
    for name, rel, res, failed in rows:
        first = "<br>".join(f.split("::", 1)[1] if "::" in f else f for f in failed[:4])
        fresh[site_number(name)] = f"| {name} | `{rel}` | {len(failed)} red | {first or res} |"
    for number in sorted({site_number(m[0]) for m in MUTATIONS}):
        lines.append(fresh.get(number) or kept[number])      # a site that was not run keeps its row of the earlier run
    lines += ["", f"After the run `git diff --stat -- close_kit` printed: {'(nothing: the code is back as it was)' if not status else status}", ""]
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(r[3] for r in rows) and not status else 1


if __name__ == "__main__":
    sys.exit(main())
