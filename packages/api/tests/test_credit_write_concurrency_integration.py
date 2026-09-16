"""本地 MySQL + Redis 并发集成测试（§8.11 五条写链路 + 多退防护）。

运行前需启动本地依赖：
  cd canvas && docker compose up -d mysql redis

执行：
  cd canvas/packages/api
  python -m pytest tests/test_credit_write_concurrency_integration.py -v -s

或仅跑脚本：
  python tests/test_credit_write_concurrency_integration.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select

# 强制本地 Redis / MySQL（与 docker-compose 默认端口一致）
os.environ.setdefault("DATABASE_URL", "mysql+aiomysql://jumeng_canvas:change-me@127.0.0.1:3306/jumeng_canvas")
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("CREDITS_ENABLED", "true")
os.environ.setdefault("JUMENG_CANVAS_USE_DOTENV", "0")

from app.core.credit_status import CreditStatus
from app.core.credit_types import CREDIT_TYPE_GENERAL
from app.core.datetime_util import now_cst_naive
from app.core.errors import AppError
from app.core.job_status import JobStatus
from app.models.credit import CreditAdminAdjustOrder, CreditRechargeOrder, CreditReservation
from app.models.credit_transaction import CreditTransaction
from app.models.database import async_session, engine
from app.models.job import GenerationJob
from app.models.subscription import SubscriptionGrant, SubscriptionPlan, UserSubscription
from app.models.user import User
from app.services.credit_admin_adjust import admin_adjust_user_credits
from app.services.credit_flow import refund_job_credits, reserve_for_job
from app.services.credit_lots import grant_credit_lot, sum_user_balance, sync_user_compute_power
from app.services.credit_operation_lock import (
    claim_credit_recharge,
    claim_credit_refund,
    credit_refund_lock_key,
    seal_credit_recharge,
)
from app.services.credit_pricing import CreditQuote
from app.services.credit_recharge import recharge_user_credits
from app.services.db_bootstrap import ensure_mysql_schema
from app.services.local_credits import reserve_local_credits
from app.services.redis_client import close_redis, get_redis
from app.services.subscriptions import SUB_STATUS_ACTIVE, _grant_period_credits


RUN_TAG = uuid.uuid4().hex[:8]
CONCURRENCY = 20


async def _require_infra() -> None:
    """确认 MySQL / Redis 可用，否则跳过集成测试。"""
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        pytest.skip(f"MySQL 不可用: {exc}")
    redis = await get_redis()
    if redis is None:
        pytest.skip("Redis 不可用（请 docker compose up -d redis）")


async def _reset_redis_keys(patterns: list[str]) -> None:
    redis = await get_redis()
    if redis is None:
        return
    for pattern in patterns:
        keys = [k async for k in redis.scan_iter(match=pattern)]
        if keys:
            await redis.delete(*keys)


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.run_until_complete(close_redis())
    loop.close()


@pytest.fixture(scope="module", autouse=True)
async def bootstrap_schema():
    await _require_infra()
    await ensure_mysql_schema()
    yield
    await close_redis()


async def _create_user(db, *, suffix: str, initial_credits: int = 0) -> User:
    now = now_cst_naive()
    user = User(
        source_user_id=f"concurrency-test-{RUN_TAG}-{suffix}",
        phone=f"199{RUN_TAG[:6]}{suffix[-4:].zfill(4)}"[:11],
        display_name=f"并发测试{suffix}",
        compute_power=0,
        created_at=now,
        last_login_at=now,
    )
    db.add(user)
    await db.flush()
    if initial_credits > 0:
        await grant_credit_lot(
            db,
            user_id=user.id,
            credit_type=CREDIT_TYPE_GENERAL,
            amount=initial_credits,
            source="test_seed",
            source_ref=f"seed-{suffix}",
        )
        await sync_user_compute_power(db, user)
    return user


async def _create_job(db, user: User, *, cost: int = 50) -> GenerationJob:
    from app.services.job_ids import allocate_generation_job_id

    job_id = await allocate_generation_job_id(db)
    job = GenerationJob(
        id=job_id,
        user_id=user.id,
        status=JobStatus.PENDING.value,
        lane="image",
        job_type="image_gen",
        model="test-model",
        credit_cost=cost,
        credit_status=CreditStatus.SKIPPED.value,
        input_params={"projectId": "1", "nodeId": "n1"},
        created_at=now_cst_naive(),
    )
    db.add(job)
    await db.flush()
    return job


async def _count_rows(db, model, **filters) -> int:
    q = select(func.count()).select_from(model)
    for k, v in filters.items():
        q = q.where(getattr(model, k) == v)
    return int((await db.execute(q)).scalar_one())


@pytest.mark.asyncio
async def test_concurrent_recharge_same_idempotency_key():
    """并发充值同一幂等键：只应入账一次。"""
    idem = f"recharge-idem-{RUN_TAG}"
    amount = 100
    await _reset_redis_keys([f"lock:credit:recharge:*"])

    async with async_session() as db:
        user = await _create_user(db, suffix="recharge")
        await db.commit()
        user_id = user.id

    async def one_recharge(i: int) -> dict[str, Any]:
        async with async_session() as db:
            u = (await db.execute(select(User).filter(User.id == user_id))).scalar_one()
            if not await claim_credit_recharge(user_id):
                return {"i": i, "ok": False, "reason": "claim_failed"}
            try:
                bal = await recharge_user_credits(db, user=u, amount=amount, idempotency_key=idem)
                await db.commit()
                return {"i": i, "ok": True, "balance": bal}
            except AppError as e:
                await db.rollback()
                return {"i": i, "ok": False, "code": e.code}
            except Exception as e:
                await db.rollback()
                return {"i": i, "ok": False, "error": str(e)}

    results = await asyncio.gather(*[one_recharge(i) for i in range(CONCURRENCY)])

    async with async_session() as db:
        orders = await _count_rows(db, CreditRechargeOrder, user_id=user_id, idempotency_key=idem)
        balance = await sum_user_balance(db, user_id)
        completed = (
            await db.execute(
                select(CreditRechargeOrder).filter(
                    CreditRechargeOrder.user_id == user_id,
                    CreditRechargeOrder.idempotency_key == idem,
                )
            )
        ).scalar_one_or_none()

    success = [r for r in results if r.get("ok")]
    print(f"[recharge] success={len(success)}/{CONCURRENCY} orders={orders} balance={balance}")

    assert orders == 1
    assert completed is not None and completed.status == "completed"
    assert balance == amount, f"期望余额 {amount}，实际 {balance}（多充？）"


@pytest.mark.asyncio
async def test_concurrent_admin_adjust_same_idempotency_key():
    """并发管理加款同一幂等键：只应加一次。"""
    idem = f"adjust-idem-{RUN_TAG}"
    delta = 80
    await _reset_redis_keys([f"lock:credit:adjust:*"])

    async with async_session() as db:
        user = await _create_user(db, suffix="adjust", initial_credits=0)
        await db.commit()
        user_id = user.id

    async def one_adjust(i: int) -> dict[str, Any]:
        async with async_session() as db:
            u = (await db.execute(select(User).filter(User.id == user_id))).scalar_one()
            try:
                bal = await admin_adjust_user_credits(
                    db,
                    user=u,
                    delta=delta,
                    reason="并发测试加款",
                    operator_id=1,
                    credit_type=CREDIT_TYPE_GENERAL,
                    idempotency_key=idem,
                )
                await db.commit()
                return {"i": i, "ok": True, "balance": bal}
            except AppError as e:
                await db.rollback()
                return {"i": i, "ok": False, "code": e.code}
            except Exception as e:
                await db.rollback()
                return {"i": i, "ok": False, "error": str(e)}

    results = await asyncio.gather(*[one_adjust(i) for i in range(CONCURRENCY)])

    async with async_session() as db:
        orders = await _count_rows(db, CreditAdminAdjustOrder, user_id=user_id, idempotency_key=idem)
        balance = await sum_user_balance(db, user_id)

    success = [r for r in results if r.get("ok")]
    print(f"[adjust] success={len(success)}/{CONCURRENCY} orders={orders} balance={balance}")

    assert orders == 1
    assert balance == delta


@pytest.mark.asyncio
async def test_concurrent_reserve_same_idempotency_key():
    """并发预扣同一幂等键：只应扣一次款。"""
    idem = f"canvas-job-reserve-{RUN_TAG}"
    amount = 30
    await _reset_redis_keys([f"lock:generation:*"])

    async with async_session() as db:
        user = await _create_user(db, suffix="reserve", initial_credits=500)
        job = await _create_job(db, user, cost=amount)
        job_id = job.id
        await db.commit()
        user_id = user.id

    async def one_reserve(i: int) -> dict[str, Any]:
        async with async_session() as db:
            try:
                result = await reserve_local_credits(
                    db,
                    user_id=user_id,
                    amount=amount,
                    idempotency_key=idem,
                    reference="test",
                    job_id=job_id,
                )
                await db.commit()
                return {"i": i, "ok": True, "created": result.get("created") if result else False}
            except Exception as e:
                await db.rollback()
                return {"i": i, "ok": False, "error": str(e)}

    results = await asyncio.gather(*[one_reserve(i) for i in range(CONCURRENCY)])

    async with async_session() as db:
        reservations = await _count_rows(db, CreditReservation, idempotency_key=idem)
        balance = await sum_user_balance(db, user_id)

    created_count = sum(1 for r in results if r.get("created"))
    print(f"[reserve] created={created_count} reservations={reservations} balance={balance}")

    assert reservations == 1
    assert balance == 500 - amount, f"多扣？balance={balance}"


@pytest.mark.asyncio
async def test_concurrent_refund_cannot_over_refund():
    """并发退款同一 job：不能多退算力（核心用例）。"""
    cost = 40
    initial = 200
    await _reset_redis_keys([f"lock:credit:refund:*"])

    async with async_session() as db:
        user = await _create_user(db, suffix="refund", initial_credits=initial)
        job = await _create_job(db, user, cost=cost)
        quote = CreditQuote(
            model="test-model",
            total=cost,
            base=cost,
            breakdown=[],
            pricing_version=1,
            option_snapshot={},
        )
        await reserve_for_job(db, user=user, job=job, quote=quote, reference="test-refund")
        await db.commit()
        job_id = job.id
        user_id = user.id
        reservation_id = job.credit_reservation_id

    async with async_session() as db:
        balance_after_reserve = await sum_user_balance(db, user_id)
    assert balance_after_reserve == initial - cost

    async def one_refund(i: int) -> dict[str, Any]:
        async with async_session() as db:
            j = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one()
            ok = await refund_job_credits(j, db, force=True)
            await db.commit()
            return {"i": i, "ok": ok, "credit_status": j.credit_status}

    results = await asyncio.gather(*[one_refund(i) for i in range(CONCURRENCY)])

    async with async_session() as db:
        balance_final = await sum_user_balance(db, user_id)
        refund_tx_count = (
            await db.execute(
                select(func.count())
                .select_from(CreditTransaction)
                .filter(
                    CreditTransaction.job_id == job_id,
                    CreditTransaction.reference_key == f"job-refund-{job_id}",
                )
            )
        ).scalar_one()
        reservation = (
            await db.execute(select(CreditReservation).filter(CreditReservation.id == int(reservation_id)))
        ).scalar_one_or_none()

    ok_count = sum(1 for r in results if r.get("ok"))
    print(
        f"[refund] ok_returns={ok_count}/{CONCURRENCY} "
        f"refund_tx={refund_tx_count} balance={balance_final} reservation={reservation.status if reservation else None}"
    )

    # 余额必须回到初始值，不能因多退而超过 initial
    assert balance_final == initial, f"多退！期望 {initial} 实际 {balance_final}"
    assert refund_tx_count == 1, f"退流水重复写入 {refund_tx_count} 条"
    assert reservation is not None and reservation.status == "released"

    redis = await get_redis()
    if redis:
        assert await redis.exists(credit_refund_lock_key(job_id))


@pytest.mark.asyncio
async def test_requeue_reserve_and_refund_reference_keys_distinct():
    """重队列预扣 + 退款：扣退流水 reference_key 含 rq 且不冲突。"""
    cost = 25
    initial = 300
    await _reset_redis_keys([f"lock:credit:refund:*", f"lock:generation:*"])

    async with async_session() as db:
        user = await _create_user(db, suffix="requeue", initial_credits=initial)
        job = await _create_job(db, user, cost=cost)
        quote = CreditQuote(
            model="test-model",
            total=cost,
            base=cost,
            breakdown=[],
            pricing_version=1,
            option_snapshot={},
        )
        await reserve_for_job(db, user=user, job=job, quote=quote, reference="rq-test")
        await refund_job_credits(job, db, force=True)
        await db.commit()
        job_id = job.id
        user_id = user.id

    # 模拟管理员重队列第二次预扣
    idem_rq1 = f"canvas-job-{job_id}-rq1"
    async with async_session() as db:
        result = await reserve_local_credits(
            db,
            user_id=user_id,
            amount=cost,
            idempotency_key=idem_rq1,
            reference="requeue-1",
            job_id=job_id,
        )
        assert result and result.get("created")
        j = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one()
        j.credit_reservation_id = str(result["reservation_id"])
        j.credit_status = CreditStatus.RESERVED.value
        from app.services.credit_transactions import record_job_consume_transaction

        bal = await sum_user_balance(db, user_id)
        await record_job_consume_transaction(
            db, user=user, job=j, balance_after=bal, idempotency_key=idem_rq1
        )
        await db.commit()

    async with async_session() as db:
        j = (await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id))).scalar_one()
        await refund_job_credits(j, db, force=True)
        await db.commit()

    async with async_session() as db:
        txs = (
            await db.execute(
                select(CreditTransaction.reference_key, CreditTransaction.delta).filter(
                    CreditTransaction.job_id == job_id
                )
            )
        ).all()
        balance = await sum_user_balance(db, user_id)
        ref_keys = {r for r, _ in txs}

    print(f"[requeue] ref_keys={sorted(ref_keys)} balance={balance}")
    assert f"job-consume-{job_id}" in ref_keys
    assert f"job-refund-{job_id}" in ref_keys
    assert f"job-consume-{job_id}-rq1" in ref_keys
    assert f"job-refund-{job_id}-rq1" in ref_keys
    assert balance == initial


@pytest.mark.asyncio
async def test_subscription_grant_idempotent_per_period():
    """同账期并发发放订阅算力：只应入账一次。"""
    await _reset_redis_keys([f"lock:subscription:*"])
    period_key = f"2026-07-08-{RUN_TAG}"

    async with async_session() as db:
        user = await _create_user(db, suffix="sub")
        plan = SubscriptionPlan(
            code=f"test-plan-{RUN_TAG}",
            name="测试套餐",
            monthly_credits=60,
            period_days=30,
            price_cents=0,
            is_active=True,
            created_at=now_cst_naive(),
            updated_at=now_cst_naive(),
        )
        db.add(plan)
        await db.flush()
        now = now_cst_naive()
        sub = UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SUB_STATUS_ACTIVE,
            auto_renew=False,
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            created_at=now,
            updated_at=now,
        )
        db.add(sub)
        await db.commit()
        user_id = user.id
        sub_id = sub.id
        plan_id = plan.id

    async def one_grant(i: int) -> bool:
        async with async_session() as db:
            u = (await db.execute(select(User).filter(User.id == user_id))).scalar_one()
            s = (await db.execute(select(UserSubscription).filter(UserSubscription.id == sub_id))).scalar_one()
            p = (await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id))).scalar_one()
            # 固定 period_key 测 UNIQUE(user_id, period_key)
            from app.services.subscriptions import _period_key

            ps = now_cst_naive()
            granted = await _grant_period_credits(
                db,
                user=u,
                subscription=s,
                plan=p,
                period_start=ps,
                period_end=ps + timedelta(days=30),
            )
            await db.commit()
            return granted

    results = await asyncio.gather(*[one_grant(i) for i in range(CONCURRENCY)])

    async with async_session() as db:
        grants = await _count_rows(
            db,
            SubscriptionGrant,
            user_id=user_id,
            period_key=_period_key(now_cst_naive()),
        )
        balance = await sum_user_balance(db, user_id)

    granted_true = sum(1 for g in results if g)
    print(f"[subscription] granted_true={granted_true}/{CONCURRENCY} grant_rows={grants} balance={balance}")
    assert grants == 1
    assert balance == 60


async def _run_all() -> None:
    """命令行直接跑全部用例并打印汇总。"""
    await _require_infra()
    await ensure_mysql_schema()
    tests = [
        test_concurrent_recharge_same_idempotency_key,
        test_concurrent_admin_adjust_same_idempotency_key,
        test_concurrent_reserve_same_idempotency_key,
        test_concurrent_refund_cannot_over_refund,
        test_requeue_reserve_and_refund_reference_keys_distinct,
        test_subscription_grant_idempotent_per_period,
    ]
    passed = 0
    for fn in tests:
        name = fn.__name__
        try:
            await fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
    print(f"\n=== {passed}/{len(tests)} passed ===")
    await close_redis()


if __name__ == "__main__":
    asyncio.run(_run_all())
