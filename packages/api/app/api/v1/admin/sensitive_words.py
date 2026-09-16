"""管理端 — 敏感词 CRUD（写 MySQL 后覆盖 Redis 单 key）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_SENSITIVE_WORDS
from ....core.entity_ids import parse_entity_id
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services import sensitive_words as sw

router = APIRouter()


class SensitiveWordOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    word: str
    word_norm: str = Field(..., alias="wordNorm")
    enabled: bool
    created_at: str = Field(..., alias="createdAt")
    updated_at: str = Field(..., alias="updatedAt")


class SensitiveWordListOut(BaseModel):
    items: list[SensitiveWordOut]
    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")


class SensitiveWordCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    word: str = Field(..., min_length=1, max_length=128)
    enabled: bool = True


class SensitiveWordUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    word: str | None = Field(None, min_length=1, max_length=128)
    enabled: bool | None = None


class SensitiveWordImportIn(BaseModel):
    """批量导入：每行一个词，或 JSON 数组。"""

    model_config = ConfigDict(populate_by_name=True)

    words: list[str] = Field(default_factory=list)
    text: str | None = None


class SensitiveWordImportOut(BaseModel):
    added: int
    skipped: int


class SensitiveWordTestIn(BaseModel):
    text: str = Field(..., max_length=20000)


class SensitiveWordTestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    matched_words: list[str] = Field(..., alias="matchedWords")


def _row_out(row) -> SensitiveWordOut:
    return SensitiveWordOut(
        id=str(row.id),
        word=row.word,
        wordNorm=row.word_norm,
        enabled=bool(row.enabled),
        createdAt=row.created_at.isoformat() if row.created_at else "",
        updatedAt=row.updated_at.isoformat() if row.updated_at else "",
    )


@router.get("/sensitive-words", response_model=SensitiveWordListOut)
async def list_admin_sensitive_words(
    q: str | None = Query(None),
    enabled: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """分页列出敏感词（权威来自 MySQL）。"""
    items, total = await sw.list_sensitive_words(
        db, q=q, enabled=enabled, page=page, page_size=page_size
    )
    return SensitiveWordListOut(
        items=[_row_out(r) for r in items],
        total=total,
        page=page,
        pageSize=page_size,
    )


@router.post("/sensitive-words", response_model=SensitiveWordOut)
async def create_admin_sensitive_word(
    body: SensitiveWordCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """新增敏感词并刷新 Redis ``cache:sensitive_words:active``。"""
    row = await sw.create_sensitive_word(
        db,
        word=body.word,
        enabled=body.enabled,
        created_by=int(current_user.id) if current_user.id is not None else None,
    )
    return _row_out(row)


@router.put("/sensitive-words/{word_id}", response_model=SensitiveWordOut)
async def update_admin_sensitive_word(
    word_id: str,
    body: SensitiveWordUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """更新或启停敏感词，并刷新 Redis 单 key。"""
    eid = parse_entity_id(word_id)
    if eid is None:
        fail(ErrorCode.INVALID_ID)
    if body.word is None and body.enabled is None:
        fail(ErrorCode.VALIDATION_ERROR, message="请至少提供 word 或 enabled")
    row = await sw.update_sensitive_word(
        db, eid, word=body.word, enabled=body.enabled
    )
    return _row_out(row)


@router.delete("/sensitive-words/{word_id}")
async def delete_admin_sensitive_word(
    word_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """删除敏感词并刷新 Redis 单 key。"""
    eid = parse_entity_id(word_id)
    if eid is None:
        fail(ErrorCode.INVALID_ID)
    await sw.delete_sensitive_word(db, eid)
    return {"ok": True}


@router.post("/sensitive-words/import", response_model=SensitiveWordImportOut)
async def import_admin_sensitive_words(
    body: SensitiveWordImportIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """批量导入；``text`` 按换行切分，并与 ``words`` 合并。"""
    words = list(body.words or [])
    if body.text:
        words.extend(line.strip() for line in body.text.splitlines() if line.strip())
    result = await sw.import_sensitive_words(
        db,
        words,
        created_by=int(current_user.id) if current_user.id is not None else None,
    )
    return SensitiveWordImportOut(**result)


@router.post("/sensitive-words/test", response_model=SensitiveWordTestOut)
async def test_admin_sensitive_text(
    body: SensitiveWordTestIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SENSITIVE_WORDS)),
):
    """试检一段文案是否命中当前启用词表（读 Redis 优先）。"""
    result = await sw.test_sensitive_text(db, body.text)
    return SensitiveWordTestOut(ok=result["ok"], matchedWords=result["matchedWords"])
