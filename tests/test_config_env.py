import json

import pytest

from migration_agent.config_env import EnvConfigError, load_llm_config_from_env, load_settings_from_env
from migration_agent.llm.litellm_client import LLMConfig


def test_defaults_equal_builtin():
    assert load_llm_config_from_env({}) == LLMConfig()
    s = load_settings_from_env({})
    assert (s.api_base, s.max_usd, s.log_level, s.config_path) == (None, None, "WARNING", None)


def test_model_overrides_and_scalars():
    cfg = load_llm_config_from_env({
        "MA_LLM_MODEL": "gemini/x", "MA_LLM_MODEL_VERIFIER": "gemini/y", "MA_LLM_MODEL_NEW_TASK": "m3",
        "MA_LLM_MAX_RETRIES": "9", "MA_LLM_TIMEOUT": "30.5", "MA_LLM_MAX_OUTPUT_TOKENS": "8192",
        "MA_LLM_PRICE_DEFAULT": "0.3,2.5", "MA_LLM_BACKOFF_BASE": "5", "MA_LLM_BACKOFF_MAX": "90",
    })
    assert cfg.default_model == "gemini/x"
    assert cfg.model_for("verifier") == "gemini/y" and cfg.model_for("new_task") == "m3"
    assert cfg.model_for("cross_ref") == LLMConfig().model_for("cross_ref")
    assert (cfg.max_retries, cfg.timeout, cfg.max_output_tokens) == (9, 30.5, 8192)
    assert cfg.default_pricing == (0.3, 2.5) and cfg.backoff_base == 5.0 and cfg.backoff_max == 90.0


def test_blank_values_ignored():
    assert load_llm_config_from_env({"MA_LLM_MODEL": "  ", "MA_LLM_MAX_RETRIES": ""}) == LLMConfig()


def test_config_file_then_env_wins(tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"llm": {"default_model": "from-file", "max_retries": 2}}))
    cfg = load_llm_config_from_env({"MA_LLM_CONFIG": str(f), "MA_LLM_MAX_RETRIES": "7"})
    assert cfg.default_model == "from-file" and cfg.max_retries == 7


def test_missing_config_file():
    with pytest.raises(EnvConfigError, match="MA_LLM_CONFIG"):
        load_llm_config_from_env({"MA_LLM_CONFIG": "/nonexistent/x.json"})


@pytest.mark.parametrize("env,name", [
    ({"MA_LLM_MAX_RETRIES": "abc"}, "MA_LLM_MAX_RETRIES"),
    ({"MA_LLM_MAX_RETRIES": "-1"}, "MA_LLM_MAX_RETRIES"),
    ({"MA_LLM_PRICE_DEFAULT": "3"}, "MA_LLM_PRICE_DEFAULT"),
    ({"MA_MAX_USD": "lots"}, "MA_MAX_USD"),
    ({"MA_LOG_LEVEL": "LOUD"}, "MA_LOG_LEVEL"),
])
def test_invalid_values_name_the_variable(env, name):
    with pytest.raises(EnvConfigError, match=name):
        load_llm_config_from_env(env)


def test_settings():
    s = load_settings_from_env({"MA_LLM_API_BASE": "https://gw.example/v1", "MA_MAX_USD": "2.5", "MA_LOG_LEVEL": "debug"})
    assert s.api_base == "https://gw.example/v1" and s.max_usd == 2.5 and s.log_level == "DEBUG"


def test_does_not_read_process_env_when_mapping_given(monkeypatch):
    monkeypatch.setenv("MA_LLM_MODEL", "leak")
    assert load_llm_config_from_env({}).default_model == LLMConfig().default_model
