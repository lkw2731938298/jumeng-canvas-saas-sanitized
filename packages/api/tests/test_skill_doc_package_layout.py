"""技能包整包注入：节点布置 / 生成专章优先。"""

from app.services.skill_docs import (
    format_skill_doc_package_context,
    list_skill_doc_package_files_from_disk,
)


def test_package_inject_orders_layout_and_generation_first():
    files = list_skill_doc_package_files_from_disk("product_cinematic_commercial")
    paths = {f["path"] for f in files}
    assert "SKILL.md" in paths
    assert "references/node-layout.md" in paths
    assert "references/generation.md" in paths

    ctx = format_skill_doc_package_context(files, title="电影级")
    i_skill = ctx.find("===== 文件: SKILL.md =====")
    i_layout = ctx.find("===== 文件: references/node-layout.md =====")
    i_gen = ctx.find("===== 文件: references/generation.md =====")
    i_brief = ctx.find("===== 文件: references/creative-brief.md =====")
    assert i_skill >= 0 and i_layout > i_skill and i_gen > i_layout
    if i_brief >= 0:
        assert i_gen < i_brief
    assert "node-layout.md" in ctx
    assert "generation.md" in ctx


def test_all_three_packages_have_layout_and_generation():
    for slug in (
        "product_cinematic_commercial",
        "viral_remake",
        "overseas_localize",
    ):
        paths = {f["path"] for f in list_skill_doc_package_files_from_disk(slug)}
        assert "SKILL.md" in paths, slug
        assert "references/node-layout.md" in paths, slug
        assert "references/generation.md" in paths, slug
        assert "references/canvas-tools.md" in paths, slug
