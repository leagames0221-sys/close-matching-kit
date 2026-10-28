"""Users, password hashing (scrypt), server-side sessions, login throttling."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from . import audit, config
from .roles import User
from .timeutil import now_iso, parse_iso

# Module-level so tests can lower the cost; production value is config.SCRYPT_N.
SCRYPT_N = config.SCRYPT_N


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(config.SCRYPT_SALT_BYTES)
    n = SCRYPT_N
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=config.SCRYPT_R,
                        p=config.SCRYPT_P, maxmem=config.SCRYPT_MAXMEM)
    return f"scrypt${n}${config.SCRYPT_R}${config.SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n),
                            r=int(r), p=int(p), maxmem=config.SCRYPT_MAXMEM)
        return hmac.compare_digest(dk, bytes.fromhex(dk_hex))
    except (ValueError, TypeError):
        return False


def create_user(conn: sqlite3.Connection, name: str, password: str, roles: list[str], *,
                by_os_user: str | None = None) -> int:
    """Make a user. Users are made at the command line (`adduser`, `seed-demo`), not on a screen; the person is then the
    one who is logged in to the PC, so the command passes `getpass.getuser()` and the operation log says it: the server
    cannot know more than that (ADR 0008: whoever can open the database file can also change it)."""
    name = name.strip()
    if not name:
        raise ValueError("名前が空です")
    if name == config.SYSTEM_USER_NAME:
        raise ValueError(f"「{name}」は道具が仕訳の下書きを作る時の名前で、利用者にはできない")
    bad = [r for r in roles if r not in config.ROLES]
    if bad or not roles:
        raise ValueError(f"役割は {','.join(config.ROLES)} から選ぶ: {bad or '(未指定)'}")
    if len(password) < 8:
        raise ValueError("パスワードは 8 文字以上")
    cur = conn.execute(
        "INSERT INTO users(name, pw_hash, roles, created_at) VALUES (?, ?, ?, ?)",
        (name, hash_password(password), ",".join(sorted(set(roles))), now_iso()),
    )
    conn.commit()
    if by_os_user is not None:
        audit.log(conn, user_id=None, user_name=f"PC の利用者 {by_os_user}", action="user_create",
                  outcome=audit.OUTCOME_OK,
                  detail=f"利用者 {name} を作った（役割 {','.join(sorted(set(roles)))}）。作ったのはこの PC の OS の利用者 {by_os_user}")
    return int(cur.lastrowid)


def ensure_system_user(conn: sqlite3.Connection) -> User:
    """The user that stands as the maker of a journal the tool drafts by itself (`config.SYSTEM_USER_NAME`). It has no
    role and a password hash that matches nothing, so nobody can log in as it; the screens show its name as the maker
    and every approver is a different person from it."""
    row = conn.execute("SELECT * FROM users WHERE name = ?", (config.SYSTEM_USER_NAME,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO users(name, pw_hash, roles, created_at) VALUES (?, '!', '', ?)",
                     (config.SYSTEM_USER_NAME, now_iso()))
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE name = ?", (config.SYSTEM_USER_NAME,)).fetchone()
    return _user_from_row(row)


def _user_from_row(row: sqlite3.Row) -> User:
    return User(id=row["id"], name=row["name"],
                roles=frozenset(x for x in row["roles"].split(",") if x))


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _user_from_row(row) if row else None


def get_user_by_name(conn: sqlite3.Connection, name: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    return _user_from_row(row) if row else None


# ---- login throttle ---------------------------------------------------------------------

def recent_failures(conn: sqlite3.Connection, name: str) -> int:
    since = (datetime.now(timezone.utc)
             - timedelta(seconds=config.LOGIN_FAIL_WINDOW_SECONDS)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    row = conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE name = ? AND ok = 0 AND at >= ?", (name, since)
    ).fetchone()
    return int(row[0])


def authenticate(conn: sqlite3.Connection, name: str, password: str) -> User | None:
    """Return the user or None. Failures (and throttled attempts) go to the operation log."""
    name = name[:200]
    if recent_failures(conn, name) >= config.LOGIN_FAIL_LIMIT:
        audit.log(conn, user_id=None, user_name=name, action="login", outcome=audit.OUTCOME_DENIED,
                  detail=f"ログインの失敗が {config.LOGIN_FAIL_WINDOW_SECONDS // 60} 分に {config.LOGIN_FAIL_LIMIT} 回に達したため受け付けない")
        return None
    row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    ok = False
    if row is not None:
        ok = verify_password(password, row["pw_hash"])
    else:
        # the same work as for a real user (same N, r, p), so the answer time does not tell which names exist
        verify_password(password, f"scrypt${SCRYPT_N}${config.SCRYPT_R}${config.SCRYPT_P}$" + "00" * 16 + "$" + "00" * 64)
    conn.execute("INSERT INTO login_attempts(name, at, ok) VALUES (?, ?, ?)",
                 (name, now_iso(), 1 if ok else 0))
    conn.commit()
    if not ok:
        audit.log(conn, user_id=row["id"] if row else None, user_name=name, action="login",
                  outcome=audit.OUTCOME_DENIED, detail="名前かパスワードが違う")
        return None
    user = _user_from_row(row)
    audit.log(conn, user_id=user.id, user_name=user.name, action="login", outcome=audit.OUTCOME_OK)
    return user


# ---- sessions ---------------------------------------------------------------------------

def _hash_sid(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def new_session(conn: sqlite3.Connection, user_id: int | None) -> tuple[str, str]:
    """Create a session; returns (cookie id, csrf token). user_id None = pre-login (for the login form)."""
    sid = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(seconds=config.SESSION_SECONDS)
           ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    conn.execute("INSERT INTO sessions(id_hash, user_id, csrf, created_at, expires_at)"
                 " VALUES (?, ?, ?, ?, ?)", (_hash_sid(sid), user_id, csrf, now_iso(), exp))
    conn.commit()
    return sid, csrf


def lookup_session(conn: sqlite3.Connection, sid: str | None) -> tuple[User | None, str] | None:
    """Return (user or None for pre-login, csrf) for a live session, else None."""
    if not sid:
        return None
    row = conn.execute("SELECT * FROM sessions WHERE id_hash = ?", (_hash_sid(sid),)).fetchone()
    if row is None:
        return None
    if parse_iso(row["expires_at"]) <= datetime.now(timezone.utc):
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (row["id_hash"],))
        conn.commit()
        return None
    user = get_user(conn, row["user_id"]) if row["user_id"] is not None else None
    return user, row["csrf"]


def delete_session(conn: sqlite3.Connection, sid: str | None) -> None:
    if sid:
        conn.execute("DELETE FROM sessions WHERE id_hash = ?", (_hash_sid(sid),))
        conn.commit()
