r"""Make the numbers and the table of the test summary report from pytest's own output, not by hand.

    .venv\Scripts\python tools\test_report.py run        run pytest with --junitxml, then rewrite the generated block
    .venv\Scripts\python tools\test_report.py build --junit FILE      the same from a junit file that exists already
    .venv\Scripts\python tools\test_report.py check      is the report about the code that is here?

The test cases are the sections `### TC-nn ...` of docs/deliverables/test-cases.md; each lists the pytest tests that
check it (backticked `tests/test_x.py::test_name`). A case is
  - 合格   when every one of its pytest tests ran and passed (a parametrised test counts when all its parameters passed);
  - 不合格 when one of them failed or errored;
  - 保留   when it is marked as one that a person walks through in a browser, or a test was skipped.
A name in the cases file that is not in the junit file stops the script (nothing is written): a report never says "pass"
for a test that did not run. The five numbers are rows of the table of the report form (Planned, Run, Passed, Failed, Held; the form also has
Reviewed and To Be Run). The form says that passed + failed + held must match the number of test cases *reviewed*;
planned = passed + failed + held is the author's choice, not the form's.

The generated block of the report sits between two comment lines; the rest of the file (variances, incidents,
recommendations) is written by a person and is kept as it is.
"""
from __future__ import annotations

import argparse
import datetime as dt
import platform
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "docs" / "deliverables" / "test-cases.md"
REPORT = ROOT / "docs" / "deliverables" / "test-summary-report.md"
START = "<!-- generated:start -->"
END = "<!-- generated:end -->"
NAME = re.compile(r"`(tests/[\w./-]+\.py::\w+)`")
CASE_HEAD = re.compile(r"^###\s+(TC-\d+)\s+(.*?)\s*$")
BROWSER_MARK = "人が通すケース：はい"
REQ = re.compile(r"\bR-\d{2}\b")


class ReportError(Exception):
    pass


@dataclass
class Case:
    id: str
    title: str
    names: list[str] = field(default_factory=list)
    browser: bool = False
    reqs: list[str] = field(default_factory=list)


def parse_cases(text: str) -> list[Case]:
    cases: list[Case] = []
    cur: Case | None = None
    for line in text.splitlines():
        m = CASE_HEAD.match(line)
        if m:
            cur = Case(m.group(1), m.group(2))
            cases.append(cur)
            continue
        if line.startswith("## ") and cur is not None:       # a new chapter ends the case
            cur = None
        if cur is None:
            continue
        cur.names.extend(n for n in NAME.findall(line) if n not in cur.names)
        if BROWSER_MARK in line:
            cur.browser = True
        if line.lstrip().startswith(("- 要件", "- **要件")):
            cur.reqs.extend(r for r in REQ.findall(line) if r not in cur.reqs)
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ReportError("テストケースの番号が重なっている")
    for c in cases:
        if not c.names and not c.browser:
            raise ReportError(f"{c.id}: pytest の試験の名前も「{BROWSER_MARK}」も書いていない")
    return cases


def parse_junit(path: Path) -> dict[str, list[str]]:
    """pytest id (without the parameter) -> list of outcomes: passed | failed | skipped."""
    out: dict[str, list[str]] = {}
    root = ET.parse(path).getroot()
    for tc in root.iter("testcase"):
        classname, name = tc.get("classname", ""), tc.get("name", "")
        module = classname.replace(".", "/") + ".py"           # tests.test_x -> tests/test_x.py
        base = name.split("[", 1)[0]
        if tc.find("failure") is not None or tc.find("error") is not None:
            res = "failed"
        elif tc.find("skipped") is not None:
            res = "skipped"
        else:
            res = "passed"
        out.setdefault(f"{module}::{base}", []).append(res)
    return out


