"""管理端模型 CRUD 服务单测。"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.models.job import Model
from app.services.admin_models import (
    _validate_name,
    evaluate_model_connectivity,
)


def test_validate_name_rejects_invalid():
    with pytest.raises(AppError):
        _validate_name("Bad-Name")


def test_validate_name_normalizes():
    assert _validate_name("My_Model_1") == "my_model_1"


def test_connectivity_comfyui_without_url(monkeypatch):
    monkeypatch.setenv("COMFYUI_BASE_URL", "")
    from app.core.config import get_settings

    get_settings.cache_clear()
    model = Model(
        id=1,
        name="local_sd",
        provider="comfyui",
        category="image",
        is_available=True,
        parameters={"implementation": "live"},
    )
    result = evaluate_model_connectivity(model)
    assert result["ok"] is False


def test_connectivity_reserved_custom_model(monkeypatch):
    monkeypatch.setenv("LLM_KEYS_SOURCE", "env")
    from app.core.config import get_settings

    get_settings.cache_clear()
    model = Model(
        id=2,
        name="custom_reserved",
        provider="dashscope",
        category="image",
        is_available=False,
        parameters={"implementation": "reserved"},
    )
    result = evaluate_model_connectivity(model)
    assert result["ok"] is False
    assert "reserved" in result["message"] or "密钥" in result["message"]
