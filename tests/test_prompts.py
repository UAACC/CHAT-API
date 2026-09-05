"""Tests for system prompt resolution (file > env vars > built-in defaults)."""

import json

import pytest

from app.config import Settings
from app.prompts import system_prompts
from app.prompts.system_prompts import DEFAULT_PROMPT_EN, DEFAULT_PROMPT_ZH, get_system_prompt, load_prompts


@pytest.fixture(autouse=True)
def clear_prompt_cache():
    load_prompts.cache_clear()
    yield
    load_prompts.cache_clear()


def _use(monkeypatch, **overrides):
    monkeypatch.setattr(system_prompts, "get_settings", lambda: Settings(**overrides))


def test_defaults_when_nothing_configured(monkeypatch):
    _use(monkeypatch)
    assert get_system_prompt("en") == DEFAULT_PROMPT_EN
    assert get_system_prompt("zh") == DEFAULT_PROMPT_ZH


def test_env_vars_override_defaults(monkeypatch):
    _use(monkeypatch, system_prompt_en="EN from env", system_prompt_zh="ZH from env")
    assert get_system_prompt("en") == "EN from env"
    assert get_system_prompt("zh-CN") == "ZH from env"


def test_partial_env_override_keeps_other_default(monkeypatch):
    _use(monkeypatch, system_prompt_en="only english")
    assert get_system_prompt("en") == "only english"
    assert get_system_prompt("zh") == DEFAULT_PROMPT_ZH


def test_prompt_file_wins_over_env(monkeypatch, tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text(json.dumps({"en": "file EN", "zh": "file ZH"}), encoding="utf-8")
    _use(monkeypatch, system_prompt_file=str(f), system_prompt_en="env EN")
    assert get_system_prompt("en") == "file EN"
    assert get_system_prompt("zh") == "file ZH"


def test_missing_prompt_file_falls_back(monkeypatch, tmp_path):
    _use(monkeypatch, system_prompt_file=str(tmp_path / "nope.json"))
    assert get_system_prompt("en") == DEFAULT_PROMPT_EN


def test_unknown_locale_uses_english(monkeypatch):
    _use(monkeypatch)
    assert get_system_prompt("fr") == DEFAULT_PROMPT_EN


def test_default_prompts_are_generic():
    """The built-in prompts must not carry any specific tenant's data."""
    for text in (DEFAULT_PROMPT_EN, DEFAULT_PROMPT_ZH):
        assert "@" not in text, "default prompt should not contain an email address"
        assert "A.H. Studio" not in text
