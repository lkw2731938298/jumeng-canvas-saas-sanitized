"""火山 Doubao-Seed-Evolving 已接入文本目录与方舟 chat。"""

from app.core.model_registry import CANVAS_MODEL_BY_NAME
from app.integrations.llm.chat import REMOTE_MODEL_IDS, VISION_CAPABLE_MODELS, _resolve_remote_model
from app.integrations.providers.dispatch import _LIVE_TEXT_MODELS
from app.services.agent_controller import _FILE_CAPABLE_EXACT, _PREFERRED_ORDER, _is_controller_spec


def test_seed_evolving_in_registry():
    spec = CANVAS_MODEL_BY_NAME["doubao_seed_evolving"]
    assert spec.provider == "doubao"
    assert spec.category == "text"
    assert spec.model_type == "llm"
    assert spec.implementation == "live"
    assert spec.upstream_model == "doubao-seed-evolving"
    assert "chat_completion" in spec.capabilities
    assert _is_controller_spec(spec)


def test_seed_evolving_chat_routing():
    assert REMOTE_MODEL_IDS["doubao_seed_evolving"] == "doubao-seed-evolving"
    assert _resolve_remote_model("doubao_seed_evolving") == "doubao-seed-evolving"
    assert "doubao_seed_evolving" in _LIVE_TEXT_MODELS
    assert "doubao_seed_evolving" in VISION_CAPABLE_MODELS
    assert "doubao_seed_evolving" in _FILE_CAPABLE_EXACT
    assert _PREFERRED_ORDER[0] == "doubao_seed_evolving"