def judge(cases: list[Case], junit: dict[str, list[str]]) -> list[tuple[Case, str, list[str]]]:
    missing = sorted({n for c in cases for n in c.names if n not in junit})
    if missing:
        raise ReportError("junit に無い試験の名前（作るのを止める）:\n  " + "\n  ".join(missing))
    rows = []
    for c in cases:
        bad: list[str] = []
        held = c.browser
        for n in c.names:
            res = junit[n]
            if "failed" in res:
                bad.append(n)
            elif "skipped" in res:
                held = True
        if bad:
            rows.append((c, "不合格", bad))
        elif held:
            rows.append((c, "保留", []))
        else:
            rows.append((c, "合格", []))
    return rows


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def build_block(rows: list[tuple[Case, str, list[str]]], junit: dict[str, list[str]], *, commit: str, dirty: bool,
                when: str, pytest_version: str) -> str:
    passed = sum(1 for _c, r, _b in rows if r == "合格")
    failed = sum(1 for _c, r, _b in rows if r == "不合格")
    held = sum(1 for _c, r, _b in rows if r == "保留")
    planned = len(rows)
    assert planned == passed + failed + held
    all_tests = [o for v in junit.values() for o in v]
    used = {n for c, _r, _b in rows for n in c.names}
    free = [k for k in junit if k not in used]
    lines = [
        START,
        f"<!-- この区画は tools/test_report.py が pytest の junit から作った。手で直さない（{when}） -->",
        "",
        "| 項目 | 値 |",
        "|---|---|",
        f"| 試験した commit | `{commit}`{'（作業木に未 commit の変更があった）' if dirty else '（作業木は commit 済みと同じ）'} |",
        f"| 日時（UTC） | {when} |",
        f"| Python | {platform.python_version()}（{platform.system()} {platform.release()}） |",
        f"| pytest | {pytest_version} |",
        f"| pytest 全体 | {len(all_tests)} 本中 {sum(1 for o in all_tests if o == 'passed')} 本が合格、"
        f"{sum(1 for o in all_tests if o == 'failed')} 本が不合格、{sum(1 for o in all_tests if o == 'skipped')} 本を飛ばした |",
        "",
        "| テストケースの数 | 値 |",
        "|---|---|",
        f"| 予定（Planned） | {planned} |",
        f"| 実行（Run） | {passed + failed} |",
        f"| 合格（Passed） | {passed} |",
        f"| 不合格（Failed） | {failed} |",
        f"| 保留（Held） | {held} |",
        "",
        f"予定 = 合格 + 不合格 + 保留（{planned} = {passed} + {failed} + {held}）。実行 = 合格 + 不合格。"
        "保留は、ブラウザで人が通すケースか、飛ばした試験を含むケース。",
        "",
        "| 番号 | ケース | 要件 | 結果 | 結ぶ pytest の試験の数 | 不合格の試験 |",
        "|---|---|---|---|---|---|",
    ]
    for c, r, bad in rows:
        lines.append(f"| {c.id} | {c.title} | {'・'.join(c.reqs)} | {r} | {len(c.names)} | "
                     f"{'<br>'.join('`' + b + '`' for b in bad)} |")
    lines += ["", f"どのケースにも結んでいない pytest の試験（部品ごとの細かい試験）：{len(free)} 本。", END]
    return "\n".join(lines)


def write_report(block: str, report: Path) -> None:
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    if START in text and END in text:
        head, rest = text.split(START, 1)
        _old, tail = rest.split(END, 1)
        new = head + block + tail
    else:
        raise ReportError(f"{report} に {START} と {END} の行が無い（区画を置いてから流す）")
    report.write_text(new, encoding="utf-8")


def build(junit_path: Path, cases_path: Path = CASES, report: Path = REPORT, *, commit: str | None = None) -> str:
    cases = parse_cases(cases_path.read_text(encoding="utf-8"))
    junit = parse_junit(junit_path)
    rows = judge(cases, junit)
    import pytest
    block = build_block(rows, junit, commit=commit or git("rev-parse", "HEAD"),
                        dirty=bool(git("status", "--porcelain", "--", "close_kit", "tools", "tests", "samples")),
                        when=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        pytest_version=pytest.__version__)
    write_report(block, report)
    return block


