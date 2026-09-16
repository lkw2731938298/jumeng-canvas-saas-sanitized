"""画布生成提交共享逻辑 —— 去重、报价校验、预扣算力、创建任务。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.job import GenerationJob, Model
from ..models.user import User
from ..core.job_status import JobStatus
from .credit_flow import credits_enabled, get_user_credit_balance, reserve_for_job
from .credit_pricing import (
    CreditQuote,
    apply_material_usage_billing_option,
    apply_quote_to_job,
    apply_ref_video_billing_option,
    assert_pricing_version,
    quote_generation_cost,
    verify_quote_token,
)
from .credit_operation_lock import generation_submit_lock, seal_generation_submit
from .generation_call_logs import log_generation_submit
from .generation_jobs import create_node_generation_job
from .project_access import get_project_owner_user, resolve_project_membership
from .project_collaboration_spend import (
    assert_collaborator_spend_allowed,
    collaborator_needs_approval,
    is_collaborator_actor,
)


def build_dedupe_key(
    *,
    user_id: str,
    project_id: str,
    node_id: str,
    model: str,
    category: str,
    option_snapshot: dict[str, str],
    idempotency_key: str | None,
) -> str | None:
    """根据用户/项目/节点/模型/选项/幂等键生成去重哈希（无幂等键则不去重）。"""
    if not idempotency_key or not idempotency_key.strip():
        return None
    payload = {
        "userId": user_id,
        "projectId": project_id,
        "nodeId": node_id,
        "model": model,
        "category": category,
        "optionSnapshot": option_snapshot,
        "idempotencyKey": idempotency_key.strip(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest[:64]


async def find_existing_job_by_dedupe(db: AsyncSession, dedupe_key: str | None) -> GenerationJob | None:
    """按 dedupe_key 查找已存在的任务（命中则复用，不重复预扣）。"""
    if not dedupe_key:
        return None
    result = await db.execute(select(GenerationJob).filter(GenerationJob.dedupe_key == dedupe_key))
    return result.scalar_one_or_none()


async def resolve_generation_billing_user(
    db: AsyncSession,
    *,
    actor: User,
    project_id: str,
) -> User:
    """确定本次生成的计费用户：项目所有者本人，或协作者场景下的项目 owner。"""
    membership = await resolve_project_membership(db, actor, project_id)
    if not membership:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    if membership.project.owner_id == actor.id:
        return actor
    return await get_project_owner_user(db, membership.project)


async def submit_node_generation(
    db: AsyncSession,
    *,
    user: User,
    catalog_model: Model,
    project_id: str,
    node_id: str,
    category: str,
    model_name: str,
    workflow_id: str | None,
    input_params: dict[str, Any],
    generation_options: dict[str, str] | None,
    idempotency_key: str | None = None,
    quote_token: str | None = None,
    expected_pricing_version: int | None = None,
    billing_user: User | None = None,
    submit_source: str | None = None,
) -> tuple[GenerationJob, CreditQuote, bool]:
    """画布节点生成提交入口：加用户锁→报价校验→去重→预扣→入队，返回 (job, quote, is_duplicate)。"""
    actor = user
    bill_to = billing_user or await resolve_generation_billing_user(db, actor=actor, project_id=project_id)
    # API 入队入口锁（按计费用户）：串行化预扣算力 + 创建 job，与 Worker 侧 model_submit_lock 互补
    async with generation_submit_lock(bill_to.id):
        return await _submit_node_generation_locked(
            db,
            actor=actor,
            billing_user=bill_to,
            catalog_model=catalog_model,
            project_id=project_id,
            node_id=node_id,
            category=category,
            model_name=model_name,
            workflow_id=workflow_id,
            input_params=input_params,
            generation_options=generation_options,
            idempotency_key=idempotency_key,
            quote_token=quote_token,
            expected_pricing_version=expected_pricing_version,
            submit_source=submit_source,
        )


async def _submit_node_generation_locked(
    db: AsyncSession,
    *,
    actor: User,
    billing_user: User,
    catalog_model: Model,
    project_id: str,
    node_id: str,
    category: str,
    model_name: str,
    workflow_id: str | None,
    input_params: dict[str, Any],
    generation_options: dict[str, str] | None,
    idempotency_key: str | None = None,
    quote_token: str | None = None,
    expected_pricing_version: int | None = None,
    submit_source: str | None = None,
) -> tuple[GenerationJob, CreditQuote, bool]:
    """提交核心逻辑（持锁内执行）：敏感词校验→报价/余额/协作审批→创建 job→预扣算力。"""
    # 用户提示词敏感词拦截（词表 Redis 单 key；命中则不预扣、不入队）
    from .sensitive_words import assert_input_params_clean

    await assert_input_params_clean(db, input_params if isinstance(input_params, dict) else None)

    canvas_tool = None
    if isinstance(input_params, dict):
        raw_tool = input_params.get("canvasTool") or input_params.get("canvas_tool")
        if isinstance(raw_tool, str) and raw_tool.strip():
            canvas_tool = raw_tool.strip()

    # 多模态：按提交 references 自动切换有/无参考视频单价，并写入素材用量计费字段
    if not canvas_tool:
        generation_options = apply_ref_video_billing_option(
            catalog_model, generation_options, input_params
        )
        generation_options = apply_material_usage_billing_option(
            catalog_model, generation_options, input_params
        )

    if canvas_tool:
        from .canvas_tool_pricing import get_canvas_tool_pricing, quote_canvas_tool_cost

        tool_pricing = await get_canvas_tool_pricing(db)
        # 视频工具按时长计费：秒数来自 generationOptions 或 input_params
        quote = quote_canvas_tool_cost(
            catalog_model,
            canvas_tool,
            tool_pricing,
            generation_options=generation_options,
            input_params=input_params if isinstance(input_params, dict) else None,
        )
    else:
        quote = quote_generation_cost(catalog_model, generation_options, strict=True)
    if quote_token:
        verify_quote_token(quote_token, quote)
    else:
        assert_pricing_version(expected_pricing_version, quote)

    # 价格为 0 视为「未设置价格」：不再当作免费直接生成，而是拦截提醒管理员先配置价格。
    # 仅在算力系统启用时拦截；全局关闭算力（演示免扣费）时不拦截。
    if credits_enabled() and quote.total <= 0:
        fail(ErrorCode.PRICE_NOT_CONFIGURED)

    dedupe_key = build_dedupe_key(
        user_id=str(actor.id),
        project_id=project_id,
        node_id=node_id,
        model=model_name,
        category=category,
        option_snapshot=quote.option_snapshot,
        idempotency_key=idempotency_key,
    )

    existing = await find_existing_job_by_dedupe(db, dedupe_key)
    if existing:
        if canvas_tool:
            from .canvas_tool_pricing import get_canvas_tool_pricing, quote_canvas_tool_cost

            tool_pricing = await get_canvas_tool_pricing(db)
            existing_quote = quote_canvas_tool_cost(
                catalog_model,
                canvas_tool,
                tool_pricing,
                generation_options=generation_options,
                input_params=input_params if isinstance(input_params, dict) else None,
            )
        else:
            existing_quote = quote_generation_cost(catalog_model, generation_options, strict=True)
        await log_generation_submit(
            db,
            job=existing,
            actor_user_id=actor.id,
            billing_user_id=billing_user.id,
            category=category,
            model=model_name,
            project_id=project_id,
            node_id=node_id,
            workflow_id=workflow_id,
            request_payload=input_params,
            outcome="success",
            is_duplicate=True,
            submit_source=submit_source,
            idempotency_key=idempotency_key,
            dedupe_key=dedupe_key,
        )
        return existing, existing_quote, True

    if credits_enabled() and quote.total > 0:
        balance = await get_user_credit_balance(billing_user, db, model_name=model_name)
        if balance is not None and balance < quote.total:
            fail(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=f"算力不足，需要 {quote.total}，当前可用 {balance}",
                content={"required": quote.total, "available": balance},
            )

    membership = await resolve_project_membership(db, actor, project_id)
    project = membership.project if membership else None
    if project and is_collaborator_actor(actor, billing_user):
        await assert_collaborator_spend_allowed(
            db,
            project=project,
            actor=actor,
            billing_user=billing_user,
            amount=quote.total,
        )

    needs_approval = (
        project is not None
        and collaborator_needs_approval(
            project,
            actor=actor,
            billing_user=billing_user,
            amount=quote.total,
        )
    )

    job = await create_node_generation_job(
        db,
        user=billing_user,
        project_id=project_id,
        node_id=node_id,
        category=category,
        model=model_name,
        workflow_id=workflow_id,
        actor_user=actor,
        input_params={
            **input_params,
            "optionSnapshot": quote.option_snapshot,
            "pricingVersion": quote.pricing_version,
            "actorUserId": str(actor.id),
        },
    )
    if dedupe_key:
        job.dedupe_key = dedupe_key

    apply_quote_to_job(job, quote)

    if needs_approval:
        job.status = JobStatus.AWAITING_APPROVAL.value
        job.credit_status = "approval_pending"
        await db.flush()
        await log_generation_submit(
            db,
            job=job,
            actor_user_id=actor.id,
            billing_user_id=billing_user.id,
            category=category,
            model=model_name,
            project_id=project_id,
            node_id=node_id,
            workflow_id=workflow_id,
            request_payload=input_params,
            outcome="success",
            is_duplicate=False,
            submit_source=submit_source,
            idempotency_key=idempotency_key,
            dedupe_key=dedupe_key,
        )
        return job, quote, False

    await reserve_for_job(
        db,
        user=billing_user,
        job=job,
        quote=quote,
        reference=f"canvas:{project_id}:{node_id}",
    )
    await seal_generation_submit(billing_user.id)
    await db.flush()
    await log_generation_submit(
        db,
        job=job,
        actor_user_id=actor.id,
        billing_user_id=billing_user.id,
        category=category,
        model=model_name,
        project_id=project_id,
        node_id=node_id,
        workflow_id=workflow_id,
        request_payload=input_params,
        outcome="success",
        is_duplicate=False,
        submit_source=submit_source,
        idempotency_key=idempotency_key,
        dedupe_key=dedupe_key,
    )
    return job, quote, False
