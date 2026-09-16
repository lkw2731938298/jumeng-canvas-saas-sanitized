"""技能包 zip 导入 / $skill 点名 / update_plan。"""

from __future__ import annotations

import io
import zipfile

from app.services.agent_skill_mentions import (
    format_skill_mention_hint,
    parse_skill_dollar_mentions,
)
from app.services.agent_update_plan import apply_update_plan_args, normalize_runtime_plan
from app.services.skill_package_import import parse_skill_package_zip, validate_agent_skills_name


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in files.items():
            zf.writestr(path, body.encode("utf-8"))
    return buf.getvalue()


def test_parse_skill_package_zip_basic():
    md = """---
name: demo-recipe
description: 演示配方。用于单元测试。不要用于生产。
metadata:
  jumeng.slug: demo_recipe
---

# Demo

按画布工具执行。
"""
    raw = _make_zip(
        {
            "demo-recipe/SKILL.md": md,
            "demo-recipe/references/flow.md": "## 流程\n一步",
            "demo-recipe/scripts/run.py": "print(1)\n",
            "demo-recipe/assets/note.txt": "skip",
        }
    )
    result = parse_skill_package_zip(raw)
    assert result.name == "demo-recipe"
    assert "演示配方" in result.description
    assert "references/flow.md" in result.package_files
    assert any(p.endswith("run.py") for p in result.unsupported_files)
    assert any("note.txt" in p or p.endswith("note.txt") for p in result.unsupported_files)


def test_parse_skill_package_zip_rejects_bad_name():
    md = """---
name: Bad_Name
description: x
---
body
"""
    raw = _make_zip({"SKILL.md": md})
    try:
        parse_skill_package_zip(raw)
        assert False, "should raise"
    except ValueError as exc:
        assert "name" in str(exc).lower() or "Name" in str(exc) or "name" in str(exc)


def test_validate_agent_skills_name():
    assert validate_agent_skills_name("product-cinematic-commercial") == (
        "product-cinematic-commercial"
    )


def test_parse_skill_dollar_mentions():
    names = parse_skill_dollar_mentions(
        "用 $product-cinematic-commercial 和 $viral-remake 做一下"
    )
    assert names == ["product-cinematic-commercial", "viral-remake"]
    hint = format_skill_mention_hint(names)
    assert "【本轮点名技能】" in hint
    assert "load_skill" in hint


def test_update_plan_single_in_progress():
    plan = apply_update_plan_args(
        {
            "steps": [
                {"step": "读画布", "status": "completed"},
                {"step": "布点", "status": "in_progress"},
                {"step": "出片", "status": "in_progress"},
            ]
        }
    )
    assert plan is not None
    in_prog = [s for s in plan["steps"] if s["status"] == "in_progress"]
    assert len(in_prog) == 1
    assert normalize_runtime_plan({"steps": []}) is None
