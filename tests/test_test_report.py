"""The script that makes the numbers of the test summary report from pytest's junit output (tools/test_report.py), and
the cases file it reads: the five numbers add up, a name that did not run stops it, and the cases file names real tests."""
import ast
import re
from pathlib import Path

import pytest

from tools import test_report as tr

ROOT = Path(__file__).resolve().parent.parent

CASES = """# テストケース仕様

## 4. ケースの一覧

### TC-01 合格するはずのケース
- 要件：R-01
- pytest：`tests/test_a.py::test_one`、`tests/test_a.py::test_two`

### TC-02 一部が不合格のケース
- 要件：R-02・R-03
- pytest：`tests/test_a.py::test_one`、`tests/test_b.py::test_bad`

### TC-03 飛ばした試験を含むケース
- 要件：R-04
- pytest：`tests/test_b.py::test_skipped`

### TC-04 ブラウザで人が通すケース
- 要件：R-05
- 人が通すケース：はい
- pytest：`tests/test_a.py::test_two`

## 5. 付録
"""

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5">
<testcase classname="tests.test_a" name="test_one" time="0.1"/>
<testcase classname="tests.test_a" name="test_two[x]" time="0.1"/>
<testcase classname="tests.test_a" name="test_two[y]" time="0.1"/>
<testcase classname="tests.test_b" name="test_bad" time="0.1"><failure message="boom">boom</failure></testcase>
<testcase classname="tests.test_b" name="test_skipped" time="0.1"><skipped message="no git"/></testcase>
<testcase classname="tests.test_b" name="test_free" time="0.1"/>
</testsuite></testsuites>"""

REPORT = "# 報告\n\n手で書く所\n\n" + tr.START + "\n古い物\n" + tr.END + "\n\n手で書く所 その後\n"


def files(tmp_path, cases=CASES, junit=JUNIT, report=REPORT):
    c, j, r = tmp_path / "cases.md", tmp_path / "junit.xml", tmp_path / "report.md"
    c.write_text(cases, encoding="utf-8")
    j.write_text(junit, encoding="utf-8")
    r.write_text(report, encoding="utf-8")
    return c, j, r


def test_the_five_numbers_follow_from_the_junit_file(tmp_path):
    c, j, r = files(tmp_path)
    block = tr.build(j, c, r, commit="abc1234")
    text = r.read_text(encoding="utf-8")
    assert "| 予定（Planned） | 4 |" in text and "| 実行（Run） | 2 |" in text and "| 合格（Passed） | 1 |" in text
    assert "| 不合格（Failed） | 1 |" in text and "| 保留（Held） | 2 |" in text
    assert "予定 = 合格 + 不合格 + 保留（4 = 1 + 1 + 2）" in text
    assert "`abc1234`" in text and "Python" in text and "日時（UTC）" in text
    row = {m.group(1): m.group(2) for m in re.finditer(r"^\| (TC-\d\d) \|.*\| (合格|不合格|保留) \|", text, flags=re.M)}
    assert row == {"TC-01": "合格", "TC-02": "不合格", "TC-03": "保留", "TC-04": "保留"}
    assert "`tests/test_b.py::test_bad`" in text                    # the failed test is named
    assert "どのケースにも結んでいない pytest の試験（部品ごとの細かい試験）：1 本" in text      # test_free
    assert block.startswith(tr.START) and block.endswith(tr.END)
    assert text.startswith("# 報告\n\n手で書く所\n") and text.endswith("手で書く所 その後\n")      # the rest is kept
    assert "古い物" not in text


def test_a_name_that_is_not_in_the_junit_file_stops_the_script_and_writes_nothing(tmp_path):
    c, j, r = files(tmp_path, cases=CASES.replace("test_two`", "test_three`"))
    with pytest.raises(tr.ReportError) as e:
        tr.build(j, c, r, commit="abc1234")
    assert "tests/test_a.py::test_three" in str(e.value)
    assert r.read_text(encoding="utf-8") == REPORT                   # untouched: no "pass" for a test that never ran


def test_a_parametrised_test_passes_only_when_every_parameter_passed(tmp_path):
    junit = JUNIT.replace('<testcase classname="tests.test_a" name="test_two[y]" time="0.1"/>',
                          '<testcase classname="tests.test_a" name="test_two[y]" time="0.1"><error message="x"/></testcase>')
    c, j, r = files(tmp_path, junit=junit)
    tr.build(j, c, r, commit="abc1234")
    text = r.read_text(encoding="utf-8")
    assert re.search(r"\| TC-01 \|.*\| 不合格 \|", text) and "| 合格（Passed） | 0 |" in text


def test_a_case_without_any_name_or_browser_mark_is_refused(tmp_path):
    bad = CASES + "\n### TC-05 何も結んでいない\n- 要件：R-06\n"
    c, j, r = files(tmp_path, cases=bad.replace("## 5. 付録\n", ""))
    with pytest.raises(tr.ReportError):
        tr.build(j, c, r, commit="abc1234")
    twice = CASES.replace("TC-02", "TC-01")
    with pytest.raises(tr.ReportError):
        tr.parse_cases(twice)


def test_a_report_without_the_generated_block_is_not_written(tmp_path):
    c, j, r = files(tmp_path, report="# 報告\n")
    with pytest.raises(tr.ReportError):
        tr.build(j, c, r, commit="abc1234")


def test_the_script_compiles_without_a_warning_and_stops_with_exit_code_2_when_a_name_is_missing(tmp_path, capsys):
    import warnings
    src = (ROOT / "tools" / "test_report.py").read_text(encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("error")                              # an invalid escape sequence is a SyntaxWarning
        compile(src, "tools/test_report.py", "exec")
    c, j, r = files(tmp_path, cases=CASES.replace("test_two`", "test_three`"))
    tr.CASES, tr.REPORT = c, r
    try:
        assert tr.main(["build", "--junit", str(j)]) == 2
    finally:
        tr.CASES, tr.REPORT = ROOT / "docs" / "deliverables" / "test-cases.md", ROOT / "docs" / "deliverables" / "test-summary-report.md"
    assert "junit に無い試験の名前" in capsys.readouterr().err


# ---- the real cases file ---------------------------------------------------------------------------------------------------------

def defined_tests() -> set[str]:
    out = set()
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                out.add(f"tests/{f.name}::{node.name}")
    return out


def test_every_name_in_the_cases_file_is_a_test_that_exists_and_every_requirement_has_a_case():
    cases = tr.parse_cases(tr.CASES.read_text(encoding="utf-8"))
    assert len(cases) >= 13
    known = defined_tests()
    missing = sorted({n for c in cases for n in c.names if n not in known})
    assert missing == []
    text = tr.CASES.read_text(encoding="utf-8")
    listed = list(dict.fromkeys(re.findall(r"^\| (R-\d\d) \|", text, flags=re.M)))
    assert listed == [f"R-{i:02d}" for i in range(1, 14)]               # the 13 items of the acceptance
    used = {r for c in cases for r in c.reqs}
    assert used >= set(listed), sorted(set(listed) - used)
    assert all(c.reqs for c in cases), [c.id for c in cases if not c.reqs]


def test_the_question_log_has_six_questions_with_every_column_filled():
    text = (ROOT / "docs" / "deliverables" / "qa-log.md").read_text(encoding="utf-8")
    rows = [[c.strip() for c in ln.strip().strip("|").split(" | ")] for ln in text.splitlines() if re.match(r"^\| Q-\d\d \|", ln)]
    assert [r[0] for r in rows] == [f"Q-{i:02d}" for i in range(1, 7)]
    for r in rows:
        assert len(r) == 8, r[0]                                          # number, date, question, found, can it, instead, source, state
        number, received, question, found, can, instead, source, state = r
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", received) and question and found and instead and state, number
        assert can.startswith(("できる", "できない", "一部")), number
        assert "http" in source, number


def test_check_accepts_later_commits_that_touch_only_documents(tmp_path, monkeypatch, capsys):
    import subprocess
    repo, hooks = tmp_path / "repo", tmp_path / "nohooks"
    repo.mkdir()
    hooks.mkdir()

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", f"core.hooksPath={hooks}",
                               *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    (repo / "docs").mkdir()
    (repo / "code.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "code")
    tested = git("rev-parse", "HEAD")
    report = repo / "docs" / "report.md"
    report.write_text(f"| 試験した commit | `{tested}` |\n", encoding="utf-8")
    monkeypatch.setattr(tr, "ROOT", repo)
    monkeypatch.setattr(tr, "REPORT", report)
    assert tr.main(["check"]) == 0 and "は今の HEAD" in capsys.readouterr().out       # the report is not committed yet
    git("add", "-A")
    git("commit", "-qm", "report")
    (repo / "README.md").write_text("x\n")
    git("add", "-A")
    git("commit", "-qm", "readme")
    assert tr.main(["check"]) == 0 and "文書" in capsys.readouterr().out               # only documents came after
    (repo / "code.py").write_text("x = 2\n")
    git("add", "-A")
    git("commit", "-qm", "code again")
    assert tr.main(["check"]) == 1 and "code.py" in capsys.readouterr().err            # code changed after the report


def test_check_refuses_a_report_whose_case_rows_no_longer_match_the_cases_file(tmp_path):
    c, j, r = files(tmp_path)
    tr.build(j, c, r, commit="abc1234")
    cases, report = c.read_text(encoding="utf-8"), r.read_text(encoding="utf-8")
    assert tr.rows_differ(cases, report) == []                                          # as made: one row per case
    added = cases.replace("## 5. 付録", "### TC-05 後から足したケース\n- 要件：R-06\n- pytest：`tests/test_a.py::test_one`\n\n## 5. 付録")
    assert any("1 対 1" in p for p in tr.rows_differ(added, report))                    # a case with no row
    renamed = cases.replace("TC-01 合格するはずのケース", "TC-01 名前を変えたケース")
    assert any("TC-01" in p for p in tr.rows_differ(renamed, report))
    more = cases.replace("- pytest：`tests/test_b.py::test_skipped`",
                         "- pytest：`tests/test_b.py::test_skipped`、`tests/test_b.py::test_free`")
    assert any("TC-03" in p and "名前 1 個" in p for p in tr.rows_differ(more, report))   # a name added to a case

