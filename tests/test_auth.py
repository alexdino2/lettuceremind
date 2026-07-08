import json

import pytest

from lettuceremind import auth
from lettuceremind.cli import main
from lettuceremind.store import PantryStore, default_store_path

PW = "correct-horse-battery"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LETTUCEREMIND_HOME", str(tmp_path))
    monkeypatch.delenv("LETTUCEREMIND_STORE", raising=False)
    monkeypatch.delenv("LETTUCEREMIND_DEALS", raising=False)
    return tmp_path


def test_register_logs_in_and_never_stores_the_password(isolated_home, capsys):
    rc = main(["register", "Alice", "--password", PW])
    assert rc == 0
    assert "alice" in capsys.readouterr().out
    assert auth.current_user() == "alice"
    raw = (isolated_home / "users.json").read_text()
    assert PW not in raw
    record = json.loads(raw)["users"]["alice"]
    assert record["salt"] and record["hash"]


def test_register_rejects_short_password(capsys):
    assert main(["register", "alice", "--password", "short"]) == 1
    assert "at least" in capsys.readouterr().err
    assert auth.current_user() is None


def test_register_rejects_bad_username(capsys):
    assert main(["register", "a b!", "--password", PW]) == 1
    assert "invalid username" in capsys.readouterr().err


def test_register_rejects_duplicate_username(capsys):
    assert main(["register", "alice", "--password", PW]) == 0
    assert main(["register", "ALICE", "--password", "another-pass1"]) == 1
    assert "already taken" in capsys.readouterr().err


def test_login_wrong_password_fails(capsys):
    main(["register", "alice", "--password", PW])
    main(["logout"])
    assert main(["login", "alice", "--password", "wrong-password"]) == 1
    assert "invalid username or password" in capsys.readouterr().err
    assert auth.current_user() is None


def test_login_unknown_user_fails_with_same_message(capsys):
    assert main(["login", "ghost", "--password", PW]) == 1
    assert "invalid username or password" in capsys.readouterr().err


def test_each_user_gets_their_own_pantry_across_all_commands(capsys):
    # alice registers and scans/adds into her own pantry
    main(["register", "alice", "--password", PW])
    assert main(["add", "milk"]) == 0

    # logging out switches every command back to the (empty) shared pantry
    main(["logout"])
    main(["list"])
    assert "empty" in capsys.readouterr().out

    # bob's pantry starts empty and stays separate
    main(["register", "bob", "--password", PW])
    main(["list"])
    assert "empty" in capsys.readouterr().out
    main(["add", "eggs"])

    # alice logs back in and sees exactly her items
    main(["login", "alice", "--password", PW])
    capsys.readouterr()
    main(["list"])
    out = capsys.readouterr().out
    assert "milk" in out
    assert "eggs" not in out


def test_default_store_path_follows_the_session(isolated_home):
    assert default_store_path() == isolated_home / "pantry.json"
    auth.register("carol", PW)
    assert default_store_path() == isolated_home / "users" / "carol" / "pantry.json"
    auth.logout()
    assert default_store_path() == isolated_home / "pantry.json"


def test_explicit_store_flag_still_wins_when_logged_in(tmp_path, capsys):
    main(["register", "alice", "--password", PW])
    override = tmp_path / "elsewhere.json"
    assert main(["--store", str(override), "add", "milk"]) == 0
    assert override.exists()
    items = PantryStore(auth.user_pantry_path("alice")).all()
    assert items == []


def test_logout_revokes_the_session_token(isolated_home):
    auth.register("alice", PW)
    session = (isolated_home / "session.json").read_text()
    auth.logout()
    # restoring a stale session file must not authenticate
    (isolated_home / "session.json").write_text(session)
    assert auth.current_user() is None


def test_whoami(capsys):
    main(["whoami"])
    assert "Not logged in" in capsys.readouterr().out
    main(["register", "alice", "--password", PW])
    capsys.readouterr()
    main(["whoami"])
    out = capsys.readouterr().out
    assert "alice" in out and "pantry" in out


def test_logout_when_not_logged_in(capsys):
    assert main(["logout"]) == 0
    assert "Not logged in" in capsys.readouterr().out
