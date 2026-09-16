"""会话备忘 compaction：超阈值用备忘替换早期原文。"""

from app.services.agent_context_compact import (
    build_context_memo,
    compact_role_contents,
    should_compact_history,
)


def test_build_context_memo_extracts_nodes_and_goals():
    memo = build_context_memo(
        [
            ("user", "[节点:产品|nodeId=img1|type=image_input] 做宣传片"),
            ("assistant", "已搭故事板，待确认生成"),
            ("user", "先改文案"),
        ],
        skill_hint="product-cinematic-commercial",
    )
    assert "【会话备忘】" in memo
    assert "img1" in memo
    assert "product-cinematic-commercial" in memo
    assert "确认生成" in memo or "待确认" in memo


def test_compact_keeps_recent_when_over_budget():
    big = "x" * 5000
    rows = [("user", f"goal-{i} {big}") for i in range(12)]
    assert should_compact_history(rows) is True
    out, memo = compact_role_contents(rows, skill_hint="viral-remake")
    assert out[0][0] == "user"
    assert out[0][1].startswith("【会话备忘】")
    assert memo.startswith("【会话备忘】")
    # 最近 4 条保留
    assert len(out) == 1 + 4
    assert out[-1][1].startswith("goal-11")


def test_compact_noop_when_short():
    rows = [("user", "你好"), ("assistant", "请说目标")]
    out, memo = compact_role_contents(rows, previous_memo="")
    assert out == rows
    assert memo == ""
