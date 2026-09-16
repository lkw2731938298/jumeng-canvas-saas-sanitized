"""Upstream cost (yuan) estimation for admin job export."""

from app.models.job import GenerationJob, Model
from app.services.upstream_cost import (
    compute_upstream_cost_yuan,
    extract_upstream_cost_config,
    format_upstream_cost_yuan,
    merge_upstream_cost_patch,
)


def _video_model(**kwargs) -> Model:
    defaults = {
        "name": "test_video",
        "category": "video",
        "parameters": {"upstreamCost": {"videoYuanPerSecond": 0.9}},
    }
    defaults.update(kwargs)
    return Model(**defaults)


def test_video_cost_is_rate_times_seconds():
    job = GenerationJob(lane="video", input_params={"optionSnapshot": {"duration": "10"}})
    model = _video_model()
    assert compute_upstream_cost_yuan(job, model, video_seconds=10) == 9.0


def test_image_cost_is_flat_per_call():
    job = GenerationJob(lane="image")
    model = Model(
        name="test_image",
        category="image",
        parameters={"upstreamCost": {"yuanPerCall": 0.05}},
    )
    assert compute_upstream_cost_yuan(job, model, video_seconds=None) == 0.05


def test_format_upstream_cost_yuan_trims_zeros():
    assert format_upstream_cost_yuan(2.7) == "2.7"
    assert format_upstream_cost_yuan(None) == ""


def test_merge_upstream_cost_patch():
    params = merge_upstream_cost_patch(
        {},
        video_yuan_per_second=0.9,
        patch_video=True,
    )
    assert params["upstreamCost"]["videoYuanPerSecond"] == 0.9
    assert extract_upstream_cost_config(params)["videoYuanPerSecond"] == 0.9
