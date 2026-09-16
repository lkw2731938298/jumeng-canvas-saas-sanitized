"""画布目录 / 聊天历史 / inspect 批量 id 收集。"""

from types import SimpleNamespace

from app.services.agent_runtime import _history_messages, _turn_user_content
from app.services.agent_tools import collect_inspect_node_ids


def _msg(role: str, content: str, **kwargs):
    return SimpleNamespace(
        role=role,
        content=content,
        graph_patch=kwargs.get("graph_patch"),
        artifacts=kwargs.get("artifacts"),
    )


def test_history_skips_current_user_and_narration():
    rows = [
        _msg("user", "上一轮问题"),
        _msg("assistant", "上一轮回答"),
        _msg(
            "assistant",
            "正在拉片…",
            graph_patch={"_narration": "skill_progress"},
        ),
        _msg("user", "把这张图改成夜景"),
    ]
    out = _history_messages(rows, skip_user_content="把这张图改成夜景")
    assert [x["content"] for x in out] == ["上一轮问题", "上一轮回答"]


def test_history_skips_legacy_artifact_narration():
    rows = [
        _msg("user", "生成一下"),
        _msg("assistant", "已生成素材", artifacts=[{"kind": "media_assets"}]),
        _msg("assistant", "可以继续改"),
    ]
    out = _history_messages(rows)
    assert [x["content"] for x in out] == ["生成一下", "可以继续改"]


def test_history_keeps_ask_choices():
    rows = [
        _msg("user", "出视频吗"),
        _msg(
            "assistant",
            "要现在生成视频吗？",
            artifacts=[{"kind": "ask_choices", "prompt": "要现在生成视频吗？"}],
        ),
        _msg("user", "确认生成"),
    ]
    out = _history_messages(rows, skip_user_content="确认生成")
    assert [x["content"] for x in out] == ["出视频吗", "要现在生成视频吗？"]


def test_turn_user_content_splits_catalog_and_focus():
    text = _turn_user_content(
        {
            "nodes": [{"id": "n1", "label": "剧本", "type": "text_input", "focused": True}],
            "edges": [],
            "focusedContent": [
                {"id": "n1", "label": "剧本", "type": "text_input", "content": "开场对白"}
            ],
        },
        "润色这段",
    )
    assert "【画布目录】" in text
    assert "【焦点内容】" in text
    assert "【画布工具速查】" in text
    assert "【本轮模型能力】" in text
    assert "【本轮用户】" in text
    catalog, _, rest = text.partition("【焦点内容】")
    assert "开场对白" not in catalog
    assert "开场对白" in rest
    assert "润色这段" in rest
    assert "`multi_angle`" in rest
    assert "默认nano_pro_i2i" in rest
    assert "「多角度」" in rest


def test_collect_inspect_node_ids_batch_and_cap():
    ids = collect_inspect_node_ids(
        {"nodeId": "a", "nodeIds": ["b", "a", "c"] + [f"x{i}" for i in range(30)]}
    )
    assert ids[0] == "b"
    assert "a" in ids
    assert len(ids) == 20
    assert collect_inspect_node_ids({}) == []
    assert collect_inspect_node_ids({"nodeId": " only "}) == ["only"]


def test_snapshot_catalog_includes_model_display_and_can():
    from app.services.agent_followup import _snapshot_catalog

    text = _snapshot_catalog(
        {
            "nodes": [
                {
                    "id": "img1",
                    "label": "产品图",
                    "type": "image_input",
                    "model": "nano_pro_i2i",
                    "hasMedia": True,
                }
            ],
            "edges": [],
            "preferredImageModel": "nano_pro_i2i",
        }
    )
    assert "model=nano_pro_i2i「全能图片 Pro 图生图」" in text
    assert "can=再生成;多角度/打光/九宫格/抠图/扩图/高清/故事板" in text
    assert "生图=nano_pro_i2i「全能图片 Pro 图生图」" in text
