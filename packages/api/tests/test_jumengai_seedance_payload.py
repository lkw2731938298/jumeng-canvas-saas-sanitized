"""聚梦网关 Seedance：官方 images + prompt [Image N] 格式。"""

from app.integrations.upstream.jumengai import (
    _build_jumengai_seedance_payload,
    _rewrite_jumengai_prompt_image_refs,
)


def test_rewrite_jumengai_prompt_image_refs():
    out = _rewrite_jumengai_prompt_image_refs(
        "镜头切到 @医生 与 @图片 35 对话",
        ["医生", "图片 35"],
    )
    assert "[Image 1]" in out or "[Image 2]" in out
    assert "@医生" not in out
    assert "@图片 35" not in out


def test_jumengai_seedance_prepends_missing_image_tokens():
    payload = _build_jumengai_seedance_payload(
        model="seedance-2.0/discount4",
        prompt="角色走路",
        duration_sec=5,
        resolution="720p",
        ratio="16:9",
        generate_audio=True,
        image_urls=[
            "https://cdn.example.com/Ihuabu/a.png",
            "https://cdn.example.com/Ihuabu/b.png",
        ],
        video_urls=[],
        audio_urls=[],
        video_mode="r2v",
        real_person_mode=True,
        image_labels=["角色A", "角色B"],
    )
    p = payload.get("prompt") or ""
    assert "[Image 1]" in p and "[Image 2]" in p


def test_jumengai_seedance_r2v_official_payload():
    payload = _build_jumengai_seedance_payload(
        model="seedance-2.0/discount4",
        prompt="角色 @角色A 走向 @角色B",
        duration_sec=11,
        resolution="720p",
        ratio="16:9",
        generate_audio=True,
        image_urls=[
            "https://cdn.example.com/Ihuabu/a.png",
            "https://cdn.example.com/Ihuabu/b.png",
        ],
        video_urls=[],
        audio_urls=[],
        video_mode="r2v",
        real_person_mode=True,
        image_labels=["角色A", "角色B"],
    )
    meta = payload.get("metadata") or {}
    params = meta.get("parameters") or {}

    assert payload.get("images") and len(payload["images"]) == 2
    assert "input_reference" not in payload
    assert "image" not in payload
    assert "[Image 1]" in payload.get("prompt", "")
    assert "[Image 2]" in payload.get("prompt", "")
    assert payload.get("size") == "16:9"
    assert params.get("ratio") == "16:9"
    assert meta.get("resolution") == "720p"
    assert params.get("resolution") == "720p"
    assert meta.get("imageMode") == "reference"
    assert payload.get("conversionSlots") == ["all"]
    content = payload.get("content") or []
    assert any(c.get("subject_type") == "person" for c in content if isinstance(c, dict))


def test_map_jumengai_task_status_failure_uppercase():
    """NewAPI 返回 data.status=FAILURE 须判失败，不能当成仍在 running。"""
    from app.integrations.upstream.jumengai import map_jumengai_task_status

    body = {
        "code": "success",
        "data": {
            "task_id": "task_x",
            "status": "FAILURE",
            "fail_reason": "生成失败，请更换人物",
            "result_url": "生成失败，请更换人物",
            "progress": "100%",
            "data": {
                "status": "failed",
                "error": {"code": "generation_failed", "message": "生成失败，请更换人物"},
            },
        },
    }
    state, url, err = map_jumengai_task_status(body, task_id="task_x", api_base="https://example.com/v1")
    assert state == "failed"
    assert url == ""
    assert "更换人物" in err or "失败" in err


def test_jumengai_seedance_reorders_images_by_prompt_mentions():
    payload = _build_jumengai_seedance_payload(
        model="seedance-2.0/discount4",
        prompt="@角色B 走向 @角色A",
        duration_sec=5,
        resolution="720p",
        ratio="16:9",
        generate_audio=True,
        image_urls=[
            "https://cdn.example.com/Ihuabu/a.png",
            "https://cdn.example.com/Ihuabu/b.png",
        ],
        video_urls=[],
        audio_urls=[],
        video_mode="r2v",
        real_person_mode=True,
        image_labels=["角色A", "角色B"],
    )
    assert payload["images"][0].endswith("b.png")
    assert payload["images"][1].endswith("a.png")
    assert "[Image 1]" in payload.get("prompt", "")
    assert "@角色B" not in payload.get("prompt", "")
