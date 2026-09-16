from fastapi import APIRouter

from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...schemas.prompt_templates import PromptConfigOut, PromptPreviewIn, PromptPreviewOut, PromptToolConfigOut
from ...services import prompt_config as cfg
from ...services import prompt_render as render

router = APIRouter()


@router.get("", response_model=PromptConfigOut)
async def get_prompt_config():
    from ...services.prompt_platform_runtime import ensure_prompt_platform_fresh
    from ...services.visual_style import resign_visual_styles_tool_config

    await ensure_prompt_platform_fresh()
    data = cfg.load_config()
    tools = dict(data.get("tools") or {})
    # 九宫格：对外统一返回 creative_tools（含各功能提示词 items）
    grid = cfg.get_tool("grid_9")
    if isinstance(grid, dict):
        tools["grid_9"] = grid
    vs = tools.get("visual_style")
    if isinstance(vs, dict):
        tools["visual_style"] = resign_visual_styles_tool_config(vs)
    return PromptConfigOut(version=int(data.get("version") or 2), tools=tools)


@router.get("/{tool_id}", response_model=PromptToolConfigOut)
async def get_prompt_tool_config(tool_id: str):
    from ...services.prompt_platform_runtime import ensure_prompt_platform_fresh

    await ensure_prompt_platform_fresh()
    tool = cfg.get_tool(tool_id)
    if not tool:
        fail(ErrorCode.PROMPT_TOOL_NOT_FOUND, message=f"Tool '{tool_id}' not found")
    return PromptToolConfigOut(tool=tool_id, config=tool)


@router.post("/preview", response_model=PromptPreviewOut)
async def preview_prompt_tool(body: PromptPreviewIn):
    tool = cfg.get_tool(body.tool)
    if not tool:
        fail(ErrorCode.PROMPT_TOOL_NOT_FOUND, message=f"Tool '{body.tool}' not found")
    errors = render.validate_tool_config(body.tool, tool)
    prompt = render.render_tool_prompt(tool, body.runtime or {})
    return PromptPreviewOut(tool=body.tool, prompt=prompt, errors=errors)
