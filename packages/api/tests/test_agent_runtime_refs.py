"""助手本轮附件 / 准星引用必须被识别。"""

from app.services.agent_runtime import (
    _format_message_refs_hint,
    runtime_stop_requested_from_brief,
)
from app.services.agent_vision_refs import extract_asset_ids_from_canvas_snapshot


def test_refs_hint_lists_attach_and_node():
    text = (
        "[附件:生成苹果电脑三视图.png|assetId=img1]\n"
        "[节点:产品视频|nodeId=n1|type=video_input|assetId=vid1]\n"
        "请做故事板"
    )
    hint = _format_message_refs_hint(text)
    assert "assetId=img1" in hint
    assert "nodeId=n1" in hint or "id=n1" in hint
    assert "必须使用" in hint
    assert _format_message_refs_hint("随便说一句") == ""


def test_snapshot_extract_prefers_focused_image():
    snap = {
        "nodes": [
            {"id": "a", "type": "image_input", "assetId": "board1", "label": "故事板", "focused": False},
            {"id": "b", "type": "image_input", "assetId": "product1", "label": "产品图", "focused": True},
        ]
    }
    ids = extract_asset_ids_from_canvas_snapshot(snap)
    assert ids[0] == "product1"
    assert "board1" in ids


def test_runtime_stop_flag_from_brief():
    assert runtime_stop_requested_from_brief({"runtimeStopRequested": True}) is True
    assert runtime_stop_requested_from_brief({"runtimeStopRequested": False}) is False
    assert runtime_stop_requested_from_brief({}) is False
    assert runtime_stop_requested_from_brief(None) is False
