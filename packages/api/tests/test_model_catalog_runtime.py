"""P3 模型目录 DB 运行时快照。"""

from __future__ import annotations

from app.core.model_registry import get_model_spec
from app.models.job import Model
from app.services.model_catalog_runtime import (
    clear_runtime_model_specs,
    model_spec_from_catalog,
    resolve_model_spec,
)


def test_model_spec_from_catalog_builds_spec():
    model = Model(
        id=1,
        name="custom_img",
        display_name="Custom",
        provider="dashscope",
        model_type="checkpoint",
        category="image",
        description="d",
        is_available=True,
        parameters={
            "upstreamModel": "wanx-v1",
            "capabilities": ["text_to_image"],
            "implementation": "live",
        },
    )
    spec = model_spec_from_catalog(model)
    assert spec.name == "custom_img"
    assert spec.provider == "dashscope"
    assert "text_to_image" in spec.capabilities
    assert spec.implementation == "live"


def test_resolve_model_spec_db_mode_uses_runtime(monkeypatch):
    monkeypatch.setenv("MODEL_CATALOG_SOURCE", "db")
    from app.core.config import get_settings

    get_settings.cache_clear()
    clear_runtime_model_specs()

    from app.services import model_catalog_runtime as runtime

    runtime._runtime_specs["from_db"] = model_spec_from_catalog(
        Model(
            id=2,
            name="from_db",
            provider="ark",
            category="image",
            is_available=True,
            parameters={"implementation": "live", "capabilities": []},
        )
    )

    assert resolve_model_spec("from_db") is not None
    assert resolve_model_spec("doubao_pro") is None
    assert get_model_spec("from_db") is not None

    clear_runtime_model_specs()


def test_resolve_model_spec_dual_falls_back_registry(monkeypatch):
    monkeypatch.setenv("MODEL_CATALOG_SOURCE", "dual")
    from app.core.config import get_settings

    get_settings.cache_clear()
    clear_runtime_model_specs()

    assert get_model_spec("doubao_pro") is not None
