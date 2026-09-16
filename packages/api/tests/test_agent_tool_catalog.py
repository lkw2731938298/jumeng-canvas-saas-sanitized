"""画布工具速查：官方名 + 功能特点 + 默认模型。"""

from app.services.agent_canvas_tool_names import official_name_for_tool
from app.services.agent_tool_catalog import (
    format_canvas_tools_detailed_index,
    format_canvas_tools_quickref,
    node_catalog_capability,
)
from app.services.agent_tools import CANVAS_RUN_TOOL_IDS, format_canvas_tools_index


def test_quickref_has_official_name_feat_and_default_model():
    text = format_canvas_tools_quickref()
    assert "「多角度」" in text
    assert "`multi_angle`" in text
    assert "azimuth" in text
    assert "默认nano_pro_i2i" in text
    assert "「打光」" in text
    assert "`lighting`" in text
    assert "「分镜表解析」" in text
    assert "默认doubao_pro" in text
    assert "「主体消除」" in text
    assert "默认rh_seedance_20_r2v" in text
    for tid in CANVAS_RUN_TOOL_IDS:
        assert f"`{tid}`" in text, tid


def test_detailed_index_includes_display_name():
    text = format_canvas_tools_detailed_index()
    assert "全能图片 Pro 图生图" in text
    assert "豆包 Pro" in text
    assert format_canvas_tools_index() == text


def test_official_name_for_tool():
    assert official_name_for_tool("multi_angle") == "多角度"
    assert official_name_for_tool("character_sheet") == "角色设定图"
    assert official_name_for_tool("storyboard_batch_videos") == "按表批量出视频"


def test_node_capability_by_type():
    assert "多角度" in node_catalog_capability(
        node_type="image_input", has_media=True
    )
    assert "文生图" in node_catalog_capability(
        node_type="image_input", has_media=False
    )
    assert "分镜表解析" in node_catalog_capability(
        node_type="storyboard_grid", has_media=False
    )
    assert "主体消除" in node_catalog_capability(
        node_type="video_input", has_media=True
    )
