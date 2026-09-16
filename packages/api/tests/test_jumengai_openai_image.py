"""聚梦网关：OpenAI 图片 url/b64_json；视频 URL 提取。"""

from __future__ import annotations

import base64

from app.integrations.upstream.fetch import decode_data_url
from app.integrations.upstream.jumengai import (
    _extract_image_urls,
    _extract_video_url,
    map_jumengai_task_status,
)


def test_image_prefers_nonempty_url() -> None:
    body = {
        "created": 1,
        "data": [{"url": "https://cdn.example/a.png", "b64_json": "iVBORw0KGgo="}],
    }
    urls = _extract_image_urls(body)
    assert urls == ["https://cdn.example/a.png"]


def test_image_empty_url_uses_b64_json() -> None:
    png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    ).decode("ascii")
    body = {"data": [{"url": "", "b64_json": png}]}
    urls = _extract_image_urls(body)
    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")
    data, mime = decode_data_url(urls[0])
    assert mime.startswith("image/png")
    assert data.startswith(b"\x89PNG")


def test_image_both_empty_is_no_image() -> None:
    assert _extract_image_urls({"data": [{"url": "", "b64_json": ""}]}) == []


def test_video_url_from_data_output() -> None:
    body = {
        "status": "success",
        "data": {"output": "https://cdn-sh.aistarslab.sticki.cn/result/video/x.mp4"},
    }
    assert _extract_video_url(body).startswith("https://cdn-sh.aistarslab")
    state, url, _err = map_jumengai_task_status(body)
    assert state == "succeeded"
    assert "sticki.cn" in url
