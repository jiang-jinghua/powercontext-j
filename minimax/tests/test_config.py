from __future__ import annotations

import pytest

from sddctx.config import load_settings
from sddctx.errors import ConfigurationError


def test_environment_overrides_model_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
    monkeypatch.setenv("MINIMAX_MODEL", "MiniMax-M3")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.io/")
    settings = load_settings(home=tmp_path)
    assert settings.minimax_api_key == "test-key"
    assert settings.minimax_model == "MiniMax-M3"
    assert settings.minimax_base_url == "https://api.minimax.io"
    assert settings.require_minimax() == "test-key"


def test_missing_key_raises_actionable_error(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    settings = load_settings(home=tmp_path)
    with pytest.raises(ConfigurationError):
        settings.require_minimax()


def test_paths_default_below_the_submission_root(tmp_path) -> None:
    settings = load_settings(home=tmp_path)
    assert settings.project_dir == (tmp_path / "demo" / "order-service").resolve()
    assert settings.db_path == (tmp_path / ".sdd" / "seekdb").resolve()
    assert settings.runs_dir == (tmp_path / "runs").resolve()
