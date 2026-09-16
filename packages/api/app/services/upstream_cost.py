"""Admin upstream cost (yuan) config and export calculations."""

from __future__ import annotations

from typing import Any

from ..models.job import GenerationJob, Model


def _is_video_job(job: GenerationJob, model_row: Model | None) -> bool:
    if job.lane == "video":
        return True
    if model_row and str(model_row.category or "").strip().lower() == "video":
        return True
    return False


def _parse_positive_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 0:
        return None
    return num


def extract_upstream_cost_config(params: dict[str, Any] | None) -> dict[str, float | None]:
    raw = (params or {}).get("upstreamCost")
    # 兼容旧数据：顶层 videoYuanPerSecond
    if not isinstance(raw, dict):
        top = _parse_positive_float((params or {}).get("videoYuanPerSecond"))
        return {"videoYuanPerSecond": top, "yuanPerCall": None}
    return {
        "videoYuanPerSecond": _parse_positive_float(raw.get("videoYuanPerSecond")),
        "yuanPerCall": _parse_positive_float(raw.get("yuanPerCall")),
    }


def _lookup_res_yuan(by_res: dict[str, Any] | None, resolution: str | None) -> float | None:
    if not isinstance(by_res, dict) or not resolution:
        return None
    key = str(resolution).strip()
    rate = _parse_positive_float(by_res.get(key))
    if rate is not None:
        return rate
    # 兼容旧选项 id：720 → 720p
    if not key.endswith("p") and key not in ("2k", "4k", "native4k", "native1080p"):
        return _parse_positive_float(by_res.get(f"{key}p"))
    return None


def _resolution_yuan_rate(params: dict[str, Any] | None, job: GenerationJob) -> float | None:
    """按分辨率取上游 ¥/秒；有参考视频时用 WithVideoReference 分档。"""
    raw = (params or {}).get("upstreamCost")
    if not isinstance(raw, dict):
        return extract_upstream_cost_config(params).get("videoYuanPerSecond")
    resolution = _job_option_id(job, "resolution")
    ref_mode = _job_option_id(job, "refVideo")
    if ref_mode == "with":
        with_rate = _lookup_res_yuan(
            raw.get("videoYuanPerSecondByResolutionWithVideoReference"),
            resolution,
        )
        if with_rate is not None:
            return with_rate
    rate = _lookup_res_yuan(raw.get("videoYuanPerSecondByResolution"), resolution)
    if rate is not None:
        return rate
    return extract_upstream_cost_config(params).get("videoYuanPerSecond")


def _job_option_id(job: GenerationJob, group_id: str) -> str | None:
    """从任务 input_params 解析某预设组选中项。"""
    params = job.input_params if isinstance(job.input_params, dict) else {}
    for key in ("optionSnapshot", "option_snapshot", "generationOptions", "generation_options"):
        snap = params.get(key)
        if isinstance(snap, dict) and snap.get(group_id):
            return str(snap[group_id])
    if group_id == "resolution":
        api = params.get("api") if isinstance(params.get("api"), dict) else {}
        if api.get("resolution"):
            return str(api["resolution"])
    return None


def merge_upstream_cost_patch(
    params: dict[str, Any],
    *,
    video_yuan_per_second: float | None = None,
    yuan_per_call: float | None = None,
    patch_video: bool = False,
    patch_call: bool = False,
) -> dict[str, Any]:
    out = dict(params)
    upstream = dict(extract_upstream_cost_config(out))
    if patch_video:
        upstream["videoYuanPerSecond"] = video_yuan_per_second
    if patch_call:
        upstream["yuanPerCall"] = yuan_per_call
    cleaned = {
        k: v
        for k, v in upstream.items()
        if v is not None and v > 0
    }
    if cleaned:
        out["upstreamCost"] = cleaned
    elif "upstreamCost" in out:
        out.pop("upstreamCost", None)
    return out


def compute_upstream_cost_yuan(
    job: GenerationJob,
    model_row: Model | None,
    *,
    video_seconds: int | None = None,
) -> float | None:
    """Estimate upstream monetary cost from admin cost-price settings."""
    if not model_row:
        return None
    params = model_row.parameters if isinstance(model_row.parameters, dict) else {}
    cost = extract_upstream_cost_config(params)

    if _is_video_job(job, model_row):
        # 优先按任务所选分辨率（及有无参考视频）取 ¥/秒
        rate = _resolution_yuan_rate(params, job)
        if rate is None:
            rate = cost.get("videoYuanPerSecond")
        if rate is None or rate <= 0:
            return None
        seconds = video_seconds
        if seconds is None or seconds <= 0:
            return None
        return round(rate * seconds, 4)

    per_call = cost.get("yuanPerCall")
    if per_call is None or per_call <= 0:
        return None
    return round(per_call, 4)


def format_upstream_cost_yuan(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "compute_upstream_cost_yuan",
    "extract_upstream_cost_config",
    "format_upstream_cost_yuan",
    "merge_upstream_cost_patch",
]
