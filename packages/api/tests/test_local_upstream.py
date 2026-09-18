"""本地 OpenAI 兼容上游：解析响应与供应商配置。"""

from app.core.llm_keys import LlmKeysConfig, ProviderKeys, is_provider_configured
from app.integrations.upstream import local as local_up


def test_extract_image_urls():
    body = {"data": [{"url": "https://example.com/a.png"}, {"url": "https://example.com/b.png"}]}
    assert local_up._extract_urls_from_image_body(body) == [
        "https://example.com/a.png",
        "https://example.com/b.png",
    ]


def test_extract_video_url_and_task_id():
    assert local_up._extract_video_url({"url": "https://example.com/v.mp4"}) == "https://example.com/v.mp4"
    assert local_up._extract_task_id({"data": {"task_id": "abc"}}) == "abc"
    assert local_up._status_of({"status": "succeeded"}) == "succeeded"


def test_local_provider_configured_with_api_base_only():
    cfg = LlmKeysConfig(local=ProviderKeys(api_key="", api_base="http://127.0.0.1:30010/v1"))
    assert is_provider_configured("local", cfg=cfg) is True


def test_local_provider_unconfigured_when_empty():
    cfg = LlmKeysConfig(local=ProviderKeys(api_key="", api_base=""))
    assert is_provider_configured("local", cfg=cfg) is False
