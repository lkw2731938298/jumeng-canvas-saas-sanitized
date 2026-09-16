"""Agent 黄金用例：意图硬闸 / $skill / openai.yaml / runtime 事件 / update_plan。"""

from __future__ import annotations

from app.services.agent_generate_intent import apply_client_intent_filter
from app.services.agent_runtime_events import (
    append_runtime_event,
    consume_runtime_steer,
    list_runtime_events_since,
)
from app.services.agent_skill_mentions import (
    format_skill_mention_hint,
    parse_skill_dollar_mentions,
)
from app.services.agent_update_plan import apply_update_plan_args
from app.services.skill_openai_policy import (
    load_openai_yaml_text,
    load_platform_skill_openai_policy,
)


def test_golden_storyboard_sheet_only_no_video():
    """只要故事板：禁止视频 generate / 空视频节点。"""
    out = apply_client_intent_filter(
        [
            ("c1", "run_canvas_tool", {"tool": "storyboard", "nodeId": "img1"}),
            ("c2", "add_node", {"type": "video_input"}),
            ("c3", "generate_node", {"nodeId": "v1"}),
        ],
        user_text="生成一张故事板",
        snapshot={"nodes": [{"id": "img1", "type": "image_input", "hasMedia": True}]},
        named=[],
        already_generated=False,
    )
    assert any(
        n == "run_canvas_tool" and a.get("tool") == "storyboard" for _, n, a in out.calls
    )
    assert all(n != "generate_node" for _, n, _ in out.calls)
    assert all(
        not (n == "add_node" and a.get("type") == "video_input") for _, n, a in out.calls
    )


def test_golden_unconfirmed_video_hard_gate():
    """未确认：视频 generate/batch 硬闸拦截。"""
    out = apply_client_intent_filter(
        [
            ("c1", "generate_node", {"nodeId": "v1"}),
            ("c2", "run_canvas_tool", {"tool": "storyboard_batch_videos"}),
        ],
        user_text="先搭好镜头",
        snapshot={"nodes": [{"id": "v1", "type": "video_input"}]},
        named=[],
        already_generated=False,
    )
    assert out.calls == [] or all(c[1] != "generate_node" for c in out.calls)
    assert len(out.video_gate_blocks) >= 1


def test_golden_named_multi_angle_kept():
    """点名多角度：保留 run_canvas_tool multi_angle。"""
    text = "多角度看背面"
    out = apply_client_intent_filter(
        [
            ("c1", "run_canvas_tool", {"tool": "multi_angle", "nodeId": "img1"}),
            ("c2", "add_node", {"type": "video_input"}),
            ("c3", "generate_node", {"nodeId": "v1"}),
        ],
        user_text=text,
        snapshot={
            "nodes": [
                {"id": "img1", "type": "image_input", "hasMedia": True},
                {"id": "v1", "type": "video_input"},
            ]
        },
        named=[],
        already_generated=False,
    )
    assert any(
        n == "run_canvas_tool" and a.get("tool") == "multi_angle" for _, n, a in out.calls
    )


def test_golden_dollar_skill_must_hint_load():
    text = "用 $product-cinematic-commercial 做宣传片"
    names = parse_skill_dollar_mentions(text)
    assert names == ["product-cinematic-commercial"]
    hint = format_skill_mention_hint(names)
    assert "load_skill" in hint


def test_golden_openai_yaml_implicit_default_on():
    policy = load_platform_skill_openai_policy("product_cinematic_commercial")
    assert policy["allowImplicitInvocation"] is True
    assert "宣传片" in (policy.get("displayName") or "")


def test_golden_openai_yaml_can_disable_implicit():
    policy = load_openai_yaml_text(
        "policy:\n  allow_implicit_invocation: false\n"
        "interface:\n  display_name: 仅点名\n"
    )
    assert policy["allowImplicitInvocation"] is False
    assert policy["displayName"] == "仅点名"


def test_golden_openai_yaml_top_level_allow_false():
    """平台简写：顶层 allow_implicit_invocation 必须生效。"""
    policy = load_openai_yaml_text(
        "allow_implicit_invocation: false\n"
        "interface:\n  display_name: 顶层关隐式\n"
    )
    assert policy["allowImplicitInvocation"] is False
    assert policy["displayName"] == "顶层关隐式"


def test_golden_openai_yaml_nested_policy_overrides_top_level():
    policy = load_openai_yaml_text(
        "allow_implicit_invocation: true\n"
        "policy:\n  allow_implicit_invocation: false\n"
        "interface:\n  display_name: nested胜\n"
    )
    assert policy["allowImplicitInvocation"] is False
    assert policy["displayName"] == "nested胜"


def test_golden_update_plan_and_events():
    plan = apply_update_plan_args(
        {
            "steps": [
                {"step": "读画布", "status": "completed"},
                {"step": "布点", "status": "in_progress"},
                {"step": "出片", "status": "pending"},
            ]
        }
    )
    assert plan and plan["steps"][1]["status"] == "in_progress"
    brief: dict = {}
    append_runtime_event(brief, kind="plan_updated", message="ok", data={"n": 3})
    append_runtime_event(brief, kind="ask_user", message="确认生成？")
    evs = list_runtime_events_since(brief, after_id=0)
    assert len(evs) == 2
    assert evs[0]["kind"] == "plan_updated"


def test_golden_steer_consume():
    brief = {"runtimeSteer": "先别出视频，改成 9:16"}
    msg = consume_runtime_steer(brief)
    assert "9:16" in (msg or "")
    assert "runtimeSteer" not in brief
