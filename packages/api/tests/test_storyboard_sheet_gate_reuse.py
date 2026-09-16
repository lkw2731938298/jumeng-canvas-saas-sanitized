"""故事板护栏：无板才补工具；已有板不注入；不清空 video；未确认则剥视频 generate。"""

from app.services.agent_canvas_orchestrator import (
    apply_storyboard_sheet_gate,
    apply_video_confirm_rail,
    should_skip_storyboard_sheet_gate,
    snapshot_has_completed_storyboard,
    user_confirmed_video_generate,
    wants_storyboard_regen,
)
from app.services.agent_followup import _snapshot_summary


def test_first_request_does_not_skip():
    text = "做6s手机宣传片，先生成故事板，然后参考生成的故事板生成视频"
    assert wants_storyboard_regen(text) is False
    assert should_skip_storyboard_sheet_gate(text, canvas_snapshot=None) is False


def test_completed_board_skips_even_if_user_repeats_storyboard_words():
    snap = {
        "nodes": [
            {
                "id": "img_1",
                "label": "故事板 · 宣传片",
                "role": "storyboard_sheet",
                "hasMedia": True,
                "assetId": "aaa",
            }
        ]
    }
    text = "做6s手机宣传片，先生成故事板，然后参考生成的故事板生成视频"
    assert snapshot_has_completed_storyboard(snap) is True
    assert should_skip_storyboard_sheet_gate(text, canvas_snapshot=snap) is True


def test_kick_message_skips_without_snapshot():
    text = "读板分配镜头：合成板已就绪（节点 abc）。请仔细用视觉阅读该图片"
    assert should_skip_storyboard_sheet_gate(text, canvas_snapshot=None) is True


def test_fresh_kick_message_skips_without_snapshot():
    text = "这是最新画布，请根据用户原目标继续（故事板节点 abc）。已有带图合成板请复用"
    assert should_skip_storyboard_sheet_gate(text, canvas_snapshot=None) is True


def test_regen_overrides_existing_board():
    snap = {
        "nodes": [
            {"id": "img_1", "label": "故事板", "role": "storyboard_sheet", "hasMedia": True}
        ]
    }
    text = "重新生成故事板，金属质感再大气一点"
    assert wants_storyboard_regen(text) is True
    assert should_skip_storyboard_sheet_gate(text, canvas_snapshot=snap) is False


def test_gate_injects_on_first_storyboard_request_keeps_video_nodes():
    """无合成图且用户要故事板 → 补工具；禁止清空 addVideoNodes。"""
    plan, gates = apply_storyboard_sheet_gate(
        {"intent": "chat", "reply": "ok", "addVideoNodes": [{"tempId": "v1"}]},
        user_text="请生成故事板",
        cinematic_phase=None,
        canvas_snapshot=None,
    )
    assert "storyboard_sheet_inject_tool" in gates
    assert "storyboard_sheet_add_anchor" in gates
    assert "storyboard_sheet_defer_videos" not in gates
    tools = plan.get("runCanvasTools") or []
    assert any(t.get("tool") == "storyboard" for t in tools if isinstance(t, dict))
    assert plan.get("addVideoNodes") == [{"tempId": "v1"}]


def test_gate_does_not_inject_when_board_exists():
    snap = {
        "nodes": [
            {"id": "img_1", "label": "故事板", "role": "storyboard_sheet", "hasMedia": True}
        ]
    }
    plan, gates = apply_storyboard_sheet_gate(
        {"intent": "chat", "reply": "ok", "addVideoNodes": [{"tempId": "v1"}]},
        user_text="做6s手机片，先故事板再出视频",
        cinematic_phase=None,
        canvas_snapshot=snap,
    )
    assert "storyboard_sheet_inject_tool" not in gates
    assert plan.get("addVideoNodes") == [{"tempId": "v1"}]
    tools = plan.get("runCanvasTools") or []
    assert not any(
        isinstance(t, dict) and t.get("tool") in ("storyboard", "blocking_storyboard")
        for t in tools
    )


def test_gate_strips_repeat_when_board_exists():
    snap = {
        "nodes": [
            {"id": "img_1", "label": "故事板", "role": "storyboard_sheet", "hasMedia": True}
        ]
    }
    plan, gates = apply_storyboard_sheet_gate(
        {
            "intent": "tool",
            "addImageNodes": [{"tempId": "a1", "label": "故事板锚点"}],
            "addVideoNodes": [{"tempId": "v1", "label": "镜头1"}],
            "runCanvasTools": [{"tool": "storyboard", "nodeId": "a1"}],
        },
        user_text="这是最新画布，请根据用户原目标继续",
        cinematic_phase=None,
        canvas_snapshot=snap,
    )
    assert "storyboard_sheet_reuse_strip_tool" in gates
    assert "storyboard_sheet_reuse_strip_anchor" in gates
    assert plan.get("runCanvasTools") == []
    assert plan.get("addImageNodes") == []
    assert plan.get("addVideoNodes") == [{"tempId": "v1", "label": "镜头1"}]


