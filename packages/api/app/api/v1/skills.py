"""Skill 目录与收藏 / 我的 Skill API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user, get_current_user_optional
from ...core.error_codes import ErrorCode
from ...core.errors import fail, ok
from ...models.database import get_db
from ...models.user import User
from ...services import skills_catalog
from ...services.cache import check_rate_limit
from ...services.discover_media import upload_skill_cover_image
from ...services.skill_docs import load_skill_doc_resolved

router = APIRouter()


class SaveFromSessionBody(BaseModel):
    """成功会话另存为我的 Skill。"""

    session_id: str = Field(..., alias="sessionId")
    title: str | None = None
    description: str | None = None

    model_config = {"populate_by_name": True}


class CreateFromPromptBody(BaseModel):
    """一句话 AI 生成我的 Skill。"""

    prompt: str = Field(..., min_length=1, max_length=500)
    # 创建表单已选类型时传入，强制 AI 按该类型出节点/规格
    media_kind: str | None = Field(None, alias="mediaKind")

    model_config = {"populate_by_name": True}


class CreateFromFormBody(BaseModel):
    """LibTV 风格表单创建我的 Skill。"""

    title: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=2, max_length=200)
    use_cases: str = Field(..., alias="useCases", min_length=2, max_length=8000)
    how_to_use: str = Field(..., alias="howToUse", min_length=2, max_length=8000)
    output_content: str = Field(..., alias="outputContent", min_length=2, max_length=8000)
    # image | video | text | audio
    media_kind: str = Field("image", alias="mediaKind")
    doc_markdown: str | None = Field(None, alias="docMarkdown", max_length=50000)
    cover_url: str | None = Field(None, alias="coverUrl", max_length=1024)
    idea: str | None = Field(None, max_length=500)
    aspect_ratio: str | None = Field(None, alias="aspectRatio")
    duration_sec: str | None = Field(None, alias="durationSec")
    clarity: str | None = None
    # AI 推算的画布节点配方
    node_recipe: dict | None = Field(None, alias="nodeRecipe")
    # 技能包能力对齐（均可选，缺省不影响存量行为）：
    # team=确定性模板投影（默认）；canvas_manual=对齐平台技能包的精细画布操控
    execution_mode: str | None = Field(None, alias="executionMode")
    # 技能自定义的画布规则覆盖，追加进 Agent 画布操控说明书附录
    canvas_rules_markdown: str | None = Field(
        None, alias="canvasRulesMarkdown", max_length=8000
    )
    # 多文件参考包：{相对路径: markdown 正文}，与 doc_markdown（SKILL.md 主文件）合并整包注入
    package_files: dict | None = Field(None, alias="packageFiles")
    # 流程设置：[{title, note}]，写入技能包 references/flow.md
    flow_steps: list | None = Field(None, alias="flowSteps")

    model_config = {"populate_by_name": True}


class ApplyRecipeBody(BaseModel):
    """将我的 Skill 配方一键铺到项目画布。"""

    project_id: str = Field(..., alias="projectId")
    session_id: str | None = Field(None, alias="sessionId")
    force: bool = False

    model_config = {"populate_by_name": True}


@router.get("")
async def list_skills(
    category: str | None = Query(None, description="分类；「推荐」或空=全部"),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """公开 Skill 目录（可匿名）；登录用户带 favorited。"""
    all_items = await skills_catalog.list_public_skills(db, category=None, user=current_user)
    if category and category.strip() and category.strip() != "推荐":
        items = [x for x in all_items if str(x.get("category") or "") == category.strip()]
    else:
        items = all_items
    return ok(
        {
            "items": items,
            "categories": await skills_catalog.list_skill_categories(db, all_items),
        }
    )


@router.get("/favorites")
async def list_my_favorite_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户收藏的 Skill。"""
    items = await skills_catalog.list_favorite_skills(db, current_user)
    return ok({"items": items})


