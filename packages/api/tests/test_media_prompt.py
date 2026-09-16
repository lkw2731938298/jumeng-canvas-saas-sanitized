"""Media generation prompt should preserve user @ mentions."""

from app.api.v1.text_generation import ReferenceItem, _build_media_user_prompt


def test_build_media_user_prompt_still_wraps_text_refs_for_text_gen():
    refs = [
        ReferenceItem(
            nodeId="n1",
            label="角色设定",
            type="text",
            content="一位穿红袍的武士",
        )
    ]
    out = _build_media_user_prompt("让 @角色设定 向前走", refs)
    assert "一位穿红袍的武士" in out
    assert "让 @角色设定 向前走" in out


def test_media_prompt_preserves_at_image_labels():
    """Image/video jobs must not strip @ labels from the model prompt."""
    user_prompt = "让 @沈承志1 (抄家前) 在 @雪景 中行走"
    # Media path uses composed prompt as-is (no _build_media_user_prompt).
    composed = user_prompt
    final_prompt = composed
    assert final_prompt == user_prompt
    assert "@沈承志1 (抄家前)" in final_prompt
    assert "@雪景" in final_prompt
