from fastapi import APIRouter, Depends, Query

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_PROMPT_TEMPLATES
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.user import User
from ....schemas.prompt_templates import (
    PromptTemplateCreateIn,
    PromptTemplateDocumentIn,
    PromptTemplateDocumentOut,
    PromptTemplateListOut,
    PromptTemplateOut,
    PromptTemplatePatchIn,
)
from ....services import prompt_templates as svc
from ....services import prompt_template_document as doc

router = APIRouter()


def _out(raw: dict) -> PromptTemplateOut:
    return PromptTemplateOut(
        id=str(raw.get("id") or ""),
        tool=str(raw.get("tool") or ""),
        category=str(raw.get("category") or ""),
        key=str(raw.get("key") or ""),
        label=str(raw.get("label") or ""),
        content=str(raw.get("content") or ""),
        enabled=bool(raw.get("enabled", True)),
        sort_order=int(raw.get("sort_order") or 0),
    )


@router.get("/prompt-templates/document", response_model=PromptTemplateDocumentOut)
async def get_admin_prompt_document(_: User = Depends(require_permission(PERM_PROMPT_TEMPLATES))):
    return PromptTemplateDocumentOut(
        document=doc.serialize_document(),
        help=doc.document_help_text(),
    )


@router.put("/prompt-templates/document", response_model=PromptTemplateDocumentOut)
async def save_admin_prompt_document(
    body: PromptTemplateDocumentIn,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    try:
        await doc.save_document(body.document)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return PromptTemplateDocumentOut(
        document=doc.serialize_document(),
        help=doc.document_help_text(),
    )


@router.post("/prompt-templates/document/reset", response_model=PromptTemplateDocumentOut)
async def reset_admin_prompt_document(_: User = Depends(require_permission(PERM_PROMPT_TEMPLATES))):
    try:
        document = await doc.reset_document_to_default()
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return PromptTemplateDocumentOut(document=document, help=doc.document_help_text())


@router.get("/prompt-templates", response_model=PromptTemplateListOut)
async def list_admin_prompt_templates(
    tool: str = Query("multi_angle", description="Tool key, e.g. multi_angle"),
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    items = svc.list_templates(tool=tool)
    return PromptTemplateListOut(tool=tool, templates=[_out(item) for item in items])


@router.post("/prompt-templates", response_model=PromptTemplateOut, status_code=201)
async def create_admin_prompt_template(
    body: PromptTemplateCreateIn,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    payload = {
        "tool": body.tool.strip(),
        "category": body.category.strip(),
        "key": body.key.strip(),
        "label": body.label,
        "content": body.content,
        "enabled": body.enabled,
        "sort_order": body.sort_order,
    }
    if not payload["tool"] or not payload["category"] or not payload["key"]:
        fail(ErrorCode.PROMPT_FIELDS_REQUIRED)
    created = await svc.create_template(payload)
    return _out(created)


@router.post("/prompt-templates/reset", response_model=PromptTemplateListOut)
async def reset_admin_prompt_templates(
    tool: str = Query(..., description="Tool key to reset from bundled defaults"),
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    if not tool.strip():
        fail(ErrorCode.PROMPT_FIELDS_REQUIRED, message="tool 为必填项")
    items = await svc.reset_tool_templates(tool.strip())
    if not items:
        fail(ErrorCode.NO_BUNDLED_DEFAULTS, message=f"No bundled defaults for tool '{tool}'")
    return PromptTemplateListOut(tool=tool.strip(), templates=[_out(item) for item in items])


@router.patch("/prompt-templates/{template_id}", response_model=PromptTemplateOut)
async def patch_admin_prompt_template(
    template_id: str,
    body: PromptTemplatePatchIn,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if "sort_order" in patch and patch["sort_order"] is None:
        patch.pop("sort_order")
    updated = await svc.update_template(template_id, patch)
    if not updated:
        fail(ErrorCode.PROMPT_TEMPLATE_NOT_FOUND)
    return _out(updated)


@router.delete("/prompt-templates/{template_id}", status_code=204)
async def delete_admin_prompt_template(
    template_id: str,
    _: User = Depends(require_permission(PERM_PROMPT_TEMPLATES)),
):
    if not await svc.delete_template(template_id):
        fail(ErrorCode.PROMPT_TEMPLATE_NOT_FOUND)
