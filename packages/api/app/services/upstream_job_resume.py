"""续轮询已提交的上游任务，避免二次 submit（保证每个 job 只向上游提交一次）。

设计约束：同一 generation_jobs.id 无论被 Worker 重试、stale 恢复、启动恢复还是进程重启，
只要 DB 里已记录 provider_task_id 等上游标记，就只能基于该标记 poll/下载/同步，
禁止再次向上游 POST 创建新 task（否则同一 job 会被重复扣上游费）。
"""

from __future__ import annotations

from ..integrations.upstream.ark import poll_seedance_task
from ..integrations.upstream.huahu import poll_huahu_seedance_task
from ..integrations.upstream.jumengai import poll_jumengai_video_task
from ..integrations.upstream.credentials import dashscope_creds, kling_creds
from ..integrations.upstream.dashscope import poll_dashscope_task
from ..integrations.upstream.errors import UpstreamError
from ..integrations.upstream.runninghub import poll_runninghub_task
from ..integrations.upstream.trace_context import note_upstream
from ..integrations.upstream.vidu import poll_vidu_task
from ..models.job import GenerationJob
from .job_trace import provider_for_model


def upstream_task_id_from_job(job: GenerationJob) -> str | None:
    """取出 job 已记录的上游任务 ID；有值即表示曾经 submit 过，后续只能 poll。"""
    # 按可靠性优先级依次回退：provider_task_id 最权威，其余为历史/兼容字段
    for raw in (
        job.provider_task_id,
        job.upstream_job_id,
        job.provider_job_id,
        job.processing_id,
    ):
        val = str(raw or "").strip()
        if val:
            return val
    return None


def clear_upstream_task_ids(job: GenerationJob) -> None:
    """清空全部上游任务标记。

    仅供管理员“显式重新生成”使用：清空后下一次 Worker 执行才会重新向上游 submit。
    自动恢复（stale/启动恢复/普通重试）绝不能调用本函数，否则会二次提交。
    """
    job.provider_task_id = None
    job.upstream_job_id = None
    job.provider_job_id = None
    job.processing_id = None


def _resolve_provider(job: GenerationJob, model_id: str) -> str:
    """确定供应商标识：优先用 job.provider，缺失时按模型注册表回退。"""
    return (job.provider or provider_for_model(model_id) or "").strip().lower()


async def poll_upstream_result_url(
    job: GenerationJob,
    *,
    model_id: str,
    use_ltx_runninghub: bool = False,
) -> str:
    """轮询已存在的上游任务直至完成并返回产物 URL；本函数永远不会新建 task。"""
    task_id = upstream_task_id_from_job(job)
    if not task_id:
        raise UpstreamError("任务缺少上游 task_id，无法续轮询", code="MISSING_TASK_ID")

    provider = _resolve_provider(job, model_id)
    # 记录 poll_resume 事件（区别于 submit），便于 trace 中核对“只 submit 一次”
    note_upstream(
        provider=provider or job.provider,
        provider_task_id=task_id,
        event="poll_resume",
        detail={"modelId": model_id},
    )

    # 按供应商分发到对应的“纯轮询”函数（不含 POST 提交）
    if provider in ("dashscope", "aliyun", "wan"):
        return await poll_dashscope_task(task_id, creds=dashscope_creds())
    if provider == "kling":
        # 可灵复用百炼 video-synthesis 查询接口，但用可灵密钥
        return await poll_dashscope_task(task_id, creds=kling_creds())
    if provider == "ark":
        return await poll_seedance_task(task_id)
    if provider == "huahu":
        return await poll_huahu_seedance_task(task_id)
    if provider == "jumengai":
        return await poll_jumengai_video_task(task_id)
    if provider == "vidu":
        return await poll_vidu_task(task_id)
    if provider in ("runninghub", "rh", "ltx_runninghub"):
        return await poll_runninghub_task(task_id, use_ltx=use_ltx_runninghub)

    raise UpstreamError(
        f"暂不支持续轮询供应商: {provider or 'unknown'}",
        code="UNSUPPORTED_RESUME",
    )


__all__ = [
    "clear_upstream_task_ids",
    "poll_upstream_result_url",
    "upstream_task_id_from_job",
]