def cmd_run(args: argparse.Namespace) -> int:
    junit = ROOT / "var" / "junit.xml"
    junit.parent.mkdir(exist_ok=True)
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"], cwd=ROOT)
    if not junit.exists():
        print("pytest が junit を作らなかった", file=sys.stderr)
        return 2
    try:
        build(junit, CASES, REPORT)
    except ReportError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"{REPORT} の生成区画を書き直した（pytest の終了コード {p.returncode}）")
    return p.returncode


def cmd_build(args: argparse.Namespace) -> int:
    try:
        build(Path(args.junit), CASES, REPORT)
    except ReportError as e:
        print(e, file=sys.stderr)
        return 2
    print(f"{REPORT} の生成区画を書き直した")
    return 0


REPORT_ROW = re.compile(r"^\| (TC-\d+) \| (.*?) \| .*? \| (?:合格|不合格|保留) \| (\d+) \|", re.M)


def rows_differ(cases_text: str, report_text: str) -> list[str]:
    """What does not match between the cases file and the case rows of the report's generated block: every case is one
    row, in the same order, with the same title and the same number of pytest names. Empty = they match (a docs-only
    commit after the report must not have changed the cases the report was made from)."""
    if START not in report_text or END not in report_text:
        return []
    block = report_text.split(START, 1)[1].split(END, 1)[0]
    rows = [(i, t, int(n)) for i, t, n in REPORT_ROW.findall(block)]
    want = [(c.id, c.title, len(c.names)) for c in parse_cases(cases_text)]
    if rows == want:
        return []
    out = []
    ids_r, ids_w = [r[0] for r in rows], [w[0] for w in want]
    if ids_r != ids_w:
        out.append(f"ケースの番号が報告の行と 1 対 1 でない（ケース {ids_w}、報告 {ids_r}）")
    for r, w in zip(rows, want):
        if r[0] == w[0] and r != w:
            out.append(f"{w[0]}: 報告は「{r[1]}」・名前 {r[2]} 個、ケースは「{w[1]}」・名前 {w[2]} 個")
    return out


def cmd_check(args: argparse.Namespace) -> int:
    """The commit in the report is HEAD, or every commit after it touched only documents: files under docs/ and the
    .md files at the top (the report itself is committed after it is made, so HEAD moves by exactly that). And the case
    rows of the report are the cases of `test-cases.md`, one to one (a document commit may not change them unseen)."""
    text = REPORT.read_text(encoding="utf-8")
    m = re.search(r"試験した commit \| `([0-9a-f]{7,40})`", text)
    if not m:
        print("報告に commit が書いていない", file=sys.stderr)
        return 2
    differ = rows_differ(CASES.read_text(encoding="utf-8"), text) if CASES.exists() else []
    if differ:
        print("NG: 報告を作った後にテストケースが変わっている。報告を作り直す:\n  " + "\n  ".join(differ), file=sys.stderr)
        return 1
    rec, head = m.group(1), git("rev-parse", "HEAD")
    if head.startswith(rec):
        print(f"OK: 報告の commit {rec[:12]} は今の HEAD")
        return 0
    changed = git("diff", "--name-only", f"{rec}..HEAD").splitlines()
    outside = [f for f in changed if not (f.startswith("docs/") or ("/" not in f and f.endswith(".md")))]
    if outside:
        print(f"NG: 報告の commit {rec[:12]} の後に文書の外が変わっている: {outside[:5]}", file=sys.stderr)
        return 1
    print(f"OK: 報告の commit {rec[:12]} の後の変更は文書（docs/ と最上位の .md）だけ（HEAD {head[:12]}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools/test_report.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    b = sub.add_parser("build")
    b.add_argument("--junit", required=True)
    b.set_defaults(fn=cmd_build)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
