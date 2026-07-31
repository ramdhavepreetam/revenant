"""Tests for the config loader (F2): layered .revenant.toml + precedence."""
from __future__ import annotations

from pathlib import Path

from revenant_cli import config
from revenant_cli.config import (
    resolve, find_project_config, load_config, mcp_server_specs,
)


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


# --- [[mcp.servers]] reader (F11.3, ADR-0004) -------------------------------

def test_mcp_specs_parses_project_entry():
    cfg = {"_raw_project": {"mcp": {"servers": [
        {"name": "git", "transport": "stdio", "command": "mcp-server-git",
         "args": ["--repo", "."], "read_only": ["status", "log"], "alias": "g"},
    ]}}, "_raw_user": {}}
    (spec,) = mcp_server_specs(cfg)
    assert spec.name == "git"
    assert spec.command == "mcp-server-git"
    assert spec.args == ["--repo", "."]
    assert spec.read_only == ["status", "log"]
    assert spec.alias == "g"


def test_mcp_specs_empty_when_no_section():
    assert mcp_server_specs({"_raw_project": {}, "_raw_user": {}}) == []


def test_mcp_specs_project_overrides_user_by_name():
    cfg = {
        "_raw_user": {"mcp": {"servers": [
            {"name": "git", "command": "user-git"}]}},
        "_raw_project": {"mcp": {"servers": [
            {"name": "git", "command": "project-git"}]}},
    }
    (spec,) = mcp_server_specs(cfg)
    assert spec.command == "project-git"


def test_mcp_specs_skips_entry_without_name(capsys):
    cfg = {"_raw_project": {"mcp": {"servers": [
        {"command": "no-name"}, {"name": "ok", "command": "c"}]}},
        "_raw_user": {}}
    specs = mcp_server_specs(cfg)
    assert [s.name for s in specs] == ["ok"]
    assert "without a name" in capsys.readouterr().err


# --- [verify] section (H1, ADR-0012) ----------------------------------------

from revenant_cli.config import verify_config


def test_verify_config_defaults_off():
    v = verify_config({"_raw_project": {}, "_raw_user": {}})
    assert v["enabled"] is False
    assert v["commands"] == []
    assert v["max_repair_attempts"] == 3
    assert v["pycompile"] is True


def test_verify_config_parses_section():
    cfg = {"_raw_project": {"verify": {
        "enabled": True, "commands": ["ruff check {paths}", "pytest -q"],
        "max_repair_attempts": 2, "pycompile": False}},
        "_raw_user": {}}
    v = verify_config(cfg)
    assert v["enabled"] is True
    assert v["commands"] == ["ruff check {paths}", "pytest -q"]
    assert v["max_repair_attempts"] == 2
    assert v["pycompile"] is False


def test_verify_config_project_overrides_user():
    cfg = {"_raw_user": {"verify": {"enabled": False}},
           "_raw_project": {"verify": {"enabled": True}}}
    assert verify_config(cfg)["enabled"] is True


# --- [context] section (H2, ADR-0013) ----------------------------------------

from revenant_cli.config import context_config


def test_context_config_defaults_on():
    c = context_config({"_raw_project": {}, "_raw_user": {}})
    assert c["inject_on_edit"] is True
    assert c["resolve_errors"] is True
    assert c["max_callers"] == 5


def test_context_config_parses_section():
    cfg = {"_raw_project": {"context": {
        "inject_on_edit": False, "resolve_errors": True, "max_callers": 2}},
        "_raw_user": {}}
    c = context_config(cfg)
    assert c["inject_on_edit"] is False
    assert c["resolve_errors"] is True
    assert c["max_callers"] == 2


def test_context_config_project_overrides_user():
    cfg = {"_raw_user": {"context": {"inject_on_edit": True}},
           "_raw_project": {"context": {"inject_on_edit": False}}}
    assert context_config(cfg)["inject_on_edit"] is False


def test_context_config_ignores_malformed_section():
    cfg = {"_raw_project": {"context": "not-a-table"}, "_raw_user": {}}
    c = context_config(cfg)
    assert c["inject_on_edit"] is True
    assert c["max_callers"] == 5
