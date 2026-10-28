"""SQLite schema and connection. All SQL uses placeholders only.

`SCHEMA` is migration 1 (the first release, frozen). Later changes are numbered migrations (see MIGRATIONS):
SQLite cannot change a CHECK constraint, so migration 2 builds new tables, copies the rows, removes the old ones
and renames the new ones; migration 3 only adds (the change requests). A migration is recorded in `schema_migrations` and is never run twice.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config
from .timeutil import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    roles TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id_hash TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    csrf TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    at TEXT NOT NULL,
    ok INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_login_attempts_name ON login_attempts(name, at);

CREATE TABLE IF NOT EXISTS config_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version TEXT NOT NULL,
    normalized_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_bytes BLOB NOT NULL,
    loaded_by INTEGER NOT NULL REFERENCES users(id),
    loaded_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS config_versions_no_update BEFORE UPDATE ON config_versions
BEGIN SELECT RAISE(ABORT, 'config_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS config_versions_no_delete BEFORE DELETE ON config_versions
BEGIN SELECT RAISE(ABORT, 'config_versions is append-only'); END;

CREATE TABLE IF NOT EXISTS environments (
    name TEXT PRIMARY KEY,
    version_id INTEGER REFERENCES config_versions(id)
);
INSERT OR IGNORE INTO environments(name, version_id) VALUES ('sandbox', NULL);
INSERT OR IGNORE INTO environments(name, version_id) VALUES ('production', NULL);

CREATE TABLE IF NOT EXISTS environment_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL REFERENCES environments(name),
    version_id INTEGER NOT NULL REFERENCES config_versions(id),
    changed_by INTEGER NOT NULL REFERENCES users(id),
    changed_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS environment_log_no_update BEFORE UPDATE ON environment_log
BEGIN SELECT RAISE(ABORT, 'environment_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS environment_log_no_delete BEFORE DELETE ON environment_log
BEGIN SELECT RAISE(ABORT, 'environment_log is append-only'); END;

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    env TEXT,
    version_id INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('成功', '拒否')),
    detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('ingest', 'match')),
    env TEXT NOT NULL REFERENCES environments(name),
    version_id INTEGER NOT NULL REFERENCES config_versions(id),
    period TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    env TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    period TEXT,
    source TEXT NOT NULL,
    account TEXT NOT NULL,
    fmt TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    date_from TEXT,
    date_to TEXT,
    row_count INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_batches_source ON batches(env, source, account);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    run_id INTEGER NOT NULL,
    env TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    period TEXT,
    source TEXT NOT NULL,
    account TEXT NOT NULL,
    booking_date TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    memo TEXT NOT NULL DEFAULT '',
    src_row INTEGER NOT NULL,
    row_key TEXT,
    ext_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_txn_source ON transactions(env, source);
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_rowkey ON transactions(env, source, row_key)
    WHERE row_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS match_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    env TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    period TEXT,
    match_set TEXT NOT NULL,
    rule_order INTEGER,
    rule_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('自動', '候補', '手')),
    state TEXT NOT NULL CHECK (state IN ('完了', '差額の処理待ち', '確認待ち', '承認待ち')),
    reason TEXT NOT NULL DEFAULT '',
    left_total INTEGER NOT NULL DEFAULT 0,
    right_total INTEGER NOT NULL DEFAULT 0,
    diff_minor INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_groups_set ON match_groups(env, match_set, state);

CREATE TABLE IF NOT EXISTS match_group_rows (
    group_id INTEGER NOT NULL REFERENCES match_groups(id) ON DELETE CASCADE,
    txn_id INTEGER NOT NULL REFERENCES transactions(id),
    side TEXT NOT NULL CHECK (side IN ('L', 'R')),
    PRIMARY KEY (group_id, txn_id)
);
CREATE INDEX IF NOT EXISTS ix_group_rows_txn ON match_group_rows(txn_id);

CREATE TABLE IF NOT EXISTS match_rows (
    env TEXT NOT NULL,
    match_set TEXT NOT NULL,
    txn_id INTEGER NOT NULL REFERENCES transactions(id),
    side TEXT NOT NULL CHECK (side IN ('L', 'R')),
    klass TEXT NOT NULL CHECK (klass IN ('自動', '候補', '未照合', '重複', '手')),
    group_id INTEGER REFERENCES match_groups(id) ON DELETE SET NULL,
    run_id INTEGER NOT NULL,
    PRIMARY KEY (env, match_set, txn_id)
);
"""


