"""画布上游模型调用的统一请求/响应类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TextGenRequest:
    """文本生成上游请求参数。"""

    model_id: str
    messages: list[dict[str, Any]]
    temperature: float = 1.0
    max_tokens: int = 4096
    project_id: str | None = None
    storage_folder: str | None = None


@dataclass
class ImageGenRequest:
    """图片生成上游请求参数。"""

    model_id: str
    prompt: str
    reference_urls: list[str] = field(default_factory=list)
    size: str = "2K"
    watermark: bool = False
    api_params: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    storage_folder: str | None = None


@dataclass
class ImageGenResult:
    """单张图片生成上游返回结果。"""

    data: bytes
    content_type: str
    ext: str
    source_url: str | None = None


@dataclass
class ImageGenBatchResult:
    """一次请求可产出多张图（generationOptions.count / api.n）。"""

    images: list[ImageGenResult] = field(default_factory=list)

    @property
    def primary(self) -> ImageGenResult:
        if not self.images:
            raise ValueError("ImageGenBatchResult has no images")
        return self.images[0]


@dataclass
class VideoGenRequest:
    """视频生成上游请求参数。"""

    model_id: str
    prompt: str
    reference_urls: list[str] = field(default_factory=list)
    # 画布连线类型拆分（优先于 reference_urls 扩展名猜测）
    reference_image_urls: list[str] = field(default_factory=list)
    reference_video_urls: list[str] = field(default_factory=list)
    reference_audio_urls: list[str] = field(default_factory=list)
    # 与 reference_*_urls 同序的标签，供聚梦网关 prompt [Image N] 转换
    reference_image_labels: list[str] = field(default_factory=list)
    reference_video_labels: list[str] = field(default_factory=list)
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    audio_url: str | None = None
    video_url: str | None = None
    # 万相 3.0 等：文档 file / 网页 link 参考（与图视频参考分离）
    file_urls: list[str] = field(default_factory=list)
    link_urls: list[str] = field(default_factory=list)
    api_params: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    storage_folder: str | None = None


@dataclass
class VideoGenResult:
    """视频生成上游返回结果。"""

    data: bytes
    content_type: str = "video/mp4"
    ext: str = "mp4"
    source_url: str | None = None
    task_id: str | None = None


@dataclass
class AudioGenRequest:
    """音频生成上游请求参数。"""

    model_id: str
    text: str = ""
    voice_id: str | None = None
    reference_audio_url: str | None = None
    api_params: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    storage_folder: str | None = None


@dataclass
class AudioGenResult:
    """音频生成上游返回结果。"""

    data: bytes
    content_type: str
    ext: str
    voice_id: str | None = None
    source_url: str | None = None
