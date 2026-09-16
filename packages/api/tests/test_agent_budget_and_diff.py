"""注入预算与 update_node_params diff 单测。"""

from app.services.agent_context_budget import (
    BUDGET_OBSERVE_CATALOG,
    apply_observe_budgets,
    clip_budget,
)
from app.services.agent_params_diff import (
    slim_generation_options_patch,
    slim_update_node_params_args,
)
from app.services.agent_tools import client_tool_to_canvas_ops


def test_clip_budget_truncates_with_label():
    text = "字" * 200
    out = clip_budget(text, 80, label="测试")
    assert len(out) <= 80 + 20  # 标签开销
    assert "截断" in out


def test_observe_budgets_cap_catalog():
    huge = "节" * (BUDGET_OBSERVE_CATALOG + 500)
    body = apply_observe_budgets(
        catalog=huge,
        focused="焦点短",
        tools="工具短",
        caps="能力短",
    )
    assert "画布目录" in body
    assert "截断" in body or len(body) < len(huge)


def test_slim_update_only_prompt_patch():
    params = slim_update_node_params_args(
        {
            "nodeId": "n1",
            "params": {
                "prompt": "新提示",
                "model": "",
                "junk": 1,
                "generationOptions": {
                    "duration": "5",
                    "aspectRatio": "",
                    "unused": None,
                },
            },
        }
    )
    assert params == {
        "prompt": "新提示",
        "generationOptions": {"duration": "5"},
    }


def test_slim_update_top_level_prompt():
    params = slim_update_node_params_args(
        {"nodeId": "n1", "prompt": "只改这一句"}
    )
    assert params == {"prompt": "只改这一句"}


def test_client_ops_skip_empty_update():
    ops = client_tool_to_canvas_ops(
        "c1",
        "update_node_params",
        {"nodeId": "n1", "params": {"model": ""}},
    )
    assert ops == []


def test_client_ops_keeps_prompt_diff():
    ops = client_tool_to_canvas_ops(
        "c1",
        "update_node_params",
        {"nodeId": "n1", "params": {"prompt": "hello"}},
    )
    assert len(ops) == 1
    assert ops[0]["params"]["prompt"] == "hello"


def test_slim_generation_options_drops_huge_nested():
    go = slim_generation_options_patch(
        {"duration": "8", "blob": {"x": "y" * 5000}}
    )
    assert go == {"duration": "8"}
