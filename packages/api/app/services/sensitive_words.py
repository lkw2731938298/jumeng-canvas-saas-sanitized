"""敏感词服务：规范化匹配、管理 CRUD、生成前提交前强拦。"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.sensitive_word import SensitiveWord
from .sensitive_words_cache import get_active_word_norms, publish_active_sensitive_words_cache

# 去空白、零宽字符
_ZW_SPACE_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\s]+")
# 常见插空规避用标点
_PUNCT_RE = re.compile(
    r"[·•.\-_\\|/[\]()（）【】「」『』《》<>\"'`~,，。!！?？:：;；@#*]+"
)

# 从 input_params 抽取待检文本的字段名
_PROMPT_TEXT_KEYS = ("prompt", "content")


def normalize_for_match(text: str) -> str:
    """提示词/词条规范化：NFKC → casefold → 去空白零宽与常见标点。"""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.casefold()
    t = _ZW_SPACE_RE.sub("", t)
    t = _PUNCT_RE.sub("", t)
    return t


def find_matched_words(text: str, word_norms: list[str]) -> list[str]:
    """在规范化后的文本中做子串匹配，返回命中的 word_norm 列表（去重保序）。"""
    hay = normalize_for_match(text)
    if not hay or not word_norms:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for w in word_norms:
        if not w or w in seen:
            continue
        if w in hay:
            hits.append(w)
            seen.add(w)
    return hits


def collect_prompt_texts(input_params: dict[str, Any] | None, *extra: str) -> list[str]:
    """收集生成入参与附加字符串中的用户提示词。"""
    texts: list[str] = []
    for s in extra:
        if isinstance(s, str) and s.strip():
            texts.append(s)
    if isinstance(input_params, dict):
        for key in _PROMPT_TEXT_KEYS:
            v = input_params.get(key)
            if isinstance(v, str) and v.strip():
                texts.append(v)
    return texts


async def assert_texts_clean(db: AsyncSession, *texts: str) -> None:
    """任一文案命中敏感词则 fail；词表优先读 Redis 单 key。"""
    nonempty = [t for t in texts if isinstance(t, str) and t.strip()]
    if not nonempty:
        return
    word_norms = await get_active_word_norms(db)
    if not word_norms:
        return
    all_hits: list[str] = []
    seen: set[str] = set()
    for t in nonempty:
        for h in find_matched_words(t, word_norms):
            if h not in seen:
                all_hits.append(h)
                seen.add(h)
    if all_hits:
        fail(
            ErrorCode.PROMPT_SENSITIVE_BLOCKED,
            content={"matchedWords": all_hits},
        )


async def assert_input_params_clean(
    db: AsyncSession,
    input_params: dict[str, Any] | None,
    *extra: str,
) -> None:
    """对生成 input_params 中的 prompt/content 做敏感词校验。"""
    texts = collect_prompt_texts(input_params, *extra)
    await assert_texts_clean(db, *texts)


async def list_sensitive_words(
    db: AsyncSession,
    *,
    q: str | None = None,
    enabled: bool | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SensitiveWord], int]:
    """管理端分页列表（直读 MySQL）。"""
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    stmt = select(SensitiveWord)
    count_stmt = select(func.count()).select_from(SensitiveWord)
    if enabled is not None:
        stmt = stmt.where(SensitiveWord.enabled.is_(enabled))
        count_stmt = count_stmt.where(SensitiveWord.enabled.is_(enabled))
    if q and q.strip():
        like = f"%{q.strip()}%"
        filt = or_(SensitiveWord.word.like(like), SensitiveWord.word_norm.like(like))
        stmt = stmt.where(filt)
        count_stmt = count_stmt.where(filt)
    total = int((await db.execute(count_stmt)).scalar() or 0)
    result = await db.execute(
        stmt.order_by(SensitiveWord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list(result.scalars().all()), total


async def create_sensitive_word(
    db: AsyncSession,
    *,
    word: str,
    enabled: bool = True,
    created_by: int | None = None,
) -> SensitiveWord:
    """新增敏感词并刷新 Redis 单 key。"""
    raw = (word or "").strip()
    if not raw:
        fail(ErrorCode.VALIDATION_ERROR, message="敏感词不能为空")
    if len(raw) > 128:
        fail(ErrorCode.VALIDATION_ERROR, message="敏感词最长 128 字")
    norm = normalize_for_match(raw)
    if not norm:
        fail(ErrorCode.VALIDATION_ERROR, message="规范化后敏感词为空，请换一个词")
    row = SensitiveWord(
        word=raw,
        word_norm=norm,
        enabled=bool(enabled),
        created_by=created_by,
        created_at=now_cst_naive(),
        updated_at=now_cst_naive(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        fail(ErrorCode.SENSITIVE_WORD_EXISTS, message="该敏感词已存在")
    await db.refresh(row)
    await publish_active_sensitive_words_cache(db)
    return row


async def update_sensitive_word(
    db: AsyncSession,
    word_id: int,
    *,
    word: str | None = None,
    enabled: bool | None = None,
) -> SensitiveWord:
    """更新词条或启停，并刷新 Redis 单 key。"""
    row = await db.get(SensitiveWord, word_id)
    if not row:
        fail(ErrorCode.SENSITIVE_WORD_NOT_FOUND)
    if word is not None:
        raw = word.strip()
        if not raw:
            fail(ErrorCode.VALIDATION_ERROR, message="敏感词不能为空")
        if len(raw) > 128:
            fail(ErrorCode.VALIDATION_ERROR, message="敏感词最长 128 字")
        norm = normalize_for_match(raw)
        if not norm:
            fail(ErrorCode.VALIDATION_ERROR, message="规范化后敏感词为空，请换一个词")
        row.word = raw
        row.word_norm = norm
    if enabled is not None:
        row.enabled = bool(enabled)
    row.updated_at = now_cst_naive()
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        fail(ErrorCode.SENSITIVE_WORD_EXISTS, message="该敏感词已存在")
    await db.refresh(row)
    await publish_active_sensitive_words_cache(db)
    return row


async def delete_sensitive_word(db: AsyncSession, word_id: int) -> None:
    """删除敏感词并刷新 Redis 单 key。"""
    row = await db.get(SensitiveWord, word_id)
    if not row:
        fail(ErrorCode.SENSITIVE_WORD_NOT_FOUND)
    await db.delete(row)
    await db.commit()
    await publish_active_sensitive_words_cache(db)


async def import_sensitive_words(
    db: AsyncSession,
    words: list[str],
    *,
    created_by: int | None = None,
) -> dict[str, int]:
    """批量导入（按规范化去重）；成功后只刷新一次 Redis。"""
    added = 0
    skipped = 0
    for raw in words:
        text = (raw or "").strip()
        if not text:
            skipped += 1
            continue
        if len(text) > 128:
            skipped += 1
            continue
        norm = normalize_for_match(text)
        if not norm:
            skipped += 1
            continue
        exists = await db.execute(
            select(SensitiveWord.id).where(SensitiveWord.word_norm == norm).limit(1)
        )
        if exists.scalar_one_or_none() is not None:
            skipped += 1
            continue
        db.add(
            SensitiveWord(
                word=text,
                word_norm=norm,
                enabled=True,
                created_by=created_by,
                created_at=now_cst_naive(),
                updated_at=now_cst_naive(),
            )
        )
        added += 1
    await db.commit()
    await publish_active_sensitive_words_cache(db)
    return {"added": added, "skipped": skipped}


async def test_sensitive_text(db: AsyncSession, text: str) -> dict[str, Any]:
    """管理端试检：返回是否命中与命中词。"""
    word_norms = await get_active_word_norms(db)
    hits = find_matched_words(text or "", word_norms)
    return {"ok": len(hits) == 0, "matchedWords": hits}