MATCH_GROUPS_V2 = """
CREATE TABLE match_groups_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    env TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    period TEXT,
    match_set TEXT NOT NULL,
    rule_order INTEGER,
    rule_name TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('自動', '候補', '手')),
    state TEXT NOT NULL CHECK (state IN ('完了', '差額の処理待ち', '確認待ち', '承認待ち', '仕訳の承認待ち')),
    reason TEXT NOT NULL DEFAULT '',
    left_total INTEGER NOT NULL DEFAULT 0,
    right_total INTEGER NOT NULL DEFAULT 0,
    diff_minor INTEGER NOT NULL DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT
)"""

MATCH_ROWS_V2 = """
CREATE TABLE match_rows_new (
    env TEXT NOT NULL,
    match_set TEXT NOT NULL,
    txn_id INTEGER NOT NULL REFERENCES transactions(id),
    side TEXT NOT NULL CHECK (side IN ('L', 'R')),
    klass TEXT NOT NULL CHECK (klass IN ('自動', '候補', '未照合', '重複', '手', '処理済み', '転記済み')),
    group_id INTEGER REFERENCES match_groups(id) ON DELETE SET NULL,
    run_id INTEGER NOT NULL,
    PRIMARY KEY (env, match_set, txn_id)
)"""

NEW_TABLES_V2 = """
CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    number TEXT NOT NULL UNIQUE,
    entry_date TEXT NOT NULL,
    period TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL CHECK (method IN ('理由コード', '手入力', '取り込み')),
    status TEXT NOT NULL CHECK (status IN ('下書き', '承認待ち', '承認済み', '却下', '取り下げ')),
    debit_total INTEGER NOT NULL CHECK (debit_total > 0),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    reason_code TEXT NOT NULL DEFAULT '',
    link_group_id INTEGER REFERENCES match_groups(id),
    link_txn_id INTEGER REFERENCES transactions(id),
    match_set TEXT NOT NULL DEFAULT '',
    slip_no TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_version_id INTEGER,
    decision_route TEXT NOT NULL DEFAULT '',
    decision_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_journals_env ON journals(env, status);
CREATE INDEX IF NOT EXISTS ix_journals_group ON journals(link_group_id);
CREATE INDEX IF NOT EXISTS ix_journals_txn ON journals(link_txn_id);
CREATE TABLE IF NOT EXISTS journal_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    line_no INTEGER NOT NULL,
    account_code TEXT NOT NULL,
    debit INTEGER NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit INTEGER NOT NULL DEFAULT 0 CHECK (credit >= 0),
    memo TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_journal_lines ON journal_lines(journal_id);
CREATE TRIGGER IF NOT EXISTS journals_approved_no_update BEFORE UPDATE ON journals
WHEN OLD.status = '承認済み' BEGIN SELECT RAISE(ABORT, 'an approved journal cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS journals_approved_no_delete BEFORE DELETE ON journals
WHEN OLD.status = '承認済み' BEGIN SELECT RAISE(ABORT, 'an approved journal cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS journal_lines_approved_no_update BEFORE UPDATE ON journal_lines
WHEN (SELECT status FROM journals WHERE id = OLD.journal_id) = '承認済み'
BEGIN SELECT RAISE(ABORT, 'the lines of an approved journal cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS journal_lines_approved_no_delete BEFORE DELETE ON journal_lines
WHEN (SELECT status FROM journals WHERE id = OLD.journal_id) = '承認済み'
BEGIN SELECT RAISE(ABORT, 'the lines of an approved journal cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS journal_lines_approved_no_insert BEFORE INSERT ON journal_lines
WHEN (SELECT status FROM journals WHERE id = NEW.journal_id) = '承認済み'
BEGIN SELECT RAISE(ABORT, 'lines cannot be added to an approved journal'); END;

CREATE TABLE IF NOT EXISTS adjusting_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    match_set TEXT NOT NULL,
    txn_id INTEGER NOT NULL REFERENCES transactions(id),
    reason_code TEXT NOT NULL,
    amount_minor INTEGER NOT NULL,
    period TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('承認待ち', '未解消', '解消', '却下')),
    version_id INTEGER NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_note TEXT NOT NULL DEFAULT '',
    resolved_group_id INTEGER REFERENCES match_groups(id),
    resolved_run_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_adjusting_active ON adjusting_items(txn_id)
    WHERE status IN ('承認待ち', '未解消', '解消');

CREATE TABLE IF NOT EXISTS posting_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    name TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content BLOB NOT NULL,
    journal_count INTEGER NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posting_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    action TEXT NOT NULL CHECK (action IN ('出力', '外す')),
    file_id INTEGER REFERENCES posting_files(id),
    sha256 TEXT NOT NULL DEFAULT '',
    by_user INTEGER NOT NULL REFERENCES users(id),
    at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_posting_log_journal ON posting_log(journal_id, id);
CREATE TABLE IF NOT EXISTS posted_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    txn_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    linked_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS posting_files_no_update BEFORE UPDATE ON posting_files
BEGIN SELECT RAISE(ABORT, 'posting_files is append-only'); END;
CREATE TRIGGER IF NOT EXISTS posting_files_no_delete BEFORE DELETE ON posting_files
BEGIN SELECT RAISE(ABORT, 'posting_files is append-only'); END;
CREATE TRIGGER IF NOT EXISTS posting_log_no_update BEFORE UPDATE ON posting_log
BEGIN SELECT RAISE(ABORT, 'posting_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS posting_log_no_delete BEFORE DELETE ON posting_log
BEGIN SELECT RAISE(ABORT, 'posting_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS posted_links_no_update BEFORE UPDATE ON posted_links
BEGIN SELECT RAISE(ABORT, 'posted_links is append-only'); END;
CREATE TRIGGER IF NOT EXISTS posted_links_no_delete BEFORE DELETE ON posted_links
BEGIN SELECT RAISE(ABORT, 'posted_links is append-only'); END;

CREATE TABLE IF NOT EXISTS task_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    period TEXT NOT NULL,
    task_id TEXT NOT NULL,
    name TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    due_date TEXT,
    due_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('未着手', '作成完了', '完了')),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (env, period, task_id)
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    instance_id INTEGER NOT NULL REFERENCES task_instances(id),
    at TEXT NOT NULL,
    user_id INTEGER,
    user_name TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_task_events ON task_events(instance_id, id);
CREATE TRIGGER IF NOT EXISTS task_events_no_update BEFORE UPDATE ON task_events
BEGIN SELECT RAISE(ABORT, 'task_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS task_events_no_delete BEFORE DELETE ON task_events
BEGIN SELECT RAISE(ABORT, 'task_events is append-only'); END;
"""


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _rebuild(conn: sqlite3.Connection, table: str, create_sql: str, indexes: list[str]) -> None:
    """Make `table` again from `create_sql` (written for the name <table>_new), keeping every row and id."""
    cols = ", ".join(_columns(conn, table))
    conn.execute(f"DROP TABLE IF EXISTS {table}_new")
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO {table}_new({cols}) SELECT {cols} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
    for ix in indexes:
        conn.execute(ix)


