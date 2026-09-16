"""项目数据清理：用户端软删除与运维物理清除。"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..integrations.oss.canvas_storage import get_canvas_storage
from ..models.asset import ProjectAsset
from ..models.job import GenerationJob
from ..models.project import Project, Workflow
from ..models.project_member import ProjectMember
from ..services.asset_store import delete_oss_object

logger = logging.getLogger(__name__)


async def hide_project(db: AsyncSession, project: Project) -> None:
    """软删除项目：标记 isdel（保留 DB 与 OSS 数据），并从用户云存储占用中扣除该项目素材大小。

    隐藏后按 owner 重算 storage_used_bytes（sum 已排除隐藏项目），使用户可用云存储相应释放。
    """
    from .storage_quota import sync_user_storage_used_bytes

    project.isdel = True
    project.updated_at = now_cst_naive()
    await db.flush()
    await sync_user_storage_used_bytes(db, int(project.owner_id))


async def restore_project(db: AsyncSession, project: Project) -> None:
    """从回收站恢复：清除 isdel，并重算用户云存储占用。"""
    from .storage_quota import sync_user_storage_used_bytes

    project.isdel = False
    project.updated_at = now_cst_naive()
    await db.flush()
    await sync_user_storage_used_bytes(db, int(project.owner_id))


async def purge_project(db: AsyncSession, project: Project) -> None:
    """Delete workflows, jobs, assets, storage files, then the project row."""
    project_id = str(project.id)
    pid = project.id

    asset_rows = await db.execute(select(ProjectAsset).where(ProjectAsset.project_id == pid))
    for row in asset_rows.scalars().all():
        if row.oss_key:
            delete_oss_object(str(row.oss_key))
        await db.delete(row)

    await db.execute(delete(GenerationJob).where(GenerationJob.project_id == pid))
    await db.execute(delete(Workflow).where(Workflow.project_id == pid))
    await db.execute(delete(ProjectMember).where(ProjectMember.project_id == pid))

    storage = get_canvas_storage()
    await asyncio.to_thread(
        storage.delete_project_tree,
        project_id,
        project.storage_folder,
    )

    await db.delete(project)
    await db.flush()
