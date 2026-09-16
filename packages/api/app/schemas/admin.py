from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from ..core.datetime_util import to_cst_iso


class AdminUserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_no: Optional[str] = Field(None, alias="userNo")
    source_user_id: str
    phone: Optional[str] = None
    display_name: str
    avatar_url: Optional[str] = None
    role: str
    is_super_admin: bool = Field(False, alias="isSuperAdmin")
    permissions: list[str] = Field(default_factory=list)
    can_manage_permissions: bool = Field(False, alias="canManagePermissions")


class AdminStatsDailyPoint(BaseModel):
    """按日聚合的运维趋势点（东八区日历日）。

    count 同时用于「条数」与「算力汇总」：算力已支持一位小数，故用 float。
    """

    date: str
    count: float = 0


class AdminStatsOut(BaseModel):
    user_count: int
    project_count: int
    workflow_count: int
    jobs: dict[str, int]
    # 今日 / 近 7 日 / 近 30 日新增（含今日）；字段名避免 _7d 以免前端 snake→camel 丢转换
    users_today: int = 0
    users_week: int = 0
    users_month: int = 0
    projects_today: int = 0
    projects_week: int = 0
    projects_month: int = 0
    jobs_today: int = 0
    jobs_week: int = 0
    jobs_month: int = 0
    # 近 30 日按日趋势（缺日补 0）
    user_trend: list[AdminStatsDailyPoint] = Field(default_factory=list)
    project_trend: list[AdminStatsDailyPoint] = Field(default_factory=list)
    job_trend: list[AdminStatsDailyPoint] = Field(default_factory=list)
    # 任务按 lane / 近 7 日成败
    jobs_by_lane: dict[str, int] = Field(default_factory=dict)
    jobs_succeeded_week: int = 0
    jobs_failed_week: int = 0
    # 算力运维：库存 / 消耗 / 发放（东八区 · 一位小数）
    credits_remaining_total: float = 0
    credits_by_type: dict[str, float] = Field(default_factory=dict)
    credits_consumed_today: float = 0
    credits_consumed_week: float = 0
    credits_consumed_month: float = 0
    credits_granted_today: float = 0
    credits_granted_week: float = 0
    credits_granted_month: float = 0
    credit_consume_trend: list[AdminStatsDailyPoint] = Field(default_factory=list)
    credit_grant_trend: list[AdminStatsDailyPoint] = Field(default_factory=list)


class AdminJobOut(BaseModel):
    id: str
    user_id: str
    user_no: Optional[str] = Field(None, alias="userNo")
    user_display_name: Optional[str] = None
    user_phone: Optional[str] = None
    project_id: Optional[str] = None
    project_no: Optional[str] = None
    project_title: Optional[str] = None
    workflow_id: Optional[str] = None
    node_id: Optional[str] = None
    job_type: str
    scene: Optional[str] = None
    lane: str
    status: str
    provider: Optional[str] = None
    request_id: Optional[str] = None
    provider_request_id: Optional[str] = None
    provider_task_id: Optional[str] = None
    provider_job_id: Optional[str] = None
    processing_id: Optional[str] = None
    upstream_job_id: Optional[str] = None
    model: Optional[str] = None
    model_display_name: Optional[str] = None
    # 实际命中的主/副上游通道（来自 trace_json.channel）
    upstream_channel: Optional[str] = None
    upstream_channel_model: Optional[str] = None
    upstream_channel_provider: Optional[str] = None
    asset_id: Optional[str] = None
    # 算力一位小数（DECIMAL）；不可再用 int，否则 1.5 会导致整表 ValidationError
    credit_cost: float
    upstream_credit_cost: Optional[float] = None
    credit_status: Optional[str] = None
    pricing_version: Optional[int] = None
    credit_breakdown: Optional[list] = None
    error_message: Optional[str] = None
    anomaly_reason: Optional[str] = Field(None, alias="anomalyReason")
    anomaly_detected_at: Optional[datetime] = Field(None, alias="anomalyDetectedAt")
    anomaly_detail: Optional[str] = Field(None, alias="anomalyDetail")
    last_admin_action: Optional[str] = Field(None, alias="lastAdminAction")
    last_admin_action_at: Optional[datetime] = Field(None, alias="lastAdminActionAt")
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    execution_seconds: Optional[int] = None
    actual_deducted_credits: float = 0
    platform_quoted_credits: float = 0

    @field_serializer("created_at", "started_at", "completed_at", "anomaly_detected_at", "last_admin_action_at", when_used="json")
    def _serialize_dt(self, value: datetime | None) -> str | None:
        return to_cst_iso(value)


