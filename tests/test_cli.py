from pathlib import Path

from lettuceremind.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_scan_and_expiring_flow(tmp_path, capsys):
    store = str(tmp_path / "pantry.json")
    rc = main(["--store", store, "scan", str(FIXTURES / "walmart.txt"),
               "--date", "2026-05-14"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "chicken breast" in out
    assert "Saved" in out

    rc = main(["--store", store, "list"])
    assert rc == 0
    assert "bananas" in capsys.readouterr().out

    rc = main(["--store", store, "expiring", "--days", "100000"])
    assert rc == 0
    assert "need attention" in capsys.readouterr().out


def test_scan_dry_run_saves_nothing(tmp_path, capsys):
    store = str(tmp_path / "pantry.json")
    rc = main(["--store", store, "scan", str(FIXTURES / "kroger.txt"), "--dry-run"])
    assert rc == 0
    assert "Dry run" in capsys.readouterr().out
    main(["--store", store, "list"])
    assert "empty" in capsys.readouterr().out


def test_add_and_remove(tmp_path, capsys):
    store = str(tmp_path / "pantry.json")
    rc = main(["--store", store, "add", "greek yogurt", "--date", "2026-07-01"])
    assert rc == 0
    assert "yogurt" in capsys.readouterr().out
    rc = main(["--store", store, "remove", "yogurt"])
    assert rc == 0
    rc = main(["--store", store, "remove", "yogurt"])
    assert rc == 1


def test_scan_missing_file_errors_cleanly(tmp_path, capsys):
    rc = main(["--store", str(tmp_path / "p.json"), "scan", "nope.txt"])
    assert rc == 1
    assert "error" in capsys.readouterr().err
