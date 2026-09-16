from fastapi import APIRouter, Depends

from ....core.admin_permissions import PERM_ADMIN_PERMISSIONS, admin_has_permission, resolved_permissions
from ....core.deps import require_admin
from ....core.entity_ids import format_user_display_id
from ....models.user import User
from ....schemas.admin import AdminUserOut

router = APIRouter()


@router.get("/me", response_model=AdminUserOut)
async def admin_me(current_user: User = Depends(require_admin)):
    """当前管理员身份与生效权限（前端侧栏过滤、页面守卫用）。"""
    perms = resolved_permissions(current_user)
    return AdminUserOut(
        id=str(current_user.id),
        user_no=format_user_display_id(current_user.id),
        source_user_id=current_user.source_user_id,
        phone=current_user.phone,
        display_name=current_user.display_name or "",
        avatar_url=current_user.avatar_url,
        role=current_user.role,
        is_super_admin=bool(getattr(current_user, "is_super_admin", False)),
        permissions=perms,
        can_manage_permissions=admin_has_permission(current_user, PERM_ADMIN_PERMISSIONS),
    )
