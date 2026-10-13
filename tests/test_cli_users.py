import asyncio
import io

import pytest

from podpocket import cli
from podpocket.accounts.users import authenticate
from podpocket.db import create_engine, create_sessionmaker


@pytest.fixture
def database(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setenv("PODPOCKET_DATABASE_URL", url)
    return url


def run(argv, monkeypatch, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return cli.main(argv)


def login_works(url, login, password):
    async def check():
        engine = create_engine(url)
        try:
            return await authenticate(create_sessionmaker(engine), login, password) is not None
        finally:
            await engine.dispose()

    return asyncio.run(check())


def test_user_add_then_list(database, monkeypatch, capsys):
    add_alice = [
        "user",
        "add",
        "Alice",
        "--email",
        "Alice@Example.com",
        "--admin",
        "--password-stdin",
    ]
    assert run(add_alice, monkeypatch, "hunter2 pass\n") == 0
    assert run(["user", "add", "bob", "--password-stdin"], monkeypatch, "bob pass\n") == 0
    assert run(["user", "list"], monkeypatch) == 0

    out = capsys.readouterr().out
    assert "created alice" in out
    rows = [line.split("\t")[:3] for line in out.splitlines() if "\t" in line]
    assert rows == [["alice", "alice@example.com", "admin"], ["bob", "-", "user"]]
    assert login_works(database, "alice@example.com", "hunter2 pass")


def test_user_add_prompts_twice_without_stdin_flag(database, monkeypatch):
    answers = iter(["typed pass", "typed pass"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))

    assert run(["user", "add", "carol"], monkeypatch) == 0

    assert login_works(database, "carol", "typed pass")


def test_mismatched_passwords_fail(database, monkeypatch, capsys):
    answers = iter(["one", "two"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))

    assert run(["user", "add", "carol"], monkeypatch) == 1

    assert "do not match" in capsys.readouterr().err


def test_duplicate_user_fails(database, monkeypatch, capsys):
    run(["user", "add", "alice", "--password-stdin"], monkeypatch, "pw one\n")

    assert run(["user", "add", "ALICE", "--password-stdin"], monkeypatch, "pw two\n") == 1

    assert "already used" in capsys.readouterr().err


def test_passwd_changes_the_password(database, monkeypatch):
    run(["user", "add", "alice", "--password-stdin"], monkeypatch, "old pass\n")

    assert run(["user", "passwd", "alice", "--password-stdin"], monkeypatch, "new pass\n") == 0

    assert not login_works(database, "alice", "old pass")
    assert login_works(database, "alice", "new pass")


def test_passwd_for_unknown_user_fails(database, monkeypatch, capsys):
    assert run(["user", "passwd", "ghost", "--password-stdin"], monkeypatch, "pw\n") == 1

    assert "no user named 'ghost'" in capsys.readouterr().err


def test_remove_asks_for_confirmation(database, monkeypatch):
    run(["user", "add", "alice", "--password-stdin"], monkeypatch, "pw\n")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert run(["user", "remove", "alice"], monkeypatch) == 1
    assert login_works(database, "alice", "pw")

    assert run(["user", "remove", "alice", "--yes"], monkeypatch) == 0
    assert not login_works(database, "alice", "pw")
