"""画布节点媒体生成任务执行（图片 / 视频 / 音频 / 工具）。"""

from __future__ import annotations

from ..core.datetime_util import cst_iso_now, now_cst_naive
import asyncio
import html
import logging
from datetime import datetime, timezone
from typing import Any, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import require_entity_id
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ..integrations.providers import (
    AudioGenRequest,
    ImageGenRequest,
    ProviderNotImplementedError,
    VideoGenRequest,
    generate_audio as provider_generate_audio,
    generate_image as provider_generate_image,
    generate_video as provider_generate_video,
    is_capability_implemented,
)
from ..integrations.providers.types import ImageGenBatchResult
from ..integrations.upstream.fetch import download_bytes
from ..integrations.upstream.jumengai import fetch_jumengai_video
from ..integrations.upstream.errors import UpstreamError
from ..core.job_status import JobStatus
from ..core.llm_keys import is_model_configured
from ..core.model_registry import get_model_spec
from ..models.job import GenerationJob, Model
from ..models.project import Project
from ..services.asset_store import get_project_asset, insert_project_asset
from ..services.project_scope import project_storage_folder
from ..services.storage_quota import assert_storage_quota_for_project
from ..services.credit_flow import commit_for_job, credit_commit_succeeded, fail_job_and_refund
from ..services.credit_operation_lock import (
    claim_job_upstream_submit,
    is_job_upstream_submitted,
    seal_job_upstream_submit,
)
from ..services.generation_jobs import (
    extract_asset_id,
    mark_job_failed,
    mark_job_polling,
    mark_job_succeeded,
    media_output_asset,
)
from ..services.upstream_job_resume import poll_upstream_result_url, upstream_task_id_from_job
from ..services.restart_job_recovery import apply_upstream_awaiting_admin_park
from ..services.generation_presets import compose_generation, normalize_option_ids
from ..services.visual_style import apply_visual_style_to_prompt
from ..integrations.upstream.trace_context import (
    peek_upstream_trace,
    reset_upstream_trace_flush_hook,
    set_upstream_trace_flush_hook,
    take_upstream_trace,
)
from ..services.generation_call_logs import (
    flush_upstream_call_logs_from_trace,
    log_upstream_api_call,
    reset_upstream_call_log_cursor,
)
from ..services.job_trace import apply_upstream_trace, capture_trace_after_upstream
from ..services.model_channels import (
    ModelChannel,
    append_channel_event,
    can_failover_to_next,
    error_code_from_exc,
    merge_channel_into_trace,
    resolve_channels_from_catalog,
)
from ..services.model_inference import inference_model_id, output_asset_category
from ..services.reference_image import _is_probably_image_url
from ..services.storage_urls import normalize_browser_storage_url

logger = logging.getLogger(__name__)

# 上游网关瞬时错误：可能已接单扣费，须停车等人工介入，禁止自动退款（§1.8）
_GATEWAY_HTTP_PARK_STATUSES = frozenset({502, 503, 504})


def should_park_on_upstream_http_status(status_code: int) -> bool:
    """裸 HTTPStatusError 是否应停车等管理员（而非 fail+退款）。"""
    return int(status_code) in _GATEWAY_HTTP_PARK_STATUSES


class _SubmitLockPark(Exception):
    """有 Redis 提交锁但无 provider_task_id：停车等管理员，不 fail、不退款。"""


async def persist_partial_upstream_trace(
    db: AsyncSession,
    job: GenerationJob,
    *,
    model_name: str,
) -> None:
    """上游 submit 回调：拿到 provider_task_id 后立即 commit，不等待 poll 结束。

    仅 request_id 等中间态仍 flush 不 commit，避免无谓小事务。
    """
    snap = peek_upstream_trace()
    if snap is None:
        return
    if not (
        snap.provider_request_id
        or snap.provider_task_id
        or snap.provider_job_id
        or snap.processing_id
        or snap.billing is not None
        or snap.events
    ):
        return
    if snap.provider_task_id:
        await seal_job_upstream_submit(int(job.id))
    apply_upstream_trace(job, snap, model_id=model_name)
    await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
    if snap.provider_task_id and job.status == JobStatus.RUNNING.value:
        job.status = JobStatus.POLLING.value
    await db.flush()
    if snap.provider_task_id:
        await db.commit()
        logger.info(
            "[upstream] job_id=%s provider_task_id=%s committed before poll",
            job.id,
            snap.provider_task_id,
        )

EXT_BY_CATEGORY = {"image": "svg", "video": "mp4", "audio": "mp3"}
MIME_BY_CATEGORY = {"image": "image/png", "video": "video/mp4", "audio": "audio/mpeg"}


def _refs_from_params(raw: list[Any]):
    """将 input_params.references 解析为 ReferenceItem 列表。"""
    from ..api.v1.text_generation import ReferenceItem

    items: list[ReferenceItem] = []
    for entry in raw:
        if isinstance(entry, dict):
            items.append(ReferenceItem.model_validate(entry))
    return items


