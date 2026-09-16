"""我的 Skill 创建：默认技能包四文件 + 流程设置写入 flow.md。"""

from app.services.skills_catalog import (
    build_user_skill_package_files,
    default_flow_steps_for_media,
)
from app.services.skill_docs import build_user_skill_markdown


def test_build_user_skill_package_has_four_refs():
    steps = default_flow_steps_for_media("video")
    pkg = build_user_skill_package_files(
        title="测试宣传片",
        description="一句话",
        media_kind="video",
        node_recipe=None,
        flow_steps=steps,
        output_content="多镜成片",
    )
    assert set(pkg.keys()) >= {
        "references/flow.md",
        "references/node-layout.md",
        "references/generation.md",
        "references/canvas-tools.md",
    }
    assert "确认后同批出片" in pkg["references/flow.md"]
    assert "测试宣传片" in pkg["references/node-layout.md"]


def test_flow_steps_overwrite_existing_flow_md():
    pkg = build_user_skill_package_files(
        title="A",
        description="d",
        media_kind="image",
        node_recipe=None,
        flow_steps=[{"title": "自定义一步", "note": "只测覆盖"}],
        output_content="图",
        existing={"references/flow.md": "# 旧流程\n不要保留\n"},
    )
    assert "自定义一步" in pkg["references/flow.md"]
    assert "不要保留" not in pkg["references/flow.md"]


def test_user_skill_markdown_uses_agent_recipe():
    md = build_user_skill_markdown(
        slug="mine-test",
        title="我的条漫",
        description="测",
        pipeline=[],
        defaults={"mediaKind": "image", "idea": "猫"},
        media_kind="image",
    )
    assert "execution: agent_recipe" in md
    assert "references/flow.md" in md