def test_confirm_phrases():
    assert user_confirmed_video_generate("确认生成") is True
    assert user_confirmed_video_generate("开始成片吧") is True
    assert user_confirmed_video_generate("做6s手机片，先故事板再出视频") is False
    assert user_confirmed_video_generate("生成视频") is False


def test_video_confirm_strips_video_generate_keeps_image():
    snap = {
        "nodes": [
            {"id": "img_cat", "type": "image_input", "hasMedia": False},
            {"id": "vid_1", "type": "video_input", "hasMedia": False},
        ]
    }
    plan, gates = apply_video_confirm_rail(
        {
            "addVideoNodes": [{"tempId": "vid_new"}],
            "generate": [
                {"nodeId": "img_cat"},
                {"nodeId": "vid_1"},
                {"nodeId": "vid_new"},
            ],
            "runCanvasTools": [
                {"tool": "storyboard", "nodeId": "img_cat"},
                {"tool": "storyboard_batch_videos", "nodeId": "sb_main"},
            ],
        },
        user_text="做6s手机片，先故事板再出视频",
        cinematic_phase=None,
        canvas_snapshot=snap,
    )
    assert "video_confirm_strip_generate" in gates
    gen_ids = {
        (g.get("nodeId") if isinstance(g, dict) else g)
        for g in (plan.get("generate") or [])
    }
    assert "img_cat" in gen_ids
    assert "vid_1" not in gen_ids
    assert "vid_new" not in gen_ids
    tools = [t.get("tool") for t in (plan.get("runCanvasTools") or []) if isinstance(t, dict)]
    assert "storyboard" in tools
    assert "storyboard_batch_videos" not in tools


def test_video_confirm_allows_after_confirm():
    plan, gates = apply_video_confirm_rail(
        {
            "addVideoNodes": [{"tempId": "vid_1"}],
            "generate": [{"nodeId": "vid_1"}],
            "runCanvasTools": [{"tool": "storyboard_batch_videos", "nodeId": "sb"}],
        },
        user_text="确认生成",
        cinematic_phase=None,
        canvas_snapshot=None,
    )
    assert gates == []
    assert plan.get("generate") == [{"nodeId": "vid_1"}]
    assert plan.get("runCanvasTools") == [{"tool": "storyboard_batch_videos", "nodeId": "sb"}]


def test_snapshot_summary_readable_and_truncated():
    text = _snapshot_summary(
        {
            "nodeCount": 80,
            "edgeCount": 12,
            "truncated": True,
            "nodes": [
                {
                    "id": "img_1",
                    "label": "故事板",
                    "type": "image_input",
                    "role": "storyboard_sheet",
                    "hasMedia": True,
                    "x": 420,
                    "y": 120,
                    "status": "error",
                    "lastError": "no_image",
                    "focused": True,
                    "shotCount": 8,
                }
            ],
            "edges": [
                {
                    "source": "img_1",
                    "target": "vid_1",
                    "sourceHandle": "image",
                    "targetHandle": "ref_in",
                }
            ],
            "failedNodes": [{"id": "img_1", "label": "故事板", "reason": "no_image"}],
            "focusedContent": [
                {
                    "id": "img_1",
                    "label": "故事板",
                    "type": "image_input",
                    "prompt": "电影感夜景街道",
                    "shotCount": 8,
                    "shotsPreview": [
                        {"shotNo": "1", "duration": "4s", "description": "远景入画"}
                    ],
                }
            ],
        }
    )
    assert "先读快照再动手" in text
    assert "画布规模：节点 80" in text
    assert "快照已截断" in text
    assert "x=420 y=120" in text
    assert "hasMedia=True" in text
    assert "role=storyboard_sheet" in text
    assert "status=error" in text
    assert "shotCount=8" in text
    assert "img_1" in text and "image" in text
    assert "failed" in text
    assert "inspect_node" in text
    # 目录行不含 prompt；正文只在焦点块
    catalog, _, focused = text.partition("【焦点内容】")
    assert 'prompt="' not in catalog
    assert "电影感夜景街道" in focused
    assert "远景入画" in focused


def test_snapshot_summary_missing_not_blank_canvas():
    text = _snapshot_summary(None)
    assert "无画布快照" in text
    assert "勿假设画布为空" in text
