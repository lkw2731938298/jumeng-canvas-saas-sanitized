"""运行时模型目录与密钥快照的统一刷新（API / Worker 跨进程对齐 Redis）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_runtime_catalog_fresh(db: AsyncSession) -> None:
    """credential / model 域版本变化时，从 Redis 或 DB 刷新本进程 L1 快照。"""
    from .credential_service import refresh_runtime_if_credential_version_changed
    from .model_catalog_runtime import refresh_runtime_model_specs_if_version_changed

    await refresh_runtime_if_credential_version_changed(db)
    await refresh_runtime_model_specs_if_version_changed(db)
