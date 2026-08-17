"""Tests for javis engine configuration resolution."""

from __future__ import annotations

import pytest

from javis.config import DEFAULT_ENGINE, load_config, resolve_engine_name


def test_default_engine_is_corecoder():
    assert DEFAULT_ENGINE == "corecoder"


def test_load_config_missing_file_returns_empty(tmp_path):
    assert load_config(tmp_path) == {}


def test_load_config_reads_json(tmp_path):
    (tmp_path / "config.json").write_text('{"engine": "mock"}', encoding="utf-8")
    assert load_config(tmp_path) == {"engine": "mock"}


def test_load_config_corrupt_json_returns_empty(tmp_path):
    (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
    assert load_config(tmp_path) == {}


def test_resolve_priority_cli_over_env_over_config():
    config = {"engine": "from-config"}
    env = {"JAVIS_ENGINE": "from-env"}
    assert resolve_engine_name(None, config, env) == "from-env"
    assert resolve_engine_name("from-cli", config, env) == "from-cli"


def test_resolve_falls_back_to_default():
    assert resolve_engine_name(None, {}, {}) == "corecoder"
    assert resolve_engine_name(None, {"engine": "from-config"}, {}) == "from-config"
