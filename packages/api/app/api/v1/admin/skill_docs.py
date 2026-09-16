"""管理端 — Skill MD 文档覆盖编辑（白名单见 ALLOWED_SKILL_DOC_SLUGS）。

支持单文件与多文件技能包（目录树 + 按文件保存）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_HOMEPAGE
from ....core.deps import require_permission
from ....core.errors import ok
from ....models.database import get_db
from ....models.user import User
from ....services import skill_docs_admin

router = APIRouter()


class SkillDocPutBody(BaseModel):
    """保存 Markdown；技能包可指定 path（默认 SKILL.md）。"""

    document: str = Field(..., min_length=1)
    path: str | None = Field(default=None, description="包内相对路径，如 references/demand-checklist.md")


@router.get("/skill-docs")
async def list_admin_skill_docs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """列表：slug、title、source（override|file）、package/fileCount。"""
    items = await skill_docs_admin.list_skill_docs_for_admin(db)
    return ok({"items": items})


@router.get("/skill-docs/{slug}")
async def get_admin_skill_doc(
    slug: str,
    path: str | None = Query(default=None, description="技能包内文件路径"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """读取当前生效全文 + 解析摘要；技能包附 tree/files。"""
    return ok(await skill_docs_admin.get_skill_doc_for_admin(db, slug, path=path))


@router.put("/skill-docs/{slug}")
async def put_admin_skill_doc(
    slug: str,
    body: SkillDocPutBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """保存覆盖到 platform_settings.skill_docs（单文件或包内某文件）。"""
    if body.path:
        data = await skill_docs_admin.set_skill_doc_file_override(
            db, slug, path=body.path, document=body.document
        )
    else:
        data = await skill_docs_admin.set_skill_doc_override(db, slug, body.document)
    await db.commit()
    return ok(data)


@router.post("/skill-docs/{slug}/reset")
async def reset_admin_skill_doc(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """删除覆盖，恢复仓库 skill_docs 默认（单文件或完整技能包）。"""
    data = await skill_docs_admin.reset_skill_doc_override(db, slug)
    await db.commit()
    return ok(data)