class AdminJobAssetOut(BaseModel):
    id: str
    project_id: str
    title: str
    category: str
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    created_at: Optional[str] = None


class AdminJobDetailOut(AdminJobOut):
    input_params: dict
    output_assets: Optional[list] = None
    result_text: Optional[str] = None
    trace_json: Optional[dict] = None
    project_asset: Optional[AdminJobAssetOut] = None
    admin_actions: list["AdminJobActionOut"] = Field(default_factory=list, alias="adminActions")


class AdminJobActionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: str
    note: str
    payload: Optional[dict] = None


class AdminJobActionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    job_id: str = Field(..., alias="jobId")
    operator_id: str = Field(..., alias="operatorId")
    action: str
    before_status: Optional[str] = Field(None, alias="beforeStatus")
    after_status: Optional[str] = Field(None, alias="afterStatus")
    before_credit_status: Optional[str] = Field(None, alias="beforeCreditStatus")
    after_credit_status: Optional[str] = Field(None, alias="afterCreditStatus")
    note: str
    payload: Optional[dict] = None
    created_at: datetime = Field(..., alias="createdAt")

    @field_serializer("created_at", when_used="json")
    def _serialize_action_dt(self, value: datetime) -> str | None:
        return to_cst_iso(value)


class AdminJobSyncActiveOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    interrupted_recovered: int = Field(0, alias="interruptedRecovered")
    stale_recovered: int = Field(0, alias="staleRecovered")
    exhausted_failed: int = Field(0, alias="exhaustedFailed")
    exhausted_pending_failed: int = Field(0, alias="exhaustedPendingFailed")
    upstream_synced: list[dict] = Field(default_factory=list, alias="upstreamSynced")
    upstream_skipped: list[dict] = Field(default_factory=list, alias="upstreamSkipped")
    status_counts: dict[str, int] = Field(default_factory=dict, alias="statusCounts")


class AdminJobActionResultOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    action: str
    job_id: str = Field(..., alias="jobId")
    status: str
    credit_status: Optional[str] = Field(None, alias="creditStatus")
    asset_id: Optional[str] = Field(None, alias="assetId")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    anomaly_reason: Optional[str] = Field(None, alias="anomalyReason")
    audit_id: str = Field(..., alias="auditId")
    message: Optional[str] = None
    upstream_state: Optional[str] = Field(None, alias="upstreamState")
    unchanged: Optional[bool] = None


class AdminJobListOut(BaseModel):
    items: list[AdminJobOut]
    total: int
    page: int
    page_size: int


class AdminModelOut(BaseModel):
    id: str
    name: str
    display_name: str
    provider: str
    model_type: str
    category: str
    description: Optional[str] = None
    is_available: bool
    sort_order: int
    parameters: Optional[dict] = None
    created_at: datetime
    is_configured: bool = False
    is_implemented: bool = False
    provider_group: str = ""


class AdminModelPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_available: Optional[bool] = Field(None, alias="isAvailable")
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    display_name: Optional[str] = Field(None, alias="displayName")
    provider: Optional[str] = None
    model_type: Optional[str] = Field(None, alias="modelType")
    category: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = Field(None, alias="coverUrl")
    parameters: Optional[dict] = None
    generation_presets: Optional[dict] = Field(None, alias="generationPresets")
    upstream_model: Optional[str] = Field(None, alias="upstreamModel")
    capabilities: Optional[list[str]] = None
    implementation: Optional[str] = None
    channels: Optional[list[dict]] = None


class AdminModelCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=2, max_length=128)
    display_name: str = Field(..., alias="displayName", min_length=1)
    provider: str
    model_type: str = Field("checkpoint", alias="modelType")
    category: str
    description: Optional[str] = None
    sort_order: int = Field(0, alias="sortOrder")
    is_available: bool = Field(False, alias="isAvailable")
    cover_url: Optional[str] = Field(None, alias="coverUrl")
    parameters: Optional[dict] = None
    upstream_model: Optional[str] = Field(None, alias="upstreamModel")
    capabilities: Optional[list[str]] = None
    implementation: Optional[str] = "reserved"
    channels: Optional[list[dict]] = None


class AdminModelTestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    message: str
    is_configured: bool = Field(..., alias="isConfigured")
    is_implemented: bool = Field(..., alias="isImplemented")


class AdminModelProviderOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    display_name: str
    default_api_base: Optional[str] = Field(None, alias="defaultApiBase")
    is_enabled: bool = Field(..., alias="isEnabled")
    sort_order: int = Field(..., alias="sortOrder")
    has_credential: bool = Field(..., alias="hasCredential")
    credential_profiles: list[str] = Field(default_factory=list, alias="credentialProfiles")
    api_key_hint: Optional[str] = Field(None, alias="apiKeyHint")
    model_count: int = Field(0, alias="modelCount")
    status: str = "unconfigured"
    last_test_ok: Optional[bool] = Field(None, alias="lastTestOk")
    last_tested_at: Optional[str] = Field(None, alias="lastTestedAt")
    last_test_message: Optional[str] = Field(None, alias="lastTestMessage")
    default_credential_updated_at: Optional[datetime] = Field(None, alias="defaultCredentialUpdatedAt")
    ui: Optional[dict] = None

    @field_serializer("default_credential_updated_at", when_used="json")
    def _serialize_cred_updated(self, value: datetime | None) -> str | None:
        return to_cst_iso(value)


class AdminModelProviderPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_api_base: Optional[str] = Field(None, alias="defaultApiBase")
    is_enabled: Optional[bool] = Field(None, alias="isEnabled")


class AdminProviderCredentialOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    profile_key: str = Field(..., alias="profileKey")
    profile_label: Optional[str] = Field(None, alias="profileLabel")
    api_key_hint: Optional[str] = Field(None, alias="apiKeyHint")
    endpoint_id_masked: str = Field("", alias="endpointIdMasked")
    api_base: Optional[str] = Field(None, alias="apiBase")
    is_active: bool = Field(..., alias="isActive")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")
    updated_by: Optional[int] = Field(None, alias="updatedBy")
    last_test_ok: Optional[bool] = Field(None, alias="lastTestOk")
    last_tested_at: Optional[str] = Field(None, alias="lastTestedAt")
    last_test_message: Optional[str] = Field(None, alias="lastTestMessage")


class AdminProviderCredentialPutIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # 新建时必填；编辑已有凭证时可省略，服务端保留原加密值
    api_key: Optional[str] = Field(None, alias="apiKey")
    api_base: Optional[str] = Field(None, alias="apiBase")
    endpoint_id: Optional[str] = Field(None, alias="endpointId")


class AdminProviderCredentialPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_key: Optional[str] = Field(None, alias="apiKey")
    api_base: Optional[str] = Field(None, alias="apiBase")
    endpoint_id: Optional[str] = Field(None, alias="endpointId")


class AdminProviderCredentialTestOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool
    message: str
    api_base: Optional[str] = Field(None, alias="apiBase")
    has_endpoint_id: Optional[bool] = Field(None, alias="hasEndpointId")
    tested_at: Optional[str] = Field(None, alias="testedAt")


class AdminProviderReferencedModelOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    display_name: str = Field(..., alias="displayName")
    category: str
    is_available: bool = Field(..., alias="isAvailable")


class AdminProjectOut(BaseModel):
    id: str
    project_no: str
    title: str
    owner_id: str
    owner_display_name: str
    owner_phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AdminProjectListOut(BaseModel):
    items: list[AdminProjectOut]
    total: int
    page: int
    page_size: int


class AdminProjectNodeOut(BaseModel):
    id: str
    type: str
    label: str


class AdminProjectAssetStatsOut(BaseModel):
    image: int
    video: int
    audio: int
    total: int


class AdminProjectDetailOut(BaseModel):
    id: str
    project_no: str
    title: str
    description: Optional[str] = None
    owner_id: str
    owner_display_name: str
    owner_phone: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    workflow_count: int
    latest_workflow_id: Optional[str] = None
    nodes: list[AdminProjectNodeOut]
    asset_stats: AdminProjectAssetStatsOut


class AdminUserListItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_no: Optional[str] = Field(None, alias="userNo")
    display_name: str
    phone: Optional[str] = None
    role: str
    is_super_admin: bool = Field(False, alias="isSuperAdmin")
    is_active: bool = True
    balance: float
    project_count: int
    job_count: int
    created_at: datetime
    last_login_at: Optional[datetime] = None


class AdminUserListOut(BaseModel):
    items: list[AdminUserListItemOut]
    total: int
    page: int
    page_size: int


class AdminUserRecentJobOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    status: str
    job_type: str
    lane: str
    model: Optional[str] = None
    credit_cost: float
    created_at: datetime


class AdminAuthEventBriefOut(BaseModel):
    id: str
    action: str
    result: str
    ip: str = ""
    created_at: datetime


class AdminUserDetailOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_no: Optional[str] = Field(None, alias="userNo")
    source_user_id: str
    display_name: str
    phone: Optional[str] = None
    role: str
    is_super_admin: bool = Field(False, alias="isSuperAdmin")
    permissions: list[str] = Field(default_factory=list)
    is_active: bool = True
    avatar_url: Optional[str] = None
    balance: float
    project_count: int
    job_count: int
    workflow_count: int
    session_count: int = 0
    jobs_by_status: dict[str, int]
    created_at: datetime
    last_login_at: Optional[datetime] = None
    last_login_ip: str = ""
    recent_auth_events: list[AdminAuthEventBriefOut] = []
    recent_jobs: list[AdminUserRecentJobOut]


class AdminUserPatchIn(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class AdminCreditTransactionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: str = Field(..., alias="userId")
    user_display_name: Optional[str] = Field(None, alias="userDisplayName")
    user_phone: Optional[str] = Field(None, alias="userPhone")
    user_no: Optional[str] = Field(None, alias="userNo")
    operator_id: Optional[str] = Field(None, alias="operatorId")
    delta: float
    balance_after: float = Field(..., alias="balanceAfter")
    source: str
    source_label: str = Field(..., alias="sourceLabel")
    credit_type: Optional[str] = Field(None, alias="creditType")
    model_name: Optional[str] = Field(None, alias="modelName")
    model_display_name: Optional[str] = Field(None, alias="modelDisplayName")
    job_id: Optional[str] = Field(None, alias="jobId")
    reason: Optional[str] = None
    created_at: datetime = Field(..., alias="createdAt")
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    expires_label: Optional[str] = Field(None, alias="expiresLabel")


class AdminCreditTransactionListOut(BaseModel):
    items: list[AdminCreditTransactionOut]
    total: int
    page: int
    page_size: int


class AdminRechargeOrderOut(BaseModel):
    """管理端充值/支付订单单条记录。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: str = Field(..., alias="userId")
    user_display_name: Optional[str] = Field(None, alias="userDisplayName")
    user_phone: Optional[str] = Field(None, alias="userPhone")
    user_no: Optional[str] = Field(None, alias="userNo")
    out_trade_no: str = Field(..., alias="outTradeNo")
    order_type: str = Field(..., alias="orderType")
    order_type_label: str = Field(..., alias="orderTypeLabel")
    payment_channel: str = Field(..., alias="paymentChannel")
    payment_channel_label: str = Field(..., alias="paymentChannelLabel")
    amount: int
    pay_amount_fen: int = Field(..., alias="payAmountFen")
    plan_id: Optional[str] = Field(None, alias="planId")
    plan_name: Optional[str] = Field(None, alias="planName")
    alipay_trade_no: Optional[str] = Field(None, alias="alipayTradeNo")
    status: str
    status_label: str = Field(..., alias="statusLabel")
    created_at: datetime = Field(..., alias="createdAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")
    # 算力充值：支付单有效期（创建起 24h）；会员已完成则为账期结束
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    expires_label: Optional[str] = Field(None, alias="expiresLabel")


class AdminRechargeOrderListOut(BaseModel):
    """管理端充值/支付订单分页列表。"""

    model_config = ConfigDict(populate_by_name=True)

    items: list[AdminRechargeOrderOut]
    total: int
    page: int
    page_size: int