async def _register_generated_asset(
    db: AsyncSession,
    project_id: str,
    category: str,
    data: bytes,
    content_type: str,
    ext: str,
    title: str,
    subcategory: str | None = None,
) -> dict:
    """将生成产物写入 OSS 并注册到项目素材库，返回资产记录。"""
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    folder = project_storage_folder(project)

    await assert_storage_quota_for_project(db, project_id, len(data))

    asset_id = new_asset_id()
    rel_path = f"assets/{category}/{asset_id}.{ext}"
    storage = get_canvas_storage()
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            data,
            content_type,
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        raise ValueError(f"Storage failed: {exc}") from exc

    is_audio = category == "audio"
    file_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    record = {
        "id": asset_id,
        "projectId": project_id,
        "title": title.strip() or "AI 生成",
        "category": category,
        "subcategory": (subcategory or "素材").strip() or "素材",
        "fileUrl": file_url,
        "thumbnailUrl": "/uploads/audio-default.svg" if is_audio else file_url,
        "fileType": content_type,
        "fileSize": len(data),
        "ossKey": stored.oss_key,
        "source": "generate",
        "createdAt": cst_iso_now(),
    }
    record = await insert_project_asset(db, project_id, record, sync_oss=False)
    from ..services.cache import invalidate_manifest_cache

    await invalidate_manifest_cache(project_id)
    return record


def _placeholder_bytes(category: str, prompt: str, model: str) -> bytes:
    """未对接真实上游时的占位产物字节（开发/兜底用）。"""
    safe_prompt = html.escape(prompt[:80])
    safe_model = html.escape(model)
    if category == "image":
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='640' height='480'>
  <rect fill='#12121c' width='640' height='480'/>
  <text fill='#8b5cf6' x='320' y='220' text-anchor='middle' font-size='18' font-family='sans-serif'>AI 图片生成</text>
  <text fill='#a78bfa' x='320' y='250' text-anchor='middle' font-size='12' font-family='sans-serif'>{safe_model}</text>
