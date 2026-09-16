"""Agent generationOptions 按时长合法区间钳制。"""

from app.services.agent_generation_options import (
    clamp_generation_options_for_model,
    sanitize_agent_node_params,
)


def test_minimax_h3_clamps_4s_to_5s():
    go, notes = clamp_generation_options_for_model(
        "rh_minimax_hailuo_h3_r2v",
        {"duration": "4", "ratio": "16:9"},
    )
    assert go["duration"] == "5"
    assert go["ratio"] == "16:9"
    assert notes
    assert "5s" in notes[0]
    assert "4s" in notes[0] or "不支持 4" in notes[0]


def test_minimax_h3_keeps_in_range():
    go, notes = clamp_generation_options_for_model(
        "rh_minimax_hailuo_h3_r2v",
        {"duration": "8"},
    )
    assert go["duration"] == "8"
    assert notes == []


def test_minimax_h3_clamps_over_max():
    go, notes = clamp_generation_options_for_model(
        "rh_minimax_hailuo_h3_r2v",
        {"duration": "20"},
    )
    assert go["duration"] == "15"
    assert notes


def test_sanitize_uses_snapshot_model_for_duration():
    clean, notes = sanitize_agent_node_params(
        {"generationOptions": {"duration": "4s", "ratio": "9:16"}},
        model_name="rh_minimax_hailuo_h3_r2v",
    )
    assert clean["generationOptions"]["duration"] == "5"
    assert notes


def test_seedance_allows_4s():
    go, notes = clamp_generation_options_for_model(
        "huahu_seedance_20_r2v",
        {"duration": "4"},
    )
    assert go["duration"] == "4"
    assert notes == []


def test_snapshot_catalog_includes_duration_range():
    from app.services.agent_followup import _snapshot_catalog

    text = _snapshot_catalog(
        {
            "nodes": [
                {
                    "id": "v1",
                    "type": "video_input",
                    "label": "镜头1",
                    "model": "rh_minimax_hailuo_h3_r2v",
                    "generationOptions": {"duration": "6"},
                }
            ],
            "edges": [],
        }
    )
    assert "durationRange=5-15s" in text
    assert "duration=6" in text
