"""画布工具主副模型规范化：缺省回填、旧图片模型迁到视频默认。"""

from app.services.canvas_tool_models import normalize_canvas_tool_models


def test_normalize_fills_subject_edit_default():
    out = normalize_canvas_tool_models({})
    tools = out["tools"]
    assert tools["video_subject_edit"]["primary"] == "rh_seedance_20_r2v"
    assert tools["video_subject_replace"]["primary"] == "rh_seedance_20_r2v"


def test_normalize_migrates_legacy_image_primary_for_subject_edit():
    raw = {
        "version": 3,
        "tools": {
            "video_subject_edit": {"primary": "nano_pro_i2i", "secondary": ""},
            "video_subject_replace": {
                "primary": "nano_pro_i2i_official",
                "secondary": "doubao_image",
            },
        },
    }
    tools = normalize_canvas_tool_models(raw)["tools"]
    assert tools["video_subject_edit"]["primary"] == "rh_seedance_20_r2v"
    assert tools["video_subject_replace"]["primary"] == "rh_seedance_20_r2v"
    assert tools["video_subject_replace"]["secondary"] == ""


def test_normalize_keeps_explicit_video_primary():
    raw = {
        "version": 4,
        "tools": {
            "video_subject_edit": {"primary": "rh_seedance_20_r2v", "secondary": ""},
        },
    }
    tools = normalize_canvas_tool_models(raw)["tools"]
    assert tools["video_subject_edit"]["primary"] == "rh_seedance_20_r2v"
