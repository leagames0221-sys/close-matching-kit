"""Command line:  python -m close_kit serve | adduser NAME --roles A,B | seed-demo

Run from the repository root with the project's virtual environment.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import sys

from . import auth, config, db

DEMO_USERS = [
    ("demo-admin", ["管理者"]),
    ("demo-admin2", ["管理者"]),
    ("demo-maker", ["作成者"]),
    ("demo-approver", ["承認者"]),
    ("demo-approver2", ["承認者"]),
    ("demo-auditor", ["監査人"]),
]


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .web import create_app
    uvicorn.run(create_app(), host=config.HOST, port=config.PORT, log_level="info")
    return 0


def cmd_adduser(args: argparse.Namespace) -> int:
    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    pw = getpass.getpass("パスワード（8 文字以上）: ")
    if pw != getpass.getpass("もう一度: "):
        print("パスワードが一致しない", file=sys.stderr)
        return 1
    conn = db.connect()
    try:
        auth.create_user(conn, args.name, pw, roles, by_os_user=getpass.getuser())
    except Exception as e:  # noqa: BLE001 - shown to the person at the terminal
        print(f"作れない: {e}", file=sys.stderr)
        return 1
    print(f"利用者 {args.name} を作った（役割 {','.join(roles)}）")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    """Create the demo users that are missing, with random passwords; the passwords go to var/demo_users.txt only."""
    conn = db.connect()
    lines = []
    for name, roles in DEMO_USERS:
        if auth.get_user_by_name(conn, name) is not None:
            print(f"{name}: すでにある（パスワードは変えない）")
            continue
        pw = secrets.token_urlsafe(12)
        auth.create_user(conn, name, pw, roles, by_os_user=getpass.getuser())
        lines.append(f"{name}\t{','.join(roles)}\t{pw}")
        print(f"{name}: 作った（役割 {','.join(roles)}）")
    if lines:
        config.VAR_DIR.mkdir(parents=True, exist_ok=True)
        mode = "a" if config.DEMO_USERS_FILE.exists() else "w"
        with config.DEMO_USERS_FILE.open(mode, encoding="utf-8") as f:
            if mode == "w":
                f.write("名前\t役割\tパスワード\n")
            f.write("\n".join(lines) + "\n")
        print(f"パスワードは {config.DEMO_USERS_FILE} に書いた（git には入らない）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m close_kit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help=f"{config.HOST}:{config.PORT} で起動する").set_defaults(fn=cmd_serve)
    a = sub.add_parser("adduser", help="利用者を作る（パスワードは入力する）")
    a.add_argument("name")
    a.add_argument("--roles", required=True, help="例: 作成者,承認者")
    a.set_defaults(fn=cmd_adduser)
    sub.add_parser("seed-demo", help="見本の利用者 6 人を作る（足りない人だけ）").set_defaults(fn=cmd_seed_demo)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
