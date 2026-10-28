"""Configuration versions (append-only) and environments (a pointer to one version)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import audit, config, roles
from .roles import User
from .timeutil import now_iso
from .workbook import WorkbookResult


NO_VERSION_TEXT = {
    config.ENV_SANDBOX: "環境 sandbox には設定の版がまだ無い（ワークブックを読み込む）",
    config.ENV_PRODUCTION: "本番の版が無い（変更の申請で上げる）",
}


class NoConfig(Exception):
    """The environment has no configuration version yet."""


class SchemaTooOld(Exception):
    """The configuration version in use was read from a workbook of an older schema (shown as a stop screen)."""


class VersionRejected(ValueError):
    """A valid workbook that must still not become a version (the message is shown to the person)."""


def reopened_periods(old_cfg: dict[str, Any], new_cfg: dict[str, Any]) -> list[str]:
    """Periods that are closed in `old_cfg` and open (or gone) in `new_cfg`: one administrator must not be able to
    reopen a closed period with a new workbook."""
    new = {p["name"]: p for p in new_cfg.get("periods", [])}
    out = []
    for p in old_cfg.get("periods", []):
        if p["closed"] and (p["name"] not in new or not new[p["name"]]["closed"]):
            out.append(p["name"])
    return out


def add_version(conn: sqlite3.Connection, user: User, result: WorkbookResult, source_bytes: bytes) -> int:
    """Store a validated workbook as a new version. No environment points at it yet; nothing is overwritten.
    Inside the caller's transaction (the caller commits)."""
    if not result.ok or result.config is None:
        raise ValueError("検査に通っていないワークブックは保存できない")
    cur = conn.execute(
        "INSERT INTO config_versions(schema_version, normalized_json, sha256, source_bytes,"
        " loaded_by, loaded_at) VALUES (?, ?, ?, ?, ?, ?)",
        (config.SCHEMA_VERSION, result.normalized_json, result.sha256, source_bytes, user.id, now_iso()))
    return int(cur.lastrowid)


_ANY = object()


def point_environment(conn: sqlite3.Connection, env: str, version_id: int, user_id: int, *,
                      expect: Any = _ANY, change_request_id: int | None = None) -> bool:
    """Move the pointer of `env` to `version_id` and append the record. With `expect` the move happens only when the
    pointer still holds that version id (None = no version yet): one `UPDATE ... WHERE version_id IS ?`, so a second
    request that was tested against the old version cannot silently overwrite the first. Returns whether it moved.
    Inside the caller's transaction (the caller commits)."""
    if expect is _ANY:
        cur = conn.execute("UPDATE environments SET version_id = ? WHERE name = ?", (version_id, env))
    else:
        cur = conn.execute("UPDATE environments SET version_id = ? WHERE name = ? AND version_id IS ?",
                           (version_id, env, expect))
    if cur.rowcount != 1:
        return False
    conn.execute("INSERT INTO environment_log(env, version_id, changed_by, changed_at, change_request_id)"
                 " VALUES (?, ?, ?, ?, ?)", (env, version_id, user_id, now_iso(), change_request_id))
    return True


def save_version(conn: sqlite3.Connection, user: User, result: WorkbookResult, source_bytes: bytes,
                 env: str = config.ENV_SANDBOX) -> int:
    """Store a validated workbook as a new version and point sandbox at it. Nothing is overwritten.
    Production is never pointed at from here: only an applied change request moves that pointer (`changes.apply`)."""
    roles.require(conn, user, "workbook_load", env=env)
    if not result.ok or result.config is None:
        raise ValueError("検査に通っていないワークブックは保存できない")
    if env != config.ENV_SANDBOX:
        raise ValueError("読み込み先は sandbox だけ。production には変更の申請で上げる")
    old_id = env_version_id(conn, env)
    if old_id is not None:
        old_cfg = json.loads(conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?",
                                          (old_id,)).fetchone()["normalized_json"])
        reopened = reopened_periods(old_cfg, result.config)
        if reopened:
            msg = "締めた期間を戻す版は読み込めない: " + "、".join(reopened)
            audit.log(conn, user_id=user.id, user_name=user.name, action="workbook_load",
                      outcome=audit.OUTCOME_DENIED, env=env, version_id=old_id, detail=msg)
            raise VersionRejected(msg)
    with conn:
        vid = add_version(conn, user, result, source_bytes)
        point_environment(conn, env, vid, user.id)
    audit.log(conn, user_id=user.id, user_name=user.name, action="workbook_load",
              outcome=audit.OUTCOME_OK, env=env, version_id=vid,
              detail=f"sha256={result.sha256}")
    return vid


def env_version_id(conn: sqlite3.Connection, env: str) -> int | None:
    row = conn.execute("SELECT version_id FROM environments WHERE name = ?", (env,)).fetchone()
    if row is None:
        raise ValueError(f"環境が無い: {env}")
    return row["version_id"]


def load_config(conn: sqlite3.Connection, env: str) -> tuple[int, dict[str, Any]]:
    vid = env_version_id(conn, env)
    if vid is None:
        raise NoConfig(NO_VERSION_TEXT.get(env, f"環境 {env} には設定の版がまだ無い"))
    row = conn.execute("SELECT normalized_json FROM config_versions WHERE id = ?", (vid,)).fetchone()
    return vid, json.loads(row["normalized_json"])


def require_current_schema(cfg: dict[str, Any]) -> None:
    """Screens and operations of the journal / task features need a workbook of schema 2."""
    if cfg.get("description", {}).get("schema_version") != config.SCHEMA_VERSION:
        raise SchemaTooOld("ワークブックを版 2 で読み直す（今の設定の版は schema の版が古い）")


def list_versions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT v.id, v.schema_version, v.sha256, v.loaded_at, u.name AS loaded_by,"
        " length(v.source_bytes) AS size FROM config_versions v JOIN users u ON u.id = v.loaded_by"
        " ORDER BY v.id DESC").fetchall()


def period_for(cfg: dict[str, Any], iso_date: str) -> str | None:
    for p in cfg.get("periods", []):
        if p["start"] <= iso_date <= p["end"]:
            return p["name"]
    return None
