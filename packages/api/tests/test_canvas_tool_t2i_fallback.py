"""无参考图时图生图改走文生图。"""

from app.services.canvas_tool_models import (
    media_request_has_image_source,
    t2i_counterpart_model_name,
)


def test_t2i_counterpart_nano_pro():
    assert t2i_counterpart_model_name("nano_pro_i2i") == "nano_pro_t2i"
    assert t2i_counterpart_model_name("nano_pro_i2i_official") == "nano_pro_t2i_official"
    assert t2i_counterpart_model_name("nano_g_i2i") == "nano_g_t2i"
    assert t2i_counterpart_model_name("nano_pro_t2i") is None
    assert t2i_counterpart_model_name("doubao_image") is None


def test_media_request_has_image_source():
    assert media_request_has_image_source("https://oss.example/a.png", []) is True
    assert media_request_has_image_source(None, []) is False
    assert media_request_has_image_source("", [{"type": "image", "url": "https://x/a.png"}]) is True
    assert media_request_has_image_source(None, [{"type": "text", "url": ""}]) is False
    assert media_request_has_image_source(None, [{"type": "image", "url": "  "}]) is False
