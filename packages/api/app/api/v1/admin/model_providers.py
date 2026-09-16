"""管理端 — 模型供应商与 API 密钥（加密存 DB，响应不含明文）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_MODEL_PROVIDERS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....schemas.admin import (
    AdminModelProviderOut,
    AdminModelProviderPatchIn,
    AdminProviderCredentialOut,
    AdminProviderCredentialPatchIn,
    AdminProviderCredentialPutIn,
    AdminProviderCredentialTestOut,
    AdminProviderReferencedModelOut,
)
from ....services.credential_service import (
    after_provider_enablement_changed,
    invalidate_credential_cache,
    list_model_providers_for_admin,
    list_provider_credentials_masked,
    list_provider_referenced_models,
    patch_model_provider_row,
    patch_provider_credential,
    test_provider_credential,
    upsert_provider_credential,
)
from ....services.provider_admin_ui import profile_label

router = APIRouter()


def _provider_out(item: dict) -> AdminModelProviderOut:
    return AdminModelProviderOut(
        code=item["code"],
        display_name=item["display_name"],
        defaultApiBase=item.get("default_api_base"),
        isEnabled=item["is_enabled"],
        sortOrder=item["sort_order"],
        hasCredential=item["has_credential"],
        credentialProfiles=item.get("credential_profiles") or [],
        apiKeyHint=item.get("api_key_hint"),
        modelCount=int(item.get("model_count") or 0),
        status=str(item.get("status") or "unconfigured"),
        lastTestOk=item.get("last_test_ok"),
        lastTestedAt=item.get("last_tested_at"),
        lastTestMessage=item.get("last_test_message"),
        defaultCredentialUpdatedAt=item.get("default_credential_updated_at"),
        ui=item.get("ui"),
    )


def _credential_out_from_row(row, *, provider_code: str) -> AdminProviderCredentialOut:
    """组装掩码响应，永不返回明文密钥。"""
    endpoint = row.endpoint_id or ""
    masked = (endpoint[:4] + "****" + endpoint[-4:]) if len(endpoint) > 8 else endpoint
    return AdminProviderCredentialOut(
        profileKey=row.profile_key,
        profileLabel=profile_label(provider_code, row.profile_key),
        apiKeyHint=row.api_key_hint,
        endpointIdMasked=masked,
        apiBase=row.api_base,
        isActive=row.is_active,
        updatedAt=row.updated_at,
        updatedBy=row.updated_by,
    )


@router.get("/model-providers", response_model=list[AdminModelProviderOut])
async def list_admin_model_providers(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """供应商总览：状态、尾号、引用模型数、UI 模板。"""
    items = await list_model_providers_for_admin(db)
    return [_provider_out(item) for item in items]


@router.patch("/model-providers/{provider_code}", response_model=AdminModelProviderOut)
async def patch_admin_model_provider(
    provider_code: str,
    body: AdminModelProviderPatchIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """更新供应商默认 API Base / 启停。"""
    try:
        provider = await patch_model_provider_row(
            db,
            provider_code,
            default_api_base=body.default_api_base,
            is_enabled=body.is_enabled,
        )
    except LookupError:
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    # 启停变更：立即刷新运行时密钥快照（禁止 env fallback 补回已停用供应商）
    if body.is_enabled is not None:
        await after_provider_enablement_changed(db)
    else:
        await invalidate_credential_cache()
    items = await list_model_providers_for_admin(db)
    row = next((i for i in items if i["code"] == provider.code), None)
    if not row:
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    return _provider_out(row)


@router.get(
    "/model-providers/{provider_code}/models",
    response_model=list[AdminProviderReferencedModelOut],
)
async def list_admin_provider_models(
    provider_code: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """该供应商下的目录模型列表（密钥抽屉只读关联）。"""
    items = await list_model_providers_for_admin(db)
    if not any(i["code"] == provider_code for i in items):
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    rows = await list_provider_referenced_models(db, provider_code)
    return [
        AdminProviderReferencedModelOut(
            id=r["id"],
            name=r["name"],
            displayName=r["display_name"],
            category=r["category"],
            isAvailable=r["is_available"],
        )
        for r in rows
    ]


@router.get("/model-providers/{provider_code}/credentials", response_model=list[AdminProviderCredentialOut])
async def list_admin_provider_credentials(
    provider_code: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """密钥掩码列表（不含明文）。"""
    items = await list_model_providers_for_admin(db)
    if not any(i["code"] == provider_code for i in items):
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    rows = await list_provider_credentials_masked(db, provider_code)
    return [
        AdminProviderCredentialOut(
            profileKey=row["profile_key"],
            profileLabel=row.get("profile_label"),
            apiKeyHint=row.get("api_key_hint"),
            endpointIdMasked=row.get("endpoint_id_masked") or "",
            apiBase=row.get("api_base"),
            isActive=row["is_active"],
            updatedAt=row.get("updated_at"),
            updatedBy=row.get("updated_by"),
            lastTestOk=row.get("last_test_ok"),
            lastTestedAt=row.get("last_tested_at"),
            lastTestMessage=row.get("last_test_message"),
        )
        for row in rows
    ]


@router.put("/model-providers/{provider_code}/credentials/{profile_key}", response_model=AdminProviderCredentialOut)
async def put_admin_provider_credential(
    provider_code: str,
    profile_key: str,
    body: AdminProviderCredentialPutIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """写入或轮换 API Key（加密落库）；编辑时 apiKey 可省略以保留原值。"""
    try:
        row = await upsert_provider_credential(
            db,
            provider_code=provider_code,
            profile_key=profile_key,
            api_key=body.api_key,
            api_base=body.api_base,
            endpoint_id=body.endpoint_id,
            updated_by=int(current_user.id),
        )
    except LookupError:
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    return _credential_out_from_row(row, provider_code=provider_code)


@router.patch("/model-providers/{provider_code}/credentials/{profile_key}", response_model=AdminProviderCredentialOut)
async def patch_admin_provider_credential(
    provider_code: str,
    profile_key: str,
    body: AdminProviderCredentialPatchIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """部分更新凭证；未传 apiKey 时不轮换数据库中的加密密钥。"""
    items = await list_model_providers_for_admin(db)
    if not any(i["code"] == provider_code for i in items):
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    try:
        row = await patch_provider_credential(
            db,
            provider_code=provider_code,
            profile_key=profile_key,
            api_key=body.api_key,
            api_base=body.api_base,
            endpoint_id=body.endpoint_id,
            updated_by=int(current_user.id),
        )
    except LookupError:
        fail(ErrorCode.PROVIDER_NOT_FOUND, message="凭证不存在，请先创建")
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    return _credential_out_from_row(row, provider_code=provider_code)


@router.post(
    "/model-providers/{provider_code}/credentials/{profile_key}/test",
    response_model=AdminProviderCredentialTestOut,
)
async def post_admin_provider_credential_test(
    provider_code: str,
    profile_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_MODEL_PROVIDERS)),
):
    """测试密钥是否可解密且已配置，并写入最近测通结果。"""
    items = await list_model_providers_for_admin(db)
    if not any(i["code"] == provider_code for i in items):
        fail(ErrorCode.PROVIDER_NOT_FOUND)
    result = await test_provider_credential(
        db,
        provider_code,
        profile_key=profile_key,
        tested_by=int(current_user.id),
    )
    return AdminProviderCredentialTestOut(
        ok=result["ok"],
        message=result["message"],
        apiBase=result.get("api_base"),
        hasEndpointId=result.get("has_endpoint_id"),
        testedAt=result.get("tested_at"),
    )