def _migration_2(conn: sqlite3.Connection) -> None:
    """Journals, adjusting items, posting records, tasks; wider CHECK lists on match_groups / match_rows;
    balances on batches and transactions."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # the documented way to rebuild a table (sqlite.org/lang_altertable)
    try:
        conn.execute("BEGIN")
        _rebuild(conn, "match_groups", MATCH_GROUPS_V2, [
            "CREATE INDEX IF NOT EXISTS ix_groups_set ON match_groups(env, match_set, state)"])
        _rebuild(conn, "match_rows", MATCH_ROWS_V2, [])
        if "opening_minor" not in _columns(conn, "batches"):
            conn.execute("ALTER TABLE batches ADD COLUMN opening_minor INTEGER")
            conn.execute("ALTER TABLE batches ADD COLUMN closing_minor INTEGER")
        if "balance_minor" not in _columns(conn, "transactions"):
            conn.execute("ALTER TABLE transactions ADD COLUMN balance_minor INTEGER")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise sqlite3.IntegrityError(f"foreign key check failed after the rebuild: {len(bad)} rows")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(NEW_TABLES_V2)


NEW_TABLES_V3 = """
CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('通常', '戻し')),
    status TEXT NOT NULL CHECK (status IN ('下書き', '申請済み')),
    title TEXT NOT NULL,
    requester_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    from_version_id INTEGER REFERENCES config_versions(id),
    to_version_id INTEGER NOT NULL REFERENCES config_versions(id),
    justification TEXT NOT NULL,
    urgency TEXT NOT NULL DEFAULT 'Scheduled' CHECK (urgency = 'Scheduled'),
    functional_impact TEXT NOT NULL DEFAULT '',
    impact_if_not TEXT NOT NULL DEFAULT '',
    rollback_version_id INTEGER REFERENCES config_versions(id),
    rollback_of INTEGER REFERENCES change_requests(id),
    submitted_at TEXT,
    diff_json TEXT NOT NULL DEFAULT '',
    impact_json TEXT NOT NULL DEFAULT '',
    security_json TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_change_requests_to ON change_requests(to_version_id);
