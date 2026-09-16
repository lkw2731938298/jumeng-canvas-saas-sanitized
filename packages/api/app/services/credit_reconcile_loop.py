"""Background loop that periodically reconciles dangling credit reservations."""

from __future__ import annotations

import asyncio
import logging

from ..models.database import async_session
from .credit_reconcile import reconcile_credit_reservations
from .credit_lots import expire_stale_lots
from .job_anomaly import run_job_anomaly_detection
from .payment_orders import expire_stale_pending_payment_orders
from .stale_jobs import recover_exhausted_running_jobs, recover_stale_running_jobs
from .subscriptions import renew_due_subscriptions

logger = logging.getLogger(__name__)

RECONCILE_INTERVAL_S = 300.0


async def run_credit_reconcile_loop(interval_s: float = RECONCILE_INTERVAL_S) -> None:
    while True:
        try:
            await asyncio.sleep(interval_s)
            async with async_session() as session:
                await expire_stale_lots(session)
                # 超时未付支付单：pending → expired，避免管理端仍显示「待支付」
                await expire_stale_pending_payment_orders(session)
                await renew_due_subscriptions(session)
                await recover_exhausted_running_jobs(session)
                await run_job_anomaly_detection(session)
                await recover_stale_running_jobs(session)
                await reconcile_credit_reservations(session)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Credit reconcile loop error: %s", exc)
