from app.services.visual_style import apply_visual_style_to_prompt, resolve_visual_style_prompt


def test_resolve_none_style():
    assert resolve_visual_style_prompt(None) == ""
    assert resolve_visual_style_prompt("none") == ""


def test_apply_visual_style_from_bundled_default():
    composed = apply_visual_style_to_prompt("一只猫", "anime")
    assert "一只猫" in composed
    assert "日系动漫" in composed

    assert apply_visual_style_to_prompt("一只猫", "none") == "一只猫"
    assert apply_visual_style_to_prompt("", "anime") != ""
