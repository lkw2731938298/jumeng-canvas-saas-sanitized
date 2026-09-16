"""助手 run_canvas_tool → canvasOps：缺 userPrompt 时用本轮用户原话补上。"""

from app.services.agent_tools import (
    client_tool_to_canvas_ops,
    intent_prompt_from_user_text,
)


def test_intent_prompt_strips_node_and_attach_tags():
    raw = "[节点:参考视频|nodeId=abc|type=video_input] 把西装男换成动漫角色 [附件:角色|assetId=9897]"
    assert intent_prompt_from_user_text(raw) == "把西装男换成动漫角色"


def test_subject_edit_injects_user_text_as_prompt():
    ops = client_tool_to_canvas_ops(
        "c1",
        "run_canvas_tool",
        {"tool": "video_subject_edit", "nodeId": "vid1"},
        user_text="把西装男换成动漫角色",
    )
    assert len(ops) == 1
    assert ops[0]["tool"] == "video_subject_edit"
    assert ops[0]["params"]["userPrompt"] == "把西装男换成动漫角色"


def test_subject_edit_keeps_explicit_user_prompt():
    ops = client_tool_to_canvas_ops(
        "c1",
        "run_canvas_tool",
        {
            "tool": "video_subject_edit",
            "nodeId": "vid1",
            "params": {"userPrompt": "改成二次元少女"},
        },
        user_text="帮我主体修改",
    )
    assert ops[0]["params"]["userPrompt"] == "改成二次元少女"


def test_bare_tool_name_prompt_is_replaced():
    ops = client_tool_to_canvas_ops(
        "c1",
        "run_canvas_tool",
        {
            "tool": "video_subject_edit",
            "nodeId": "vid1",
            "params": {"userPrompt": "主体修改"},
        },
        user_text="把片里的人改成动漫",
    )
    assert ops[0]["params"]["userPrompt"] == "把片里的人改成动漫"


def test_subject_replace_in_run_enum_and_injects():
    ops = client_tool_to_canvas_ops(
        "c2",
        "run_canvas_tool",
        {"tool": "video_subject_replace", "nodeId": "vid1", "params": {"refImageNodeId": "img1"}},
        user_text="换成参考图里的角色",
    )
    assert ops[0]["params"]["userPrompt"] == "换成参考图里的角色"
    assert ops[0]["params"]["refImageNodeId"] == "img1"


def test_run_canvas_tool_schema_includes_frame_edit():
    from app.services.agent_tools import CANVAS_RUN_TOOL_IDS, openai_tool_schemas

    assert "video_subject_edit" in CANVAS_RUN_TOOL_IDS
    assert "video_subject_replace" in CANVAS_RUN_TOOL_IDS
    run = next(t for t in openai_tool_schemas() if t["function"]["name"] == "run_canvas_tool")
    enum = run["function"]["parameters"]["properties"]["tool"]["enum"]
    assert "video_subject_edit" in enum
    assert "video_subject_replace" in enum
    assert "video_subject_remove" in enum
