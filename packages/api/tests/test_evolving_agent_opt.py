"""Doubao-Seed-Evolving 助手：思考开关、并行读、文档摘要、缓存延期。"""

from app.integrations.llm.chat import (
    EVOLVING_CONTEXT_CACHE_DEFERRED,
    EVOLVING_DEEP_MAX_TOKENS,
    EVOLVING_FAST_MAX_TOKENS,
    apply_evolving_chat_tools_payload,
    cached_prompt_tokens_from_usage,
    evolving_tools_call_kwargs,
    visible_text_and_reasoning,
)
from app.services.agent_followup import _focused_content_block
from app.services.agent_generate_intent import evolving_use_deep_thinking
from app.services.agent_runtime import max_server_rounds_for_model
from app.services.agent_tools import _INSPECT_TEXT_MAX, _WF_SNAP_EDGE_MAX, _WF_SNAP_NODE_MAX


def test_evolving_fast_path_named_atomic_and_image_only():
    assert evolving_use_deep_thinking("打光") is False
    assert evolving_use_deep_thinking("看背面") is False
    assert evolving_use_deep_thinking("做一张图") is False
    assert evolving_use_deep_thinking("生成一张故事板") is False
    assert evolving_use_deep_thinking("随便聊聊", chat_only=True) is False


def test_evolving_deep_path_skill_and_recipe():
    assert evolving_use_deep_thinking("打光", has_skill=True) is True
    assert evolving_use_deep_thinking("做宣传片") is True
    assert evolving_use_deep_thinking("一键出海") is True
    assert evolving_use_deep_thinking("帮我按这个产品搭一套镜头") is True


def test_evolving_payload_fast_disables_thinking():
    payload: dict = {}
    kw = evolving_tools_call_kwargs(deep=False)
    apply_evolving_chat_tools_payload(
        payload,
        "doubao_seed_evolving",
        thinking_enabled=kw["thinking_enabled"],
        reasoning_effort=kw["reasoning_effort"],
        parallel_tool_calls=kw["parallel_tool_calls"],
    )
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    assert payload["parallel_tool_calls"] is True
    assert kw["max_tokens"] == EVOLVING_FAST_MAX_TOKENS


def test_evolving_payload_deep_enables_thinking():
    payload: dict = {}
    kw = evolving_tools_call_kwargs(deep=True)
    apply_evolving_chat_tools_payload(
        payload,
        "doubao_seed_evolving",
        thinking_enabled=kw["thinking_enabled"],
        reasoning_effort=kw["reasoning_effort"],
        parallel_tool_calls=kw["parallel_tool_calls"],
    )
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "medium"
    assert payload["parallel_tool_calls"] is True
    assert kw["max_tokens"] == EVOLVING_DEEP_MAX_TOKENS


def test_evolving_payload_does_not_touch_other_models():
    payload = {"model": "doubao-pro"}
    apply_evolving_chat_tools_payload(payload, "doubao_pro", thinking_enabled=True)
    assert "thinking" not in payload
    assert "parallel_tool_calls" not in payload


def test_reasoning_not_in_visible_content():
    visible, reasoning = visible_text_and_reasoning(
        {
            "content": "准备打光",
            "reasoning_content": "用户要补光，应调 lighting",
        }
    )
    assert visible == "准备打光"
    assert "lighting" in reasoning
    vis2, rea2 = visible_text_and_reasoning(
        {
            "content": [
                {"type": "reasoning", "text": "先 inspect"},
                {"type": "text", "text": "开始改画布"},
            ]
        }
    )
    assert vis2 == "开始改画布"
    assert "inspect" in rea2
    assert "先 inspect" not in vis2


def test_empty_content_with_only_reasoning_is_empty_visible():
    visible, reasoning = visible_text_and_reasoning(
        {"content": "", "reasoning_content": "思考中"}
    )
    assert visible == ""
    assert reasoning == "思考中"


def test_max_server_rounds_evolving_only():
    assert max_server_rounds_for_model("doubao_seed_evolving") == 10
    assert max_server_rounds_for_model("doubao_pro") == 6


def test_inspect_and_workflow_snapshot_caps():
    assert _INSPECT_TEXT_MAX == 12000
    assert _WF_SNAP_NODE_MAX == 800
    assert _WF_SNAP_EDGE_MAX == 1200


def test_focused_document_excerpt_from_live_params():
    body = "剧本正文" + ("甲" * 2100)
    block = _focused_content_block(
        {
            "nodes": [
                {
                    "id": "doc1",
                    "label": "分镜剧本",
                    "type": "document_input",
                    "focused": True,
                }
            ],
            "focusedContent": [],
            "liveParams": {"doc1": {"content": body}},
        }
    )
    assert "doc1" in block
    assert "剧本正文" in block
    assert "甲" in block
    # 约前 2k，不是全文
    assert len(block) < len(body)


def test_focused_content_prefers_longer_live_doc():
    block = _focused_content_block(
        {
            "nodes": [{"id": "t1", "label": "大纲", "type": "text_input", "focused": True}],
            "focusedContent": [
                {"id": "t1", "label": "大纲", "type": "text_input", "content": "短"}
            ],
            "liveParams": {"t1": {"content": "这是更长的未保存正文，应进入观察块"}},
        }
    )
    assert "更长的未保存正文" in block


def test_context_cache_deferred_and_usage_parser():
    assert EVOLVING_CONTEXT_CACHE_DEFERRED is True
    assert cached_prompt_tokens_from_usage({"usage": {"prompt_tokens": 10}}) is None
    assert (
        cached_prompt_tokens_from_usage(
            {"usage": {"prompt_tokens_details": {"cached_tokens": 128}}}
        )
        == 128
    )
    assert cached_prompt_tokens_from_usage({"usage": {"prompt_cache_hit_tokens": 64}}) == 64
