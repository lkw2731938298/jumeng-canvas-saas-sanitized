"""CosyVoice 响应解析：官方 output.audio.url。"""

from app.integrations.upstream.dashscope import _cosyvoice_audio_url_from_response


def test_cosyvoice_audio_url_from_nested_audio_object():
    url = "https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/pre/cosyvoice.mp3"
    data = {
        "request_id": "ee88b03d",
        "output": {
            "finish_reason": "stop",
            "audio": {"data": "", "url": url, "id": "audio_1"},
        },
    }
    assert _cosyvoice_audio_url_from_response(data) == url


def test_cosyvoice_audio_url_legacy_flat_fields():
    url = "https://example.com/a.mp3"
    assert _cosyvoice_audio_url_from_response({"output": {"audio_url": url}}) == url
    assert _cosyvoice_audio_url_from_response({"audio_url": url}) == url


def test_cosyvoice_audio_url_empty():
    assert _cosyvoice_audio_url_from_response({}) == ""
    assert _cosyvoice_audio_url_from_response({"output": {"audio": {"data": "xxx"}}}) == ""
