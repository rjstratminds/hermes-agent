import importlib
import os
import sys
from pathlib import Path

import pytest

from hermes_cli.env_loader import load_hermes_dotenv, load_onecli_proxy_env


def test_user_env_overrides_stale_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("OPENAI_BASE_URL=https://new.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"


def test_project_env_overrides_stale_shell_values_when_user_env_missing(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://project.example/v1"


def test_user_env_takes_precedence_over_project_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    user_env = home / ".env"
    project_env = tmp_path / ".env"
    user_env.write_text("OPENAI_BASE_URL=https://user.example/v1\n", encoding="utf-8")
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\nOPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [user_env, project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://user.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "project-key"


def test_main_import_applies_user_env_over_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://new.example/v1\nHERMES_INFERENCE_PROVIDER=custom\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"


def test_load_onecli_proxy_env_overrides_stale_proxy_values(tmp_path, monkeypatch):
    env_file = tmp_path / "hermes-m3-proxy.env"
    env_file.write_text(
        "\n".join(
            [
                "ONECLI_GATEWAY_URL=http://openclaw-gcp.tailc13f7e.ts.net:10255",
                "HTTP_PROXY=http://x:test-token@openclaw-gcp.tailc13f7e.ts.net:10255",
                "HTTPS_PROXY=http://x:test-token@openclaw-gcp.tailc13f7e.ts.net:10255",
                "NO_PROXY=127.0.0.1,localhost,api.telegram.org",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9999")
    loaded = load_onecli_proxy_env(env_file)

    assert loaded == env_file
    assert os.getenv("HTTP_PROXY") == "http://x:test-token@openclaw-gcp.tailc13f7e.ts.net:10255"
    assert os.getenv("NO_PROXY") == "127.0.0.1,localhost,api.telegram.org"


def test_load_onecli_proxy_env_rejects_api_port(tmp_path):
    env_file = tmp_path / "hermes-m3-proxy.env"
    env_file.write_text(
        "\n".join(
            [
                "ONECLI_GATEWAY_URL=http://openclaw-gcp.tailc13f7e.ts.net:10254",
                "HTTP_PROXY=http://x:test-token@openclaw-gcp.tailc13f7e.ts.net:10254",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="10255"):
        load_onecli_proxy_env(env_file)
