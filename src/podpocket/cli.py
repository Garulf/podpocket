import argparse
import asyncio
import getpass
import sys

import uvicorn

from podpocket.accounts import users
from podpocket.config import DatabaseSettings
from podpocket.db import create_engine, create_sessionmaker
from podpocket.migrate import upgrade_to_head
from podpocket.timefmt import iso_z


class Aborted(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        uvicorn.run(
            "podpocket.app:create_app_from_env",
            factory=True,
            host=args.host,
            port=args.port,
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
        return 0
    return run_user_command(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="podpocket")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    user = commands.add_parser("user", help="manage accounts")
    actions = user.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--email")
    add.add_argument("--admin", action="store_true")
    add.add_argument("--password-stdin", action="store_true")
    passwd = actions.add_parser("passwd", help="set a new password and sign out every device")
    passwd.add_argument("username")
    passwd.add_argument("--password-stdin", action="store_true")
    remove = actions.add_parser("remove", help="delete an account and all of its data")
    remove.add_argument("username")
    remove.add_argument("--yes", action="store_true")
    actions.add_parser("list", help="list accounts")
    return parser


def run_user_command(args: argparse.Namespace) -> int:
    try:
        password = read_password(args) if args.action in ("add", "passwd") else None
        database_url = DatabaseSettings().database_url
        upgrade_to_head(database_url)
        asyncio.run(user_command(args, database_url, password))
    except users.UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (Aborted, EOFError, KeyboardInterrupt):
        print("aborted", file=sys.stderr)
        return 1
    return 0


def read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise users.UserError("passwords do not match")
    return password


def confirm_removal(username: str) -> bool:
    answer = input(f"Remove {username} and all of their data? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def user_command(args: argparse.Namespace, database_url: str, password: str | None) -> None:
    engine = create_engine(database_url)
    sessionmaker = create_sessionmaker(engine)
    try:
        match args.action:
            case "add":
                user = await users.create_user(
                    sessionmaker,
                    args.username,
                    password or "",
                    email=args.email,
                    is_admin=args.admin,
                )
                print(f"created {user.username} ({user.uuid})")
            case "passwd":
                user = await users.set_password(sessionmaker, args.username, password or "")
                print(f"changed the password of {user.username} and signed out every device")
            case "remove":
                target = await users.get_user(sessionmaker, args.username)
                if not args.yes and not confirm_removal(target.username):
                    raise Aborted
                user = await users.remove_user(sessionmaker, args.username)
                print(f"removed {user.username}")
            case "list":
                for user in await users.list_users(sessionmaker):
                    role = "admin" if user.is_admin else "user"
                    print(f"{user.username}\t{user.email or '-'}\t{role}\t{iso_z(user.created_at)}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