@router.get("/mine")
async def list_my_skills(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户私有 Skill（另存流水线）。"""
    items = await skills_catalog.list_my_skills(db, current_user)
    return ok({"items": items})


@router.post("/from-session")
async def save_skill_from_session(
    body: SaveFromSessionBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把成功 Agent 会话另存为我的 Skill。"""
    content = await skills_catalog.save_skill_from_session(
        db,
        current_user,
        session_id=body.session_id,
        title=body.title,
        description=body.description,
    )
    await db.commit()
    return ok(content)


@router.post("/from-prompt")
async def create_skill_from_prompt(
    body: CreateFromPromptBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """一句话让 AI 生成「我的 Skill」（标题 / 默认想法 / 流水线 / SKILL.md）。"""
    allowed = await check_rate_limit(
        f"skill_from_prompt:{current_user.id}",
        limit=10,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="创建过于频繁，请稍后再试")
    content = await skills_catalog.create_skill_from_prompt(
        db,
        current_user,
        prompt=body.prompt,
    )
    await db.commit()
    return ok(content)


@router.post("/draft-from-prompt")
async def draft_skill_from_prompt(
    body: CreateFromPromptBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """一句话 AI 填充创建表单（不落库）；按后台 skillAiFill 预扣算力。"""
    allowed = await check_rate_limit(
        f"skill_draft_prompt:{current_user.id}",
        limit=20,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="生成过于频繁，请稍后再试")

    # 校验提示词后再预扣，避免空请求扣费
    text = (body.prompt or "").strip()
    if len(text) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID)
    if len(text) > 500:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="描述过长，请控制在 500 字以内")

    from ...services.skill_ai_fill_credits import (
        charge_skill_ai_fill,
        commit_skill_ai_fill,
        release_skill_ai_fill,
    )

    # 先预扣，再调 LLM；失败原路退还
    charge = await charge_skill_ai_fill(db, user=current_user)
    await db.commit()
    reservation_id = charge.get("reservationId")
    fill_id = str(charge.get("fillId") or "")
    amount = int(charge.get("total") or 0)
    try:
        draft = await skills_catalog.draft_skill_from_prompt(
            text, media_kind=body.media_kind
        )
        await commit_skill_ai_fill(db, reservation_id)
        await db.commit()
        return ok(draft)
    except Exception:
        await release_skill_ai_fill(
            db,
            user=current_user,
            reservation_id=reservation_id,
            amount=amount,
            fill_id=fill_id,
        )
        await db.commit()
        raise


@router.post("/from-form")
async def create_skill_from_form(
    body: CreateFromFormBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """LibTV 风格表单保存「我的 Skill」。"""
    allowed = await check_rate_limit(
        f"skill_from_form:{current_user.id}",
        limit=30,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="创建过于频繁，请稍后再试")
    content = await skills_catalog.create_skill_from_form(
        db,
        current_user,
        title=body.title,
        description=body.description,
        use_cases=body.use_cases,
        how_to_use=body.how_to_use,
        output_content=body.output_content,
        media_kind=body.media_kind,
        doc_markdown=body.doc_markdown,
        cover_url=body.cover_url,
        idea=body.idea,
        aspect_ratio=body.aspect_ratio,
        duration_sec=body.duration_sec,
        clarity=body.clarity,
        node_recipe=body.node_recipe,
        execution_mode=body.execution_mode,
        canvas_rules_markdown=body.canvas_rules_markdown,
        package_files=body.package_files,
        flow_steps=body.flow_steps,
    )
    await db.commit()
    return ok(content)


@router.post("/import-package")
async def import_skill_package(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, alias="dryRun"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传 Agent Skills / Codex 风格 zip：只入库 SKILL.md + references/*.md。

    scripts/ 记入 unsupportedFiles，服务端不执行。dryRun=true 时只校验预览不落库。
    """
    allowed = await check_rate_limit(
        f"skill_import_zip:{current_user.id}",
        limit=20,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="导入过于频繁，请稍后再试")
    filename = str(file.filename or "").lower()
    if filename and not filename.endswith(".zip"):
        fail(ErrorCode.INVALID_FILE_TYPE, message="请上传 .zip 技能包")
    raw = await file.read()
    if not raw:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="空文件")
    if len(raw) > 2 * 1024 * 1024:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="zip 过大（上限 2MB）")
    content = await skills_catalog.create_skill_from_package_zip(
        db,
        current_user,
        zip_bytes=raw,
        dry_run=bool(dry_run),
    )
    if not dry_run:
        await db.commit()
    return ok(content)


@router.post("/{slug}/apply-recipe")
async def apply_skill_recipe(
    slug: str,
    body: ApplyRecipeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """我的 Skill：确认后按 nodeRecipe 铺空节点与连线（不跑 Agent Team）。"""
    allowed = await check_rate_limit(
        f"skill_apply_recipe:{current_user.id}",
        limit=60,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="操作过于频繁，请稍后再试")
    content = await skills_catalog.apply_skill_recipe(
        db,
        current_user,
        slug=slug,
        project_id=body.project_id,
        session_id=body.session_id,
        force=bool(body.force),
    )
    await db.commit()
    return ok(content)


@router.post("/cover")
async def upload_my_skill_cover(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """上传我的 Skill 封面图。"""
    allowed = await check_rate_limit(
        f"skill_cover:{current_user.id}",
        limit=20,
        window_s=3600,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="上传过于频繁，请稍后再试")
    raw = await file.read()
    try:
        uploaded = upload_skill_cover_image(
            file.filename or "cover.jpg",
            raw,
            file.content_type,
            user_id=int(current_user.id),
        )
    except ValueError as exc:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message=str(exc) or "封面上传失败")
    return ok(uploaded)


@router.get("/{slug}/doc")
async def get_skill_doc(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """读取 Skill 的 SKILL.md（平台文件/管理覆盖，或我的 Skill 行内 doc_markdown）。"""
    # 先校验 Skill 可见，再读 MD
    skill = await skills_catalog.require_skill_by_slug(db, slug, user=current_user)
    doc = await load_skill_doc_resolved(db, slug, skill_row=skill)
    markdown = ""
    if isinstance(doc, dict):
        markdown = str(doc.get("raw") or doc.get("markdown") or "").strip()
    # 平台 Skill 或公开 Skill：无文件/空正文时拼装可预览 fallback（禁止「暂无说明」）
    need_fallback = doc is None or not markdown
    is_platform = getattr(skill, "owner_user_id", None) is None
    if need_fallback and (is_platform or skill.visibility == "public"):
        pipe = skill.pipeline if isinstance(skill.pipeline, list) else []
        steps_txt = "\n".join(
            f"- `{s.get('agent')}` → `{s.get('action')}`"
            for s in pipe
            if isinstance(s, dict) and s.get("action")
        ) or "- （暂无声明步骤）"
        title = skill.title or slug
        desc = (skill.description or "").strip() or title
        fallback_md = (
            f"---\nname: {title}\nslug: {slug}\ntitle: {title}\n"
            f"description: {desc.replace(chr(10), ' ')}\n"
            f"entryKind: {skill.entry_kind or ''}\n---\n\n"
            f"# {title}\n\n{desc}\n\n## pipeline\n\n{steps_txt}\n"
        )
        doc = {
            "slug": slug,
            "title": title,
            "name": title,
            "description": desc,
            "summary": desc[:200],
            "markdown": fallback_md,
            "raw": fallback_md,
            "pipeline": {"steps": pipe},
            "execution": "",
            "entryKind": skill.entry_kind or "",
            "source": "fallback",
        }
        markdown = fallback_md
    if doc is None or not markdown:
        fail(ErrorCode.SKILL_NOT_FOUND, message="该 Skill 暂无说明文档")
    pipe = doc.get("pipeline")
    steps = pipe.get("steps") if isinstance(pipe, dict) else pipe
    execution = doc.get("execution") or ""
    entry_kind = doc.get("entryKind") or ""
    if isinstance(pipe, dict):
        execution = execution or str(pipe.get("execution") or "")
        entry_kind = entry_kind or str(pipe.get("entryKind") or "")
    return ok(
        {
            "slug": doc["slug"],
            "title": doc.get("title") or doc.get("name"),
            "description": doc.get("description") or "",
            "summary": doc.get("summary") or "",
            "markdown": markdown,
            "pipeline": steps or [],
            "execution": execution,
            "entryKind": entry_kind,
            "source": doc.get("source") or "file",
        }
    )


class UpdateSkillMetaBody(BaseModel):
    """所有者更新 Skill 封面 / 名称 / 分类。"""

    title: str | None = Field(None, max_length=64)
    cover_url: str | None = Field(None, alias="coverUrl", max_length=1024)
    category: str | None = Field(None, max_length=64)
    # 显式清空封面（与未传 coverUrl 区分）
    clear_cover: bool = Field(False, alias="clearCover")
    # 技能包能力对齐：执行模式 / 画布规则覆盖 / 多文件参考包（均可选，创建后仍可追加编辑）
    execution_mode: str | None = Field(None, alias="executionMode")
    canvas_rules_markdown: str | None = Field(
        None, alias="canvasRulesMarkdown", max_length=8000
    )
    package_files: dict | None = Field(None, alias="packageFiles")
    # 显式清空参考包（与未传 packageFiles 区分）
    clear_package_files: bool = Field(False, alias="clearPackageFiles")

    model_config = {"populate_by_name": True}


@router.patch("/{slug}")
async def update_my_skill_meta(
    slug: str,
    body: UpdateSkillMetaBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新自己的 Skill 元信息；已发布内容修改后回待审。"""
    content = await skills_catalog.update_my_skill_meta(
        db,
        current_user,
        slug,
        title=body.title,
        cover_url=body.cover_url,
        category=body.category,
        clear_cover=bool(body.clear_cover),
        execution_mode=body.execution_mode,
        canvas_rules_markdown=body.canvas_rules_markdown,
        package_files=body.package_files,
        clear_package_files=bool(body.clear_package_files),
    )
    await db.commit()
    return ok(content)


@router.get("/{slug}")
async def get_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Skill 详情。"""
    skill = await skills_catalog.require_skill_by_slug(db, slug, user=current_user)
    favorited = False
    if current_user is not None:
        favs = await skills_catalog.list_favorite_skills(db, current_user)
        favorited = any(str(x.get("slug")) == skill.slug for x in favs)
    return ok(await skills_catalog.skill_to_dict_resolved(db, skill, favorited=favorited))


@router.delete("/{slug}")
async def delete_my_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """归档自己的私有 Skill。"""
    content = await skills_catalog.archive_my_skill(db, current_user, slug)
    await db.commit()
    return ok(content)


@router.post("/{slug}/publish")
async def publish_my_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交社区发布（管理员审核通过后进入公开目录）。"""
    content = await skills_catalog.publish_my_skill(db, current_user, slug)
    await db.commit()
    return ok(content)


@router.post("/{slug}/unpublish")
async def unpublish_my_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤回公开或取消待审。"""
    content = await skills_catalog.unpublish_my_skill(db, current_user, slug)
    await db.commit()
    return ok(content)


@router.post("/{slug}/favorite")
async def favorite_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """收藏 Skill。"""
    content = await skills_catalog.add_skill_favorite(db, current_user, slug)
    await db.commit()
    return ok(content)


@router.delete("/{slug}/favorite")
async def unfavorite_skill(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消收藏。"""
    content = await skills_catalog.remove_skill_favorite(db, current_user, slug)
    await db.commit()
    return ok(content)
