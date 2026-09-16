"""用户云存储配额 —— 按项目 owner 汇总 OSS 素材占用（GiB），会员额外空间不叠加套餐。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import cst_iso
from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.asset import ProjectAsset
from ..models.project import Project
from ..models.user import User
from .platform_settings import _ensure_settings_row
from .subscriptions import get_user_active_subscription

# 1 GiB = 1024^3 字节
GIB_BYTES = 1024**3
DEFAULT_GENERAL_STORAGE_GB = 2


@dataclass(frozen=True, slots=True)
class StorageQuotaSnapshot:
    """用户存储配额快照（展示与校验共用）。"""

    used_bytes: int
    quota_bytes: int
    general_gb: int
    member_gb: int
    is_member_active: bool
    member_expires_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "usedBytes": self.used_bytes,
            "quotaBytes": self.quota_bytes,
            "generalGb": self.general_gb,
            "memberGb": self.member_gb,
            "isMemberActive": self.is_member_active,
            "memberExpiresAt": self.member_expires_at,
            "usedGb": round(self.used_bytes / GIB_BYTES, 2),
            "quotaGb": round(self.quota_bytes / GIB_BYTES, 2),
        }


def gb_to_bytes(gb: int) -> int:
    """GiB 转字节（非负）。"""
    safe = max(int(gb or 0), 0)
    return safe * GIB_BYTES


async def get_default_storage_gb(db: AsyncSession) -> int:
    """读取平台通用存储配额（GiB）。"""
    row = await _ensure_settings_row(db)
    return max(int(row.default_storage_gb or DEFAULT_GENERAL_STORAGE_GB), 0)


async def set_default_storage_gb(db: AsyncSession, gb: int) -> int:
    """设置平台通用存储配额（GiB）。"""
    value = int(gb)
    if value < 0 or value > 10_000:
        fail(ErrorCode.INVALID_STORAGE_QUOTA, message="通用存储配额须在 0～10000 GiB 之间")
    row = await _ensure_settings_row(db)
    row.default_storage_gb = value
    await db.flush()
    return value


async def sum_owner_storage_used_bytes(db: AsyncSession, owner_id: int) -> int:
    """按 project_assets.file_size 汇总用户名下未隐藏项目的 OSS 素材占用。

    已软删除（隐藏，isdel=True）的项目不计入用户云存储占用，使删除项目后用户可用空间相应释放。
    """
    result = await db.execute(
        select(func.coalesce(func.sum(ProjectAsset.file_size), 0))
        .select_from(ProjectAsset)
        .join(Project, Project.id == ProjectAsset.project_id)
        .where(Project.owner_id == owner_id, Project.isdel.is_(False))
    )
    return int(result.scalar_one() or 0)


async def sync_user_storage_used_bytes(db: AsyncSession, user_id: int) -> int:
    """重算并写回 users.storage_used_bytes 缓存。"""
    used = await sum_owner_storage_used_bytes(db, user_id)
    result = await db.execute(select(User).filter(User.id == user_id).with_for_update())
    user = result.scalar_one_or_none()
    if user:
        user.storage_used_bytes = used
        await db.flush()
    return used


async def get_project_owner_id(db: AsyncSession, project_id: str | int) -> int:
    """解析项目 owner，用于协作项目按主人计配额。"""
    pid = require_entity_id(str(project_id))
    result = await db.execute(select(Project.owner_id).where(Project.id == pid))
    owner_id = result.scalar_one_or_none()
    if owner_id is None:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    return int(owner_id)


async def get_user_storage_snapshot(db: AsyncSession, user_id: int) -> StorageQuotaSnapshot:
    """计算用户有效配额与已用空间。"""
    general_gb = await get_default_storage_gb(db)
    member_gb = 0
    is_member_active = False
    member_expires_at: str | None = None

    pair = await get_user_active_subscription(db, user_id)
    if pair:
        _sub, plan = pair
        member_gb = max(int(plan.storage_gb or 0), 0)
        is_member_active = True
        member_expires_at = cst_iso(_sub.current_period_end)

    quota_bytes = gb_to_bytes(general_gb + member_gb)

    result = await db.execute(select(User.storage_used_bytes).where(User.id == user_id))
    cached = result.scalar_one_or_none()
    used_bytes = int(cached or 0)

    return StorageQuotaSnapshot(
        used_bytes=used_bytes,
        quota_bytes=quota_bytes,
        general_gb=general_gb,
        member_gb=member_gb,
        is_member_active=is_member_active,
        member_expires_at=member_expires_at,
    )


async def assert_can_add_storage(
    db: AsyncSession,
    *,
    owner_id: int,
    delta_bytes: int,
) -> StorageQuotaSnapshot:
    """校验 owner 在增加 delta_bytes 后是否未超配额；不足则 402。"""
    delta = max(int(delta_bytes or 0), 0)
    if delta <= 0:
        return await get_user_storage_snapshot(db, owner_id)

    snapshot = await get_user_storage_snapshot(db, owner_id)
    if snapshot.used_bytes + delta > snapshot.quota_bytes:
        fail(
            ErrorCode.STORAGE_QUOTA_EXCEEDED,
            content={
                **snapshot.to_dict(),
                "deltaBytes": delta,
            },
        )
    return snapshot


async def assert_storage_quota_for_project(
    db: AsyncSession,
    project_id: str | int,
    delta_bytes: int,
) -> StorageQuotaSnapshot:
    """协作项目按 owner 校验存储配额。"""
    owner_id = await get_project_owner_id(db, project_id)
    return await assert_can_add_storage(db, owner_id=owner_id, delta_bytes=delta_bytes)


async def apply_storage_delta(db: AsyncSession, owner_id: int, delta_bytes: int) -> None:
    """素材增删后更新 owner 的 storage_used_bytes 缓存。"""
    delta = int(delta_bytes or 0)
    if delta == 0:
        return
    result = await db.execute(select(User).filter(User.id == owner_id).with_for_update())
    user = result.scalar_one_or_none()
    if not user:
        return
    next_used = max(int(user.storage_used_bytes or 0) + delta, 0)
    user.storage_used_bytes = next_used
    await db.flush()


async def apply_storage_delta_for_project(db: AsyncSession, project_id: str | int, delta_bytes: int) -> None:
    """按项目 owner 应用存储占用增量。"""
    owner_id = await get_project_owner_id(db, project_id)
    await apply_storage_delta(db, owner_id, delta_bytes)


async def backfill_all_users_storage_used_bytes(db: AsyncSession) -> int:
    """启动时对账：按 project_assets 重算全部用户 storage_used_bytes。"""
    users = await db.execute(select(User.id))
    updated = 0
    for (user_id,) in users.all():
        await sync_user_storage_used_bytes(db, int(user_id))
        updated += 1
    return updated