CREATE TABLE IF NOT EXISTS change_request_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES change_requests(id),
    at TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    user_name TEXT NOT NULL,
    roles TEXT NOT NULL,
    env TEXT NOT NULL,
    version_id INTEGER,
    action TEXT NOT NULL,
    to_state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_change_events ON change_request_events(request_id, id);
CREATE TABLE IF NOT EXISTS change_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES change_requests(id),
    run_at TEXT NOT NULL,
    run_by INTEGER NOT NULL REFERENCES users(id),
    base_version_id INTEGER REFERENCES config_versions(id),
    to_version_id INTEGER NOT NULL REFERENCES config_versions(id),
    base_sha256 TEXT NOT NULL DEFAULT '',
    to_sha256 TEXT NOT NULL,
    prod_copy_at TEXT NOT NULL,
    samples_json TEXT NOT NULL,
    result_status TEXT NOT NULL CHECK (result_status IN ('成功', '失敗（変更の影響）')),
    result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_change_tests ON change_tests(request_id, id);
CREATE TABLE IF NOT EXISTS change_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES change_requests(id),
    test_id INTEGER NOT NULL REFERENCES change_tests(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    user_name TEXT NOT NULL,
    roles TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    UNIQUE (test_id, user_id)
);
CREATE TRIGGER IF NOT EXISTS change_requests_frozen_no_update BEFORE UPDATE ON change_requests
WHEN OLD.status <> '下書き' BEGIN SELECT RAISE(ABORT, 'a change request that left the draft cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS change_requests_frozen_no_delete BEFORE DELETE ON change_requests
WHEN OLD.status <> '下書き' BEGIN SELECT RAISE(ABORT, 'a change request that left the draft cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS change_request_events_no_update BEFORE UPDATE ON change_request_events
BEGIN SELECT RAISE(ABORT, 'change_request_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_request_events_no_delete BEFORE DELETE ON change_request_events
BEGIN SELECT RAISE(ABORT, 'change_request_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_tests_no_update BEFORE UPDATE ON change_tests
BEGIN SELECT RAISE(ABORT, 'change_tests is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_tests_no_delete BEFORE DELETE ON change_tests
BEGIN SELECT RAISE(ABORT, 'change_tests is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_approvals_no_update BEFORE UPDATE ON change_approvals
BEGIN SELECT RAISE(ABORT, 'change_approvals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_approvals_no_delete BEFORE DELETE ON change_approvals
BEGIN SELECT RAISE(ABORT, 'change_approvals is append-only'); END;
"""


def _migration_3(conn: sqlite3.Connection) -> None:
    """Change requests: the request (frozen once it leaves the draft), its history, the test results and the approvals
    of a result (all append-only); the environment log learns which request moved a pointer."""
    conn.commit()
    if "change_request_id" not in _columns(conn, "environment_log"):
        conn.execute("ALTER TABLE environment_log ADD COLUMN change_request_id INTEGER")
    conn.executescript(NEW_TABLES_V3)


JOURNALS_V4 = """
CREATE TABLE journals_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    number TEXT NOT NULL UNIQUE,
    entry_date TEXT NOT NULL,
    period TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL CHECK (method IN ('理由コード', '手入力', '取り込み', '照合の差額')),
    status TEXT NOT NULL CHECK (status IN ('下書き', '承認待ち', '承認済み', '却下', '取り下げ')),
    debit_total INTEGER NOT NULL CHECK (debit_total > 0),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    version_id INTEGER NOT NULL,
    reason_code TEXT NOT NULL DEFAULT '',
    link_group_id INTEGER REFERENCES match_groups(id),
    link_txn_id INTEGER REFERENCES transactions(id),
    match_set TEXT NOT NULL DEFAULT '',
    slip_no TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT,
    decision_version_id INTEGER,
    decision_route TEXT NOT NULL DEFAULT '',
    decision_note TEXT NOT NULL DEFAULT ''
)"""

JOURNALS_V4_AFTER = [
    "CREATE INDEX IF NOT EXISTS ix_journals_env ON journals(env, status)",
    "CREATE INDEX IF NOT EXISTS ix_journals_group ON journals(link_group_id)",
    "CREATE INDEX IF NOT EXISTS ix_journals_txn ON journals(link_txn_id)",
    "CREATE TRIGGER IF NOT EXISTS journals_approved_no_update BEFORE UPDATE ON journals"
    " WHEN OLD.status = '承認済み' BEGIN SELECT RAISE(ABORT, 'an approved journal cannot be changed'); END",
    "CREATE TRIGGER IF NOT EXISTS journals_approved_no_delete BEFORE DELETE ON journals"
    " WHEN OLD.status = '承認済み' BEGIN SELECT RAISE(ABORT, 'an approved journal cannot be deleted'); END",
]


def _migration_4(conn: sqlite3.Connection) -> None:
    """The way a journal was made gets a fourth value, '照合の差額' (drafted by the tool for the difference of a pair
    inside the tolerance). A CHECK list cannot be widened in place, so the table is rebuilt the way
    sqlite.org/lang_altertable describes (as migration 2 did); the triggers and indexes of `journals` are made again."""
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")   # the triggers of journal_lines name `journals`, absent for a moment
    try:
        conn.execute("BEGIN")
        _rebuild(conn, "journals", JOURNALS_V4, JOURNALS_V4_AFTER)
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise sqlite3.IntegrityError(f"foreign key check failed after the rebuild: {len(bad)} rows")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


MIGRATIONS = [(2, _migration_2), (3, _migration_3), (4, _migration_4)]


def migrate(conn: sqlite3.Connection) -> None:
    """Run the numbered migrations that this database has not seen. Running it again changes nothing."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)", (now_iso(),))
    conn.commit()
    done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    for version, fn in MIGRATIONS:
        if version in done:
            continue
        fn(conn)
        conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, now_iso()))
        conn.commit()


def connect(path: Path | str | None = None, init: bool = True) -> sqlite3.Connection:
    p = Path(path) if path is not None else config.DEFAULT_DB
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if init:
        conn.executescript(SCHEMA)
        conn.commit()
        migrate(conn)
    return conn