</svg>"""
        return svg.encode("utf-8")
    if category == "video":
        return b"<svg xmlns='http://www.w3.org/2000/svg' width='640' height='360'></svg>"
    return f"AI Audio placeholder\nModel: {model}\n".encode("utf-8")


async def _load_project_storage_folder(db: AsyncSession, project_id: str) -> str:
    """加载项目 OSS 存储目录名（用于上游参考图等路径解析）。"""
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    return project_storage_folder(project)


async def _generate_image_batch(
    model: str,
    prompt: str,
    references,
    source_url: Optional[str],
    api_params: dict,
    project_id: str,
    storage_folder: str,
) -> ImageGenBatchResult:
    """调用上游图片生成，返回一批 ImageGenResult（支持 api.n 多张）。"""
    spec = get_model_spec(model)
    if not spec or not (
        "text_to_image" in spec.capabilities or "image_to_image" in spec.capabilities
    ):
        raise ValueError(f"Image model '{model}' is not registered for image generation")
    if not is_model_configured(model):
        raise ValueError(f"Model '{model}' is not configured (configure provider credentials in admin)")

    from ..api.v1.text_generation import media_reference_urls

    ref_urls: list[str] = []
    if source_url:
        ref_urls.append(str(source_url))
    for url in media_reference_urls(references):
        if url not in ref_urls:
            ref_urls.append(url)

    size = str(api_params.get("size") or "2K")
    watermark = bool(api_params.get("watermark", False))

    if is_capability_implemented(model, "text_to_image") or is_capability_implemented(
        model, "image_to_image"
    ):
        result = await provider_generate_image(
            ImageGenRequest(
                model_id=model,
                prompt=prompt,
                reference_urls=ref_urls,
                size=size,
                watermark=watermark,
                api_params=api_params,
                project_id=project_id,
                storage_folder=storage_folder,
            )
        )
        if not result.images:
            raise ValueError(f"Model '{model}' returned no image data")
        return result
    raise ProviderNotImplementedError(model, "text_to_image")


async def _resolve_media_channels(
    db: AsyncSession,
    catalog_model: Model,
    params: dict[str, Any],
) -> list[ModelChannel]:
    """构造媒体任务上游通道：主模型自身通道 + 后台配置副模型通道（作 fallback）。

    副模型来自 input_params.canvasToolSecondaryModel（后台配置解析后写入），
    其自身通道整体降级为 fallback 角色追加到主模型通道之后，实现多跳兜底；
    未配置副模型时行为与原逻辑一致（仅主模型自身主/副通道）。
    """
    channels = list(resolve_channels_from_catalog(catalog_model))
    seen = {c.model_name for c in channels}

    secondary_name = str(
        params.get("canvasToolSecondaryModel")
        or params.get("canvas_tool_secondary_model")
        or ""
    ).strip()
    if not secondary_name or secondary_name in seen:
        return channels

    from .model_catalog_cache import get_cached_model_by_name

    # 副模型常为「用户目录停用」的稳定版；后台开关仅隐藏前端选择，副模型仍须可用作兜底。
    sec_model = await get_cached_model_by_name(
        db, secondary_name, category=catalog_model.category, available_only=False
    )
    if sec_model is None:
        # 副模型不在同类目目录时，按名直接追加一条 fallback 通道兜底
        channels.append(
            ModelChannel(role="fallback", model_name=secondary_name, enabled=True, label="配置副模型")
        )
        return channels

    for ch in resolve_channels_from_catalog(sec_model):
        if ch.model_name in seen:
            continue
        seen.add(ch.model_name)
        channels.append(
            ModelChannel(
                role="fallback",
                model_name=ch.model_name,
                enabled=ch.enabled,
                on_errors=ch.on_errors,
                label=ch.label or "配置副模型",
            )
        )
    return channels


class MediaJobResult:
    """媒体任务执行结果：素材记录、类目、提示文案、实际推理模型 ID。

    extra_records：一次生成多张时除主图外的其余素材（前端会扇出为兄弟节点）。
    """

    def __init__(
        self,
        record: dict,
        asset_category: str,
        message: str,
        inference: str,
        extra_records: list[dict] | None = None,
    ):
        self.record = record
        self.asset_category = asset_category
        self.message = message
        self.inference = inference
        self.extra_records = list(extra_records or [])


async def execute_media_job(
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
) -> MediaJobResult:
    """媒体任务执行：复用 / 续轮询(DB task_id) / 或首次向上游 POST。

    防重复 POST：Redis lock:model:submit:{jobId} = "1"，TTL 5 天，只锁 job.id；
    续轮询用 DB 的 provider_task_id，不把上游 task_id 写入 Redis。
    """
    from .runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)
    job_id = int(job.id)
    params = job.input_params if isinstance(job.input_params, dict) else {}
    project_id = str(params.get("projectId") or job.project_id or "")
    category = str(params.get("category") or catalog_model.category or "image")
    inference = inference_model_id(catalog_model)

    await log_upstream_api_call(
        db,
        job,
        action="worker_execute",
        outcome="pending",
        provider=job.provider,
        detail={"category": category, "modelId": inference, "jobStatus": job.status},
    )

    existing = await _load_existing_job_outcome(db, job, project_id, catalog_model)
    if existing:
        await log_upstream_api_call(
            db,
            job,
            action="reuse_output",
            outcome="success",
            provider=job.provider,
            detail={"category": category, "modelId": inference},
        )
        return existing

    # 测试服 Mock：不调上游、不占 submit 锁，直接写占位素材
    from .ai_mock_generation import build_mock_media_bytes, is_ai_mock_enabled, mock_delay

    if is_ai_mock_enabled():
        await mock_delay()
        prompt = str(params.get("prompt") or "").strip()
        data, ext, mime, asset_category = await build_mock_media_bytes(
            category=category if category in ("image", "video", "audio") else str(catalog_model.category or "image"),
            prompt=prompt,
            model_name=str(catalog_model.name or inference),
        )
        title = str(params.get("assetTitle") or params.get("asset_title") or prompt or "AI_MOCK")[:40] or "AI_MOCK"
        record = await _register_generated_asset(
            db, project_id, asset_category, data, mime, ext, f"[MOCK] {title}"
        )
        await log_upstream_api_call(
            db,
            job,
            action="ai_mock",
            outcome="success",
            provider="ai_mock",
            detail={"category": asset_category, "modelId": inference},
        )
        logger.info(
            "ai_mock media job_id=%s category=%s asset=%s",
            job.id,
            asset_category,
            record.get("id"),
        )
        return MediaJobResult(record, asset_category, "AI_MOCK 占位媒体已写入", inference)

    # 续轮询：只看 DB 上游 task_id（接口 POST 返回的 ID），与 Redis 锁无关
    resume_task_id = upstream_task_id_from_job(job)
    if resume_task_id:
        await log_upstream_api_call(
            db,
            job,
            action="poll_resume",
            outcome="pending",
            provider=job.provider,
            provider_task_id=resume_task_id,
            detail={"category": category, "modelId": inference},
        )
        return await _resume_media_job_from_upstream(db, job, catalog_model, project_id, params)

    # 【锁·job.id】5 天内已 POST 过但 DB 无 task_id → 停车，禁止 fail+退款
    if await is_job_upstream_submitted(job_id):
        raise _SubmitLockPark("该任务已向上游提交过，请勿重复提交")

    # 【锁·job.id】SET NX 占位，防并发 POST
    if not await claim_job_upstream_submit(job_id):
        if upstream_task_id_from_job(job):
            return await _resume_media_job_from_upstream(db, job, catalog_model, project_id, params)
        raise _SubmitLockPark("上游提交处理中，请稍后重试")

    try:
        prompt = str(params.get("prompt") or "").strip()
        references = _refs_from_params(params.get("references") or [])
        source_url = params.get("sourceUrl") or params.get("source_url")
        generation_options = params.get("generationOptions") or params.get("generation_options") or {}

        presets = None
        if isinstance(catalog_model.parameters, dict):
            presets = catalog_model.parameters.get("generationPresets")

        visual_style_id = params.get("visualStyleId") or params.get("visual_style_id")
        prompt_with_style = apply_visual_style_to_prompt(prompt, str(visual_style_id) if visual_style_id else None)

        option_ids = normalize_option_ids(presets, generation_options if isinstance(generation_options, dict) else None)
        composed_prompt, api_params = compose_generation(prompt_with_style, presets, option_ids)
        from ..api.v1.text_generation import media_reference_urls

        final_prompt = composed_prompt
        asset_subcategory = str(params.get("assetSubcategory") or params.get("asset_subcategory") or "").strip() or None
        asset_title_override = str(params.get("assetTitle") or params.get("asset_title") or "").strip() or None
        asset_category = output_asset_category(catalog_model)
        storage_folder = await _load_project_storage_folder(db, project_id)

        channels = await _resolve_media_channels(db, catalog_model, params)
        last_exc: BaseException | None = None
        failover_from: str | None = None
        failover_reason: str | None = None

        for idx, channel in enumerate(channels):
            # 上一通道若已写入 provider_task_id，禁止再 POST，改续轮询
            await db.refresh(job)
            if upstream_task_id_from_job(job):
                return await _resume_media_job_from_upstream(
                    db, job, catalog_model, project_id, params
                )

            inference = channel.model_name or inference_model_id(catalog_model)
            spec = get_model_spec(inference)
            has_next = idx + 1 < len(channels)

            append_channel_event(
                job,
                event="channel_submit",
                detail={
                    "role": channel.role,
                    "modelName": inference,
                    "provider": (spec.provider if spec else None) or job.provider,
                },
            )

            try:
                result = await _generate_media_once(
                    db=db,
                    job=job,
                    catalog_model=catalog_model,
                    inference=inference,
                    spec=spec,
                    category=category,
                    final_prompt=final_prompt,
                    composed_prompt=composed_prompt,
                    references=references,
                    source_url=source_url,
                    api_params=api_params if isinstance(api_params, dict) else {},
                    project_id=project_id,
                    storage_folder=storage_folder,
                    asset_category=asset_category,
                    asset_subcategory=asset_subcategory,
                    asset_title_override=asset_title_override,
                    prompt=prompt,
                    media_reference_urls=media_reference_urls,
                    # 音色/复刻等音频选项从原始 params 透传（勿漏 generation_options）
                    params=params if isinstance(params, dict) else {},
                    generation_options=generation_options if isinstance(generation_options, dict) else {},
                )
                merge_channel_into_trace(
                    job,
                    catalog_name=catalog_model.name,
                    channel=channel,
                    failover_from=failover_from,
                    failover_reason=failover_reason,
                )
                await seal_job_upstream_submit(job_id)
                return result
            except (UpstreamError, httpx.HTTPStatusError, ProviderNotImplementedError, ValueError) as exc:
                last_exc = exc
                # 合并本通道失败事件到 job，再清空 contextvar，便于副通道干净提交
                apply_upstream_trace(job, take_upstream_trace(), model_id=inference)
                await db.refresh(job)
                has_task_id = bool(upstream_task_id_from_job(job))
                if can_failover_to_next(
                    exc,
                    current=channel,
                    has_provider_task_id=has_task_id,
                    has_next=has_next,
                ):
                    reason = error_code_from_exc(exc)
                    next_ch = channels[idx + 1]
                    append_channel_event(
                        job,
                        event="channel_failover",
                        detail={
                            "from": channel.role,
                            "to": next_ch.role,
                            "fromModel": inference,
                            "toModel": next_ch.model_name,
                            "errorCode": reason,
                            "errorMessage": str(exc)[:500],
                        },
                    )
                    failover_from = channel.role
                    failover_reason = reason
                    logger.warning(
                        "Media job %s channel %s failed (%s), trying %s",
                        job_id,
                        channel.role,
                        reason,
                        next_ch.role,
                    )
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise ValueError(f"Model '{catalog_model.name}' has no enabled upstream channel")
    except Exception:
        # §1.7：失败路径禁止 clear 上游提交标记，防 Worker 自动重试导致双 POST
        raise


async def _generate_media_once(
    *,
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
    inference: str,
    spec: Any,
    category: str,
    final_prompt: str,
    composed_prompt: str,
    references: Any,
    source_url: Any,
    api_params: dict[str, Any],
    project_id: str,
    storage_folder: str,
    asset_category: str,
    asset_subcategory: str | None,
    asset_title_override: str | None,
    prompt: str,
    media_reference_urls: Any,
    params: dict[str, Any] | None = None,
    generation_options: dict[str, Any] | None = None,
) -> MediaJobResult:
    """单通道上游生成（图片/视频/音频/占位），不含 Redis claim。"""
    job_id = int(job.id)
    extra_records: list[dict] = []
    # 中文注释：音频分支读音色/复刻参数；须由 execute_media_job 显式传入，避免 NameError
    params = params if isinstance(params, dict) else {}
    if not isinstance(generation_options, dict):
        generation_options = (
            params.get("generationOptions") or params.get("generation_options") or {}
        )
    if not isinstance(generation_options, dict):
        generation_options = {}

    if category in ("image", "tool") and spec and (
        "text_to_image" in spec.capabilities or "image_to_image" in spec.capabilities
    ):
        await log_upstream_api_call(
            db,
            job,
            action="image_submit",
            outcome="pending",
            provider=spec.provider,
            detail={"category": category, "modelId": inference},
        )
        batch = await _generate_image_batch(
            inference, final_prompt, references, source_url, api_params, project_id, storage_folder
        )
        title = asset_title_override or prompt[:40] or ("画布工具生成" if category == "tool" else "AI 图片生成")
        records: list[dict] = []
        for idx, img in enumerate(batch.images):
            # 多张时标题加序号，便于素材库区分
            item_title = title if len(batch.images) == 1 else f"{title} ({idx + 1}/{len(batch.images)})"
            records.append(
                await _register_generated_asset(
                    db,
                    project_id,
                    asset_category,
                    img.data,
                    img.content_type,
                    img.ext,
                    item_title,
                    asset_subcategory,
                )
            )
        record = records[0]
        extra_records = records[1:]
        n_ok = len(records)
        message = (
            f"{catalog_model.display_name or inference} 已生成 {n_ok} 张并写入素材库"
            if n_ok > 1
            else f"{catalog_model.display_name or inference} 已生成并写入素材库"
        )
    elif category == "video" and spec and (
        spec.video_mode or "subtitle_erase" in (spec.capabilities or ())
    ):
        from ..api.v1.text_generation import media_reference_urls, media_reference_urls_typed

        ref_urls = media_reference_urls(references)
        ref_images, ref_videos, ref_audios = media_reference_urls_typed(references)
        ref_image_labels = [
            str(getattr(r, "label", "") or "").strip()
            for r in references
            if getattr(r, "type", None) == "image" and str(getattr(r, "url", "") or "").strip()
        ]
        ref_video_labels = [
            str(getattr(r, "label", "") or "").strip()
            for r in references
            if getattr(r, "type", None) == "video" and str(getattr(r, "url", "") or "").strip()
        ]
        if spec.video_mode == "i2v":
            image_refs = [r.url for r in references if r.type == "image" and r.url]
            if not image_refs:
                image_refs = [u for u in ref_urls if _is_probably_image_url(u)]
            first_url = (
                source_url if _is_probably_image_url(str(source_url or "")) else None
            ) or (image_refs[0] if image_refs else None)
            last_url = image_refs[1] if len(image_refs) > 1 else None
            ref_urls = image_refs
        else:
            first_url = source_url or (ref_urls[0] if ref_urls else None)
            last_url = ref_urls[1] if len(ref_urls) > 1 else None
        await log_upstream_api_call(
            db,
            job,
            action="video_submit",
            outcome="pending",
            provider=spec.provider,
            detail={
                "category": category,
                "modelId": inference,
                "videoMode": spec.video_mode,
                **(
                    {"capability": "subtitle_erase"}
                    if "subtitle_erase" in (spec.capabilities or ())
                    else {}
                ),
            },
        )
        # 去字幕：源视频 + 擦除参数写入 api_params（聚梦网关精准版 / RunningHub 精细化版）
        video_api_params = dict(api_params) if isinstance(api_params, dict) else {}
        if "subtitle_erase" in (spec.capabilities or ()):
            video_src = ""
            if source_url:
                video_src = str(source_url).strip()
            if not video_src:
                for r in references or []:
                    if getattr(r, "type", None) == "video" and getattr(r, "url", None):
                        video_src = str(r.url).strip()
                        break
            if not video_src:
                for u in ref_urls or []:
                    if u and str(u).strip():
                        video_src = str(u).strip()
                        break
            if not video_src:
                raise ValueError("去字幕需要源视频（请连接视频节点或传入 videoUrl）")
            video_api_params["videoUrl"] = video_src
            video_api_params["sourceUrl"] = video_src
            go = generation_options if isinstance(generation_options, dict) else {}
            # 从前端 generationOptions 透传擦除类型 / 编码偏好 / 框选区域
            for src_key, dst_key in (
                ("eraseType", "eraseType"),
                ("encodeMode", "encodeMode"),
                ("eraseRatioLocation", "eraseRatioLocation"),
            ):
                raw = go.get(src_key)
                if raw is None or raw == "":
                    raw = params.get(src_key) if isinstance(params, dict) else None
                if raw is None or raw == "":
                    continue
                video_api_params[dst_key] = raw
        from ..api.v1.text_generation import media_file_urls, media_link_urls

        try:
            video_result = await provider_generate_video(
                VideoGenRequest(
                    model_id=inference,
                    prompt=final_prompt,
                    reference_urls=ref_urls,
                    reference_image_urls=ref_images,
                    reference_video_urls=ref_videos,
                    reference_audio_urls=ref_audios,
                    reference_image_labels=ref_image_labels,
                    reference_video_labels=ref_video_labels,
                    first_frame_url=first_url,
                    last_frame_url=last_url,
                    audio_url=ref_audios[0] if ref_audios else None,
                    video_url=video_api_params.get("videoUrl")
                    if "subtitle_erase" in (spec.capabilities or ())
                    else None,
                    file_urls=media_file_urls(references),
                    link_urls=media_link_urls(references),
                    api_params=video_api_params,
                    project_id=project_id,
                    storage_folder=storage_folder,
                )
            )
        except ProviderNotImplementedError as exc:
            raise ValueError(str(exc)) from exc
        if not video_result.data:
            raise ValueError(f"视频模型 '{inference}' 未返回视频数据")
        reused = await _load_existing_job_outcome(db, job, project_id, catalog_model)
        if reused:
            await seal_job_upstream_submit(job_id)
            return reused
        ext = video_result.ext or "mp4"
        content_type = video_result.content_type or "video/mp4"
        title = asset_title_override or prompt[:40] or "AI 视频生成"
        record = await _register_generated_asset(
            db, project_id, asset_category, video_result.data, content_type, ext, title, asset_subcategory
        )
        message = f"{catalog_model.display_name or inference} 视频已生成并写入素材库"
    elif category == "audio" and spec and (
        "text_to_speech" in spec.capabilities
        or "text_to_music" in spec.capabilities
        or "voice_clone" in spec.capabilities
        or "voice_design" in spec.capabilities
        or "audio_separate" in spec.capabilities
    ):
        await log_upstream_api_call(
            db,
            job,
            action="audio_submit",
            outcome="pending",
            provider=spec.provider,
            detail={"category": category, "modelId": inference},
        )
        # 分离音频：源视频写入 videoUrl（RunningHub 配置必填字段）+ sourceUrl 兼容
        audio_api_params = dict(api_params) if isinstance(api_params, dict) else {}
        if "audio_separate" in (spec.capabilities or ()):
            video_src = ""
            if source_url:
                video_src = str(source_url).strip()
            if not video_src:
                for r in references or []:
                    if getattr(r, "type", None) == "video" and getattr(r, "url", None):
                        video_src = str(r.url).strip()
                        break
            if video_src:
                audio_api_params["videoUrl"] = video_src
                audio_api_params["sourceUrl"] = video_src
            else:
                raise ValueError("分离音频需要源视频（请连接视频节点或传入 videoUrl）")
        # 音色：从前端 generationOptions / 顶层 params 透传给 CosyVoice
        go = generation_options if isinstance(generation_options, dict) else {}
        voice_id = (
            str(params.get("voiceId") or params.get("voice_id") or "").strip()
            or str(go.get("voiceId") or go.get("voice") or "").strip()
            or None
        )
        voice_kind = str(go.get("voiceKind") or params.get("voiceKind") or "").strip()
        tts_model = str(go.get("ttsModel") or params.get("ttsModel") or "").strip()
        if voice_id:
            audio_api_params["voice"] = voice_id
            audio_api_params["voiceId"] = voice_id
        if voice_kind:
            audio_api_params["voiceKind"] = voice_kind
        if tts_model:
            audio_api_params["ttsModel"] = tts_model
        # 语速/声调/音量/音高：从前端 generationOptions 或节点 audioGenParams 透传
        agp = params.get("audioGenParams") or params.get("audio_gen_params") or {}
        if not isinstance(agp, dict):
            agp = {}
        for key in ("speechRate", "tone", "volume", "pitch"):
            raw = go.get(key)
            if raw is None or raw == "":
                raw = agp.get(key)
            if raw is None or raw == "":
                continue
            audio_api_params[key] = raw
        # 音乐模型：MiniMax / Suno 字段从 generationOptions 透传
        if "text_to_music" in (spec.capabilities or ()):
            lyrics = str(go.get("lyrics") or params.get("lyrics") or "").strip()
            if lyrics:
                audio_api_params["lyrics"] = lyrics
            music_prompt = str(
                go.get("musicPrompt") or go.get("stylePrompt") or params.get("musicPrompt") or ""
            ).strip()
            if music_prompt:
                audio_api_params["musicPrompt"] = music_prompt
            # Suno Custom：title / tags 必填（歌词走 lyrics 或主 prompt）
            suno_title = str(go.get("title") or params.get("title") or "").strip()
            if suno_title:
                audio_api_params["title"] = suno_title[:80]
            suno_tags = str(go.get("tags") or params.get("tags") or "").strip()
            if suno_tags:
                audio_api_params["tags"] = suno_tags[:1000]
        # 声音复刻：参考音优先 sourceUrl / 音频引用
        ref_audio = ""
        if source_url and (
            "voice_clone" in (spec.capabilities or ()) or inference == "cosyvoice_clone"
        ):
            ref_audio = str(source_url).strip()
        if not ref_audio:
            for r in references or []:
                if getattr(r, "type", None) == "audio" and getattr(r, "url", None):
                    ref_audio = str(r.url).strip()
                    break
        try:
            audio_result = await provider_generate_audio(
                AudioGenRequest(
                    model_id=inference,
                    text=final_prompt,
                    voice_id=voice_id,
                    api_params=audio_api_params,
                    project_id=project_id,
                    storage_folder=storage_folder,
                    reference_audio_url=ref_audio or (
                        str(audio_api_params.get("sourceUrl") or "") or None
                    ),
                )
            )
        except ProviderNotImplementedError as exc:
            raise ValueError(str(exc)) from exc
        if not audio_result.data:
            raise ValueError(f"音频模型 '{inference}' 未返回音频数据")
        # 复刻成功：写入 input_params，并落库用户「我的音色」供语音合成选用
        if getattr(audio_result, "voice_id", None):
            try:
                ip = dict(job.input_params or {}) if isinstance(job.input_params, dict) else {}
                ip["resolvedVoiceId"] = audio_result.voice_id
                job.input_params = ip
            except Exception:
                pass
            if inference == "cosyvoice_clone" or "voice_clone" in (spec.capabilities or ()):
                try:
                    from ..integrations.upstream.dashscope import CLONE_TARGET_MODEL
                    from ..services.user_voice_clones import register_existing_voice_clone

                    prompt_snip = (final_prompt or "").strip().replace("\n", " ")[:24]
                    display_name = (
                        str(go.get("cloneDisplayName") or params.get("cloneDisplayName") or "").strip()
                        or (f"复刻音色·{prompt_snip}" if prompt_snip else "复刻音色")
                    )[:64]
                    saved = await register_existing_voice_clone(
                        db,
                        user_id=int(job.user_id),
                        voice_id=str(audio_result.voice_id),
                        display_name=display_name,
                        source_audio_url=ref_audio or None,
                        target_model=CLONE_TARGET_MODEL,
                    )
                    try:
                        ip = dict(job.input_params or {}) if isinstance(job.input_params, dict) else {}
                        ip["resolvedCloneId"] = saved.get("id")
                        ip["resolvedVoiceLabel"] = saved.get("display_name")
                        job.input_params = ip
                    except Exception:
                        pass
                except Exception:
                    logger.warning(
                        "persist cloned voice failed job=%s voice=%s",
                        getattr(job, "id", None),
                        getattr(audio_result, "voice_id", None),
                        exc_info=True,
                    )
        ext = audio_result.ext or "mp3"
        content_type = audio_result.content_type or "audio/mpeg"
        title = asset_title_override or prompt[:40] or "AI 音频生成"
        record = await _register_generated_asset(
            db, project_id, asset_category, audio_result.data, content_type, ext, title
        )
        message = f"{catalog_model.display_name or inference} 音频已生成并写入素材库"
    elif category in ("image", "tool"):
        raise ValueError(f"Model '{inference}' does not support image generation")
    else:
        ext = EXT_BY_CATEGORY.get(category, "svg")
        content_type = MIME_BY_CATEGORY.get(category, "image/svg+xml")
        data = _placeholder_bytes(category, composed_prompt, catalog_model.name)
        if category == "audio":
            ext, content_type = "txt", "text/plain; charset=utf-8"
        title = prompt[:40] or "AI 生成"
        asset_category = category if category in MIME_BY_CATEGORY else "image"
        record = await _register_generated_asset(db, project_id, asset_category, data, content_type, ext, title)
        message = "媒体生成 API 占位实现，已写入项目素材库"

    return MediaJobResult(
        record,
        asset_category,
        message,
        inference,
        extra_records=extra_records,
    )


async def _load_existing_job_outcome(
    db: AsyncSession,
    job: GenerationJob,
    project_id: str,
    catalog_model: Model,
) -> MediaJobResult | None:
    """任务已有有效产物资产时直接复用，避免与 sync_upstream 重复入库。"""
    await db.refresh(job)

    asset_id = str(job.asset_id or extract_asset_id(job.output_assets) or "").strip()
    if not asset_id:
        return None

    record = await get_project_asset(db, project_id, asset_id, sync_oss=False)
    if not record:
        return None

    inference = inference_model_id(catalog_model)
    asset_category = output_asset_category(catalog_model)
    message = f"{catalog_model.display_name or inference} 已生成并写入素材库"
    return MediaJobResult(record, asset_category, message, inference)


async def _resume_media_job_from_upstream(
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
    project_id: str,
    params: dict[str, Any],
) -> MediaJobResult:
    """基于已有 provider_task_id 续轮询上游任务并注册产物——不做第二次 submit。

    用于 job 被重试/恢复后重跑：只查询已提交的上游任务、下载结果、写入素材库。
    """
    prompt = str(params.get("prompt") or "").strip()
    category = str(params.get("category") or catalog_model.category or "image")
    asset_subcategory = str(params.get("assetSubcategory") or params.get("asset_subcategory") or "").strip() or None
    asset_title_override = str(params.get("assetTitle") or params.get("asset_title") or "").strip() or None

    inference = inference_model_id(catalog_model)
    asset_category = output_asset_category(catalog_model)
    spec = get_model_spec(inference)
    # RunningHub 海外版模型（LTX、全能图片 Pro/G）：以 use_ltx_key 或 provider=ltx_runninghub 为准
    use_ltx = bool(
        (spec and (spec.parameters_extra or {}).get("use_ltx_key"))
        or (spec and spec.provider == "ltx_runninghub")
        or ((catalog_model.provider or "").strip().lower() == "ltx_runninghub")
    )

    # 标记为 polling：表示已进入“只查询不提交”阶段
    await mark_job_polling(db, job)

    # 仅轮询已有上游任务拿到产物 URL（内部不会 POST 新任务）
    result_url = await poll_upstream_result_url(
        job,
        model_id=inference,
        use_ltx_runninghub=use_ltx,
    )
    # 聚梦网关 /content 需带 Bearer；公网 URL 仍走普通下载
    resume_provider = ((job.provider or "").strip() or ((spec.provider if spec else "") or "")).strip().lower()
    if resume_provider == "jumengai":
        data, content_type = await fetch_jumengai_video(result_url)
    else:
        data, content_type = await download_bytes(result_url)

    # 下载后再次检查：定时 sync 可能已先落库，禁止再注册一份重复资产
    reused = await _load_existing_job_outcome(db, job, project_id, catalog_model)
    if reused:
        return reused

    # 根据类别/上游 content-type 推断落库扩展名、MIME 与默认标题
    if category == "video" or (spec and spec.video_mode):
        ext = "mp4"
        if content_type and "webm" in content_type:
            ext = "webm"
        mime = content_type or "video/mp4"
        title = asset_title_override or prompt[:40] or "AI 视频生成"
    elif category == "audio":
        ext = "mp3"
        if content_type and "wav" in content_type:
            ext = "wav"
        mime = content_type or "audio/mpeg"
        title = asset_title_override or prompt[:40] or "AI 音频生成"
    else:
        ext = "png"
        if content_type and ("jpeg" in content_type or "jpg" in content_type):
            ext = "jpg"
        elif content_type and "webp" in content_type:
            ext = "webp"
        mime = content_type or "image/png"
        title = asset_title_override or prompt[:40] or "AI 图片生成"

    record = await _register_generated_asset(
        db, project_id, asset_category, data, mime, ext, title, asset_subcategory
    )
    message = f"{catalog_model.display_name or inference} 已从上游任务同步并写入素材库"
    return MediaJobResult(record, asset_category, message, inference)


async def process_media_generation_job(db: AsyncSession, job: GenerationJob) -> None:
    """Worker 入口：执行媒体任务、注册 trace/调用日志、结算算力、标记终态。"""
    model_name = job.model or (job.input_params or {}).get("model")
    if not model_name:
        await fail_job_and_refund(db, job, "Missing model on generation job", force=True)
        await db.commit()
        return

    from .model_catalog_cache import get_cached_model_by_name

    # 后台模型开关仅隐藏前端可选模型；模型本身始终可用（含副模型兜底、官方稳定版），
    # 故 Worker 执行时按 available_only=False 解析，只要模型已接入即可执行。
    catalog_model = await get_cached_model_by_name(
        db, model_name, available_only=False
    )
    if not catalog_model:
        await fail_job_and_refund(db, job, f"Model '{model_name}' is not available", force=True)
        await db.commit()
        return

    reset_upstream_call_log_cursor()

    async def _persist_partial_upstream_trace() -> None:
        await persist_partial_upstream_trace(db, job, model_name=model_name)

    # 注册 flush 回调，供各上游 integration 在 submit 成功后调用 maybe_flush_upstream_trace()
    flush_token = set_upstream_trace_flush_hook(_persist_partial_upstream_trace)
    try:
        outcome = await execute_media_job(db, job, catalog_model)
        # 主图 + 其余张数一并写入 output_assets，供前端扇出节点
        output = [media_output_asset(outcome.record, outcome.asset_category)]
        for extra in outcome.extra_records:
            output.append(media_output_asset(extra, outcome.asset_category))
        capture_trace_after_upstream(job, model_id=outcome.inference)
        await flush_upstream_call_logs_from_trace(db, job, model_id=outcome.inference)
        await commit_for_job(job, db)
        if not credit_commit_succeeded(job):
            await mark_job_failed(
                db,
                job,
                "算力结算失败，请联系管理员",
                error_code="CREDIT_COMMIT_FAILED",
            )
            await db.commit()
            return
        await mark_job_succeeded(db, job, output)
        await db.commit()
    except (UpstreamError, httpx.HTTPStatusError) as exc:
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        err_msg = str(exc)
        if isinstance(exc, UpstreamError):
            from ..core.error_codes import format_upstream_job_error_message, normalize_error_code

            code = normalize_error_code(exc.code) or "UPSTREAM_ERROR"
            err_msg = format_upstream_job_error_message(code=code, message=exc.message)
            await log_upstream_api_call(
                db,
                job,
                action="worker_execute",
                outcome="failure",
                provider=job.provider,
                error_message=err_msg,
            )
            # 网关 502/504、轮询 TIMEOUT：上游可能仍在出片，禁止 fail+退款（§1.8）
            if code in ("GATEWAY_TIMEOUT", "TIMEOUT"):
                apply_upstream_awaiting_admin_park(
                    job,
                    error_message=err_msg,
                    reason="gateway_timeout_without_task_id" if code == "GATEWAY_TIMEOUT" else "poll_timeout",
                )
            else:
                await fail_job_and_refund(db, job, err_msg, error_code=code)
        else:
            # 裸 httpx.HTTPStatusError：502/503/504 上游可能已接单，禁止 fail+退款（§1.8）
            http_status = int(exc.response.status_code)
            err_msg = f"上游 HTTP {http_status}"[:2000]
            await log_upstream_api_call(
                db,
                job,
                action="worker_execute",
                outcome="failure",
                provider=job.provider,
                error_message=err_msg,
            )
            park_admin = should_park_on_upstream_http_status(http_status)
            if park_admin:
                apply_upstream_awaiting_admin_park(
                    job,
                    error_message=err_msg or "上游网关异常，任务可能仍在处理，请联系管理员",
                    reason="gateway_http_status",
                )
            else:
                await fail_job_and_refund(
                    db,
                    job,
                    err_msg,
                    error_code="UPSTREAM_HTTP_ERROR",
                )
        await db.commit()
    except httpx.TimeoutException as exc:
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        err_msg = (str(exc).strip() or type(exc).__name__)[:2000]
        await log_upstream_api_call(
            db,
            job,
            action="worker_execute",
            outcome="failure",
            provider=job.provider,
            error_message=err_msg,
        )
        # 客户端读超时：上游可能已生成，停车不退款
        apply_upstream_awaiting_admin_park(
            job,
            error_message=err_msg or "上游请求超时，任务可能仍在处理",
            reason="client_read_timeout",
        )
        await db.commit()
    except _SubmitLockPark as exc:
        apply_upstream_awaiting_admin_park(
            job,
            error_message=str(exc),
            reason="submit_lock_without_task_id",
        )
        await db.commit()
    except Exception as exc:
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        logger.exception("Media job %s failed", job.id)
        # httpx.ReadTimeout 等异常 str() 常为空，须回退类型名避免管理端「无错误信息」
        raw = str(exc).strip() or type(exc).__name__
        err_msg = raw[:2000]
        await log_upstream_api_call(
            db,
            job,
            action="worker_execute",
            outcome="failure",
            provider=job.provider,
            error_message=err_msg,
        )
        await fail_job_and_refund(db, job, err_msg)
        await db.commit()
    finally:
        reset_upstream_trace_flush_hook(flush_token)


def is_canvas_media_job(job: GenerationJob) -> bool:
    """判断是否为画布节点发起的图片/视频/音频媒体任务。"""
    if not job.node_id:
        return False
    return job.job_type in ("image_gen", "video_gen", "audio_gen")
