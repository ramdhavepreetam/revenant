"""Tests for the config loader (F2): layered .revenant.toml + precedence."""
from __future__ import annotations

from pathlib import Path

from revenant_cli import config
from revenant_cli.config import resolve, find_project_config, load_config


# --- resolve() precedence ----------------------------------------------------

def test_flag_beats_config_and_default():
    assert resolve("model", "flagmodel", {"model": "cfgmodel"}, "def") == "flagmodel"


def test_config_beats_default_when_flag_is_sentinel():
    assert resolve("model", "", {"model": "cfgmodel"}, "def") == "cfgmodel"


def test_default_when_neither_flag_nor_config():
    assert resolve("model", "", {}, "def") == "def"


def test_store_true_sentinel_is_false():
    # read_only flag not passed (False) but config sets it -> config wins.
    assert resolve("read_only", False, {"read_only": True}, False) is True
    # flag passed True always wins.
    assert resolve("read_only", True, {"read_only": False}, False) is True


def test_int_sentinel_is_zero():
    assert resolve("max_steps", 0, {"max_steps": 12}, 0) == 12
    assert resolve("max_steps", 5, {"max_steps": 12}, 0) == 5


# --- find_project_config walks up ---------------------------------------------

def test_find_project_config_in_workspace(tmp_path: Path):
    (tmp_path / ".revenant.toml").write_text('model = "x"\n')
    assert find_project_config(tmp_path) == (tmp_path / ".revenant.toml")


def test_find_project_config_walks_up(tmp_path: Path):
    (tmp_path / ".revenant.toml").write_text('model = "x"\n')
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_config(nested) == (tmp_path / ".revenant.toml")


def test_find_project_config_absent(tmp_path: Path):
    assert find_project_config(tmp_path) is None


# --- load_config merge (project beats user) -----------------------------------

def test_project_overrides_user(tmp_path: Path, monkeypatch):
    user_dir = tmp_path / "userhome" / "revenant"
    user_dir.mkdir(parents=True)
    (user_dir / "config.toml").write_text('model = "user-model"\nbase_url = "http://user"\n')
    monkeypatch.setattr(config, "user_config_path", lambda: user_dir / "config.toml")

    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / ".revenant.toml").write_text('model = "proj-model"\n')

    merged = load_config(ws)
    # project wins for model; user still supplies base_url.
    assert merged["model"] == "proj-model"
    assert merged["base_url"] == "http://user"


def test_malformed_config_is_skipped(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "nope.toml")
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / ".revenant.toml").write_text("this is not = valid = toml ===")
    merged = load_config(ws)
    # Malformed file yields no keys and a warning, never a crash.
    assert "model" not in merged
    assert "warning" in capsys.readouterr().err.lower()


def test_load_config_no_files(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "nope.toml")
    ws = tmp_path / "empty"
    ws.mkdir()
    merged = load_config(ws)
    # Only the bookkeeping keys, no scalar settings.
    assert not any(k in merged for k in ("model", "base_url", "read_only"))
    assert merged["_project_path"] is None
