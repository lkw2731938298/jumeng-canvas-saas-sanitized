"""画布工具主模型对照：默认表 + 速查注入。"""

from app.services.agent_canvas_tool_models import (
    default_tool_primary_models,
    format_agent_canvas_tool_models_doc,
    format_tool_model_bit,
    tool_primary_models_from_cfg,
)
from app.services.agent_tool_catalog import format_canvas_tools_quickref


def test_default_includes_subject_edit_seedance():
    models = default_tool_primary_models()
    assert models["video_subject_edit"] == "rh_seedance_20_r2v"
    assert models["multi_angle"] == "nano_pro_i2i"


def test_cfg_override_primary():
    cfg = {
        "version": 2,
        "tools": {
            "video_subject_edit": {"primary": "custom_video_model", "secondary": ""},
        },
    }
    models = tool_primary_models_from_cfg(cfg)
    assert models["video_subject_edit"] == "custom_video_model"
    # 未覆盖的仍用默认
    assert models["cutout"] == "nano_pro_i2i"


def test_quickref_shows_primary_label():
    text = format_canvas_tools_quickref()
    assert "主模型=" in text
    assert "video_subject_edit" in text
    assert "rh_seedance_20_r2v" in text


def test_quickref_uses_override_map():
    text = format_canvas_tools_quickref(
        {"video_subject_edit": "my_special_r2v", "multi_angle": "nano_pro_i2i"}
    )
    assert "my_special_r2v" in text
    bit = format_tool_model_bit("video_subject_edit", {"video_subject_edit": "my_special_r2v"})
    assert bit.startswith("主模型=my_special_r2v")


def test_models_doc_table():
    doc = format_agent_canvas_tool_models_doc()
    assert "# 画布工具 · 主模型对照" in doc
    assert "`video_subject_edit`" in doc
    assert "`rh_seedance_20_r2v`" in doc
