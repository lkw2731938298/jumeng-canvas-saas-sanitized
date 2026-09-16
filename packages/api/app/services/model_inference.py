"""将目录模型 ID 解析为实际上游推理 API 使用的模型名。"""

from __future__ import annotations

from ..models.job import Model


def inference_model_id(model: Model) -> str:
    """返回调用上游时使用的模型标识（工具类模型可映射到 inferenceModel）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    if model.category == "tool":
        inference = params.get("inferenceModel") or params.get("inference_model")
        if inference:
            return str(inference)
    return model.name


def output_asset_category(model: Model) -> str:
    """返回生成产物在资产索引中的 category（image/video/audio 等）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    if model.category == "tool":
        output = params.get("outputCategory") or params.get("output_category") or "image"
        return str(output)
    return model.category
