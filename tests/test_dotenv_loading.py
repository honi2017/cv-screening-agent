"""Tests for screen.cli's .env loading (load_dotenv_file / bootstrap_env).

Before this fix, TRAKSTAR_API_KEY was read straight from os.environ and
nothing in the codebase ever opened .env -- a cron job (nothing exported)
could never authenticate. These tests cover: .env supplying a missing var,
a real environment variable taking precedence over .env, a missing .env
being a non-error, python-dotenv's normal parsing (comments, blank lines,
quotes, an embedded "="), and -- the one that matters most for a
credentials file -- that a key's value never reaches stdout/stderr on a
failure path.

Every test scrubs the two keys a project .env can supply
(TRAKSTAR_API_KEY, OPENING_ID) via monkeypatch.delenv *before* touching
them. python-dotenv mutates os.environ directly rather than through
monkeypatch, but monkeypatch.delenv still snapshots "absent" at that point
and restores exactly that on teardown, so no test here leaks state into
another one. No test reads the real project .env: each writes its own
under tmp_path (and monkeypatch.chdir's into tmp_path for the tests that
exercise cwd-based resolution through main()). Only obvious dummy values
are ever used -- never a realistic-looking credential.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from screen.cli import EXIT_AUTH, EXIT_OK, bootstrap_env, load_dotenv_file, main

sys.path.insert(0, str(Path(__file__).parent))

ROLE = str(Path(__file__).resolve().parents[1] / "roles" / "fde")
DUMMY_KEY = "test-key-not-real"

_DOTENV_KEYS = ("TRAKSTAR_API_KEY", "OPENING_ID")


@pytest.fixture(autouse=True)
def _scrub_dotenv_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure a clean, restorable slate for every test in this module."""
    for key in _DOTENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# --- load_dotenv_file: precedence, missing file, parsing --------------------


def test_dotenv_supplies_missing_var(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(f"TRAKSTAR_API_KEY={DUMMY_KEY}\n")

    assert load_dotenv_file(dotenv_path) is True

    import os

    assert os.environ["TRAKSTAR_API_KEY"] == DUMMY_KEY


def test_existing_env_var_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("TRAKSTAR_API_KEY", "from-shell-not-real")
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(f"TRAKSTAR_API_KEY={DUMMY_KEY}\n")

    load_dotenv_file(dotenv_path)

    import os

    assert os.environ["TRAKSTAR_API_KEY"] == "from-shell-not-real"


def test_missing_dotenv_file_is_not_an_error(tmp_path):
    dotenv_path = tmp_path / ".env"  # deliberately never created
    assert dotenv_path.exists() is False

    assert load_dotenv_file(dotenv_path) is False

    import os

    assert "TRAKSTAR_API_KEY" not in os.environ


def test_parses_comments_blank_lines_quotes_and_embedded_equals(tmp_path, monkeypatch):
    monkeypatch.delenv("EXTRA_VALUE", raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "\n"
        "# a comment above the real setting\n"
        "\n"
        '   TRAKSTAR_API_KEY = "test-key-not-real"   \n'
        "OPENING_ID='704353'\n"
        "# another comment\n"
        "EXTRA_VALUE=foo=bar=baz\n"
    )

    assert load_dotenv_file(dotenv_path) is True

    import os

    assert os.environ["TRAKSTAR_API_KEY"] == DUMMY_KEY
    assert os.environ["OPENING_ID"] == "704353"
    assert os.environ["EXTRA_VALUE"] == "foo=bar=baz"


# --- wired into main(): cwd resolution, offline stages, no leak -------------


def test_missing_dotenv_offline_stage_still_runs(tmp_path, monkeypatch):
    """No .env anywhere in cwd: fetch --source folder and precheck must not error."""
    monkeypatch.chdir(tmp_path)  # cwd has no .env
    src = tmp_path / "cvs"
    src.mkdir()
    proj_root = tmp_path / "proj"

    rc = main(
        ["--root", str(proj_root), "--opening", "704353", "--role", ROLE,
         "fetch", "--source", "folder", "--path", str(src)]
    )
    assert rc == EXIT_OK

    rc = main(
        ["--root", str(proj_root), "--opening", "704353", "--role", ROLE,
         "precheck", "--today", "2026-08"]
    )
    assert rc == EXIT_OK


def test_opening_default_resolves_from_dotenv(tmp_path, monkeypatch):
    """--opening's argparse default must see .env's OPENING_ID via bootstrap_env."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"OPENING_ID=555555\nTRAKSTAR_API_KEY={DUMMY_KEY}\n")
    src = tmp_path / "cvs"
    src.mkdir()
    proj_root = tmp_path / "proj"

    # No --opening flag at all: the default must come from .env, not the
    # hard-coded "704353" fallback.
    rc = main(
        ["--root", str(proj_root), "--role", ROLE,
         "fetch", "--source", "folder", "--path", str(src)]
    )
    assert rc == EXIT_OK
    assert (proj_root / "data" / "555555").is_dir()
    assert not (proj_root / "data" / "704353").exists()


def test_bootstrap_env_runs_before_parser_is_built(tmp_path, monkeypatch):
    """Calling bootstrap_env() directly must make the .env value visible."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"TRAKSTAR_API_KEY={DUMMY_KEY}\n")

    bootstrap_env()

    import os

    assert os.environ["TRAKSTAR_API_KEY"] == DUMMY_KEY


# --- the key must never reach output -----------------------------------------


def test_empty_key_error_points_at_something_that_works(tmp_path, monkeypatch, capsys):
    """The old message told operators to fix .env, which fetch.py never read."""
    monkeypatch.chdir(tmp_path)  # no .env, no env var -> key resolves empty
    proj_root = tmp_path / "proj"

    rc = main(
        ["--root", str(proj_root), "--opening", "704353", "--role", ROLE,
         "fetch", "--source", "trakstar"]
    )
    assert rc == EXIT_AUTH

    captured = capsys.readouterr()
    assert "project-root .env" in captured.err
    assert "export" in captured.err.lower()


def test_key_value_never_appears_in_output_on_failure(tmp_path, monkeypatch, capsys):
    """A populated (dummy) key must never leak into stdout/stderr on failure.

    The suite-wide network block (tests/conftest.py) turns any real Trakstar
    call into a connection error before anything leaves the process, so this
    exercises the real failure path without any live call.
    """
    monkeypatch.setenv("TRAKSTAR_API_KEY", DUMMY_KEY)
    monkeypatch.chdir(tmp_path)  # no .env; the shell var above is what's used
    proj_root = tmp_path / "proj"

    rc = main(
        ["--root", str(proj_root), "--opening", "704353", "--role", ROLE,
         "fetch", "--source", "trakstar"]
    )
    assert rc != EXIT_OK

    captured = capsys.readouterr()
    assert DUMMY_KEY not in captured.out
    assert DUMMY_KEY not in captured.err
