from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_PROMPT_TEMPLATES
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.user import User
from ....schemas.prompt_templates import (
    PromptConfigOut,
    PromptPreviewIn,
    PromptPreviewOut,
    PromptToolConfigIn,
    PromptToolConfigOut,
)
from ....services import prompt_config as cfg
from ....services import prompt_render as render
from ....services.cache import check_rate_limit
from ....services.visual_style import upload_visual_style_image

router = APIRouter()


class VisualStyleImageOut(BaseModel):
    image_url: str = Field(..., alias="imageUrl")
    oss_key: str = Field(..., alias="ossKey")


@router.get("/prompt-config", response_model=PromptConfigOut)
async def get_admin_prompt_config(_: User = Depends(require_permission(PERM_PROMPT_TEMPLATES))):
    from ....services.prompt_platform_runtime import ensure_prompt_platform_fresh
    from ....services.visual_style import resign_visual_styles_tool_config

    # 管理端与用户端一致：先对齐 Redis/OSS，再合并内置缺省工具
    await ensure_prompt_platform_fresh()
    data = cfg.load_config()
    tools = dict(data.get("tools") or {})
    vs = tools.get("visual_style")
    if isinstance(vs, dict):
        tools["visual_style"] = resign_visual_styles_tool_config(vs)
    return PromptConfigOut(version=int(data.get("version") or 2), tools=tools)


@router.get("/prompt-config/tools/{tool_id}", response_model=PromptToolConfigOut)
async def get_admin_prompt_tool(tool_id: str, _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES))):
    from ....services.prompt_platform_runtime import ensure_prompt_platform_fresh

    await ensure_prompt_platform_fresh()
    tool = cfg.get_tool(tool_id)
    if not tool:
        fail(ErrorCode.PROMPT_TOOL_NOT_FOUND, message=f"Tool '{tool_id}' not found")
    return PromptToolConfigOut(tool=tool_id, config=tool)


@router.put("/prompt-config/tools/{tool_id}", response_model=PromptToolConfigOut)
async def save_admin_prompt_tool(
    tool_id: str,
    body: PromptToolConfigIn,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    config = dict(body.config or {})
    errors = render.validate_tool_config(tool_id, config)
    if errors:
        fail(ErrorCode.VALIDATION_ERROR, message="; ".join(errors))
    saved = await cfg.save_tool(tool_id, config)
    return PromptToolConfigOut(tool=tool_id, config=saved)


@router.post("/prompt-config/tools/{tool_id}/reset", response_model=PromptToolConfigOut)
async def reset_admin_prompt_tool(tool_id: str, _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES))):
    try:
        saved = await cfg.reset_tool(tool_id)
    except ValueError as exc:
        fail(ErrorCode.PROMPT_TOOL_NOT_FOUND, message=str(exc))
    return PromptToolConfigOut(tool=tool_id, config=saved)


@router.post("/prompt-config/preview", response_model=PromptPreviewOut)
async def preview_admin_prompt_tool(
    body: PromptPreviewIn,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    if body.config:
        tool = dict(body.config)
    else:
        tool = cfg.get_tool(body.tool)
    if not tool:
        fail(ErrorCode.PROMPT_TOOL_NOT_FOUND, message=f"Tool '{body.tool}' not found")
    errors = render.validate_tool_config(body.tool, tool)
    prompt = render.render_tool_prompt(tool, body.runtime or {})
    return PromptPreviewOut(tool=body.tool, prompt=prompt, errors=errors)


@router.post("/prompt-config/visual-style-image", response_model=VisualStyleImageOut)
async def upload_admin_visual_style_image(
    style_id: str = Form(..., alias="styleId"),
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    allowed = await check_rate_limit(
        f"admin:visual-style-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        result = upload_visual_style_image(style_id, file.filename or "style.jpg", raw, file.content_type)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    return VisualStyleImageOut(imageUrl=result["imageUrl"], ossKey=result["ossKey"])
