"""Seedance 2.0 走 CN 站 sparkvideo 接口；不迁海外版。"""

from app.core.model_registry import CANVAS_MODEL_BY_NAME
from app.integrations.upstream.runninghub import ROUTES

_SEEDANCE_20 = {
    "rh_seedance_20_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0/multimodal-video",
    "rh_seedance_20_fast_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/multimodal-video",
    "rh_seedance_20_mini_r2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/multimodal-video",
    "rh_seedance_20_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0/image-to-video",
    "rh_seedance_20_fast_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/image-to-video",
    "rh_seedance_20_mini_i2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/image-to-video",
    "rh_seedance_20_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0/text-to-video",
    "rh_seedance_20_fast_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-fast/text-to-video",
    "rh_seedance_20_mini_t2v": "/openapi/v2/rhart-video/sparkvideo-2.0-mini/text-to-video",
}

_SEEDANCE_25 = (
    "rh_seedance_25_r2v",
    "rh_seedance_25_t2v",
    "rh_seedance_25_i2v",
)


def test_seedance_20_cn_sparkvideo_routes():
    for name, path in _SEEDANCE_20.items():
        spec = CANVAS_MODEL_BY_NAME.get(name)
        assert spec is not None, name
        assert spec.provider == "runninghub", name
        extra = spec.parameters_extra or {}
        assert extra.get("use_ltx_key") is False, name
        assert ROUTES[name] == path
        assert not path.startswith("/openapi/v2/bytedance/seedance-2.0")


def test_seedance_25_stays_cn_token_routes():
    for name in _SEEDANCE_25:
        spec = CANVAS_MODEL_BY_NAME.get(name)
        assert spec is not None, name
        assert spec.provider == "runninghub", name
        extra = spec.parameters_extra or {}
        assert extra.get("use_ltx_key") is False, name
        assert ROUTES[name].startswith("/openapi/v2/bytedance/seedance-2.5-token/")
