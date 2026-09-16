from pydantic import BaseModel, ConfigDict, Field
from typing import Optional
from datetime import datetime

class SsoLoginRequest(BaseModel):
    token: str

class AuthConfigOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    registration_enabled: bool = Field(..., alias="registrationEnabled")
    captcha_enabled: bool = Field(True, alias="captchaEnabled")
    captcha_password_after_failures: int = Field(2, alias="captchaPasswordAfterFailures")

class RegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phone: str
    password: str
    code: Optional[str] = None
    display_name: Optional[str] = None
    # 可选：用户邀请码（6 位）
    invite_code: Optional[str] = Field(None, alias="inviteCode")

class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phone: str
    password: str
    sms_code: Optional[str] = Field(None, alias="smsCode")
    captcha_ticket: Optional[str] = Field(None, alias="captchaTicket")

class SmsCodeSendRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    phone: str
    scene: str = "user_login"
    captcha_ticket: Optional[str] = Field(None, alias="captchaTicket")

class SmsCodeSendResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    masked_phone: str = Field(..., alias="maskedPhone")
    code: str = ""
    scene: str

class CaptchaChallengeOut(BaseModel):
    """滑动拼图挑战：背景图 + 拼图块 + 尺寸元数据。"""

    model_config = ConfigDict(populate_by_name=True)

    captcha_id: str = Field(..., alias="captchaId")
    background_image: str = Field("", alias="backgroundImage")
    slider_image: str = Field("", alias="sliderImage")
    puzzle_y: int = Field(0, alias="puzzleY")
    image_width: int = Field(320, alias="imageWidth")
    image_height: int = Field(168, alias="imageHeight")
    slider_size: int = Field(48, alias="sliderSize")
    theme_id: str = Field("", alias="themeId")
    theme_accent: str = Field("#60a5fa", alias="themeAccent")
    expires_in: int = Field(..., alias="expiresIn")
    # 兼容旧字段；新前端用 backgroundImage
    image_base64: str = Field("", alias="imageBase64")
    # 开发模式回显目标 X（像素）
    answer: str = ""

class CaptchaVerifyRequest(BaseModel):
    """滑动验证：提交拼图块水平偏移 slideX。"""

    model_config = ConfigDict(populate_by_name=True)

    captcha_id: str = Field(..., alias="captchaId")
    slide_x: Optional[float] = Field(None, alias="slideX")
    # 兼容旧图形码字段（可传偏移字符串）
    captcha_code: Optional[str] = Field(None, alias="captchaCode")
    phone: str = ""
    scene: str = "user_login"

class CaptchaVerifyOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    captcha_ticket: str = Field(..., alias="captchaTicket")
    expires_in: int = Field(..., alias="expiresIn")

class CaptchaStatusOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    captcha_enabled: bool = Field(..., alias="captchaEnabled")
    required_for_password_login: bool = Field(..., alias="requiredForPasswordLogin")
    fail_count: int = Field(0, alias="failCount")
    threshold: int = 2

class LoginSmsRequest(BaseModel):
    phone: str
    code: str

class AdminLoginRequest(BaseModel):
    """管理后台登录：手机号 + 密码 + 短信验证码。"""

    model_config = ConfigDict(populate_by_name=True)

    phone: str
    password: str
    sms_code: Optional[str] = Field(None, alias="smsCode")
    code: Optional[str] = None
    captcha_ticket: Optional[str] = Field(None, alias="captchaTicket")

class ResetPasswordRequest(BaseModel):
    phone: str
    code: str
    password: str

class UserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_no: Optional[str] = Field(None, alias="userNo")
    phone: Optional[str] = None
    display_name: str
    avatar_url: Optional[str] = None
    compute_power: Optional[float] = Field(None, alias="computePower")
    credits_enabled: bool = Field(False, alias="creditsEnabled")

class SsoLoginResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_token: str
    user: UserOut
    # 注册携带邀请码时返回发奖摘要（登录等其它入口为 None）
    invite_reward: Optional[dict] = Field(None, alias="inviteReward")
    register_bonus: Optional[dict] = Field(None, alias="registerBonus")

class ProjectIn(BaseModel):
    title: str
    description: Optional[str] = None

class ProjectUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = Field(None, alias="coverUrl")

class ProjectOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    project_no: str
    title: str
    description: Optional[str] = None
    cover_url: Optional[str] = None
    workflow_count: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    role: Optional[str] = None
    owner_id: Optional[str] = Field(None, alias="ownerId")
    owner_display_name: Optional[str] = Field(None, alias="ownerDisplayName")


class ProjectBatchIdsIn(BaseModel):
    """批量移入回收站 / 恢复 / 永久删除的项目 ID 列表。"""

    model_config = ConfigDict(populate_by_name=True)

    project_ids: list[str] = Field(..., alias="projectIds", min_length=1, max_length=50)


class ProjectBatchResultOut(BaseModel):
    """批量操作结果。"""

    model_config = ConfigDict(populate_by_name=True)

    ok_count: int = Field(..., alias="okCount")
    failed_ids: list[str] = Field(default_factory=list, alias="failedIds")


class ProjectMemberOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userId")
    display_name: str = Field("", alias="displayName")
    phone_masked: str = Field("", alias="phoneMasked")
    status: str = "active"
    invited_at: Optional[datetime] = Field(None, alias="invitedAt")
    first_accessed_at: Optional[datetime] = Field(None, alias="firstAccessedAt")
    last_accessed_at: Optional[datetime] = Field(None, alias="lastAccessedAt")


class ProjectInviteOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(..., alias="projectId")
    project_no: Optional[str] = Field(None, alias="projectNo")
    title: str = ""
    cover_url: Optional[str] = Field(None, alias="coverUrl")
    inviter_display_name: str = Field("", alias="inviterDisplayName")
    invited_at: Optional[datetime] = Field(None, alias="invitedAt")


class ProjectMemberLookupOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userId")
    display_name: str = Field("", alias="displayName")
    phone_masked: str = Field("", alias="phoneMasked")


class ProjectMemberInviteIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userId")


class ProjectBillingBalanceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    credits_enabled: bool = Field(..., alias="creditsEnabled")
    balance: Optional[float] = None
    available_for_model: Optional[float] = Field(None, alias="availableForModel")
    owner_display_name: str = Field("", alias="ownerDisplayName")
    is_collaborator: bool = Field(False, alias="isCollaborator")


class ProjectPresenceUserOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userId")
    display_name: str = Field("", alias="displayName")


class ProjectPresenceOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    users: list[ProjectPresenceUserOut] = Field(default_factory=list)


class ProjectCollaborationSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    collaborator_daily_cap: Optional[float] = Field(None, alias="collaboratorDailyCap")
    collaborator_total_cap: Optional[float] = Field(None, alias="collaboratorTotalCap")
    collaborator_approval_threshold: Optional[float] = Field(None, alias="collaboratorApprovalThreshold")
    daily_spent: float = Field(0, alias="dailySpent")
    total_spent: float = Field(0, alias="totalSpent")
    pending_approval_count: int = Field(0, alias="pendingApprovalCount")


class ProjectCollaborationSettingsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    collaborator_daily_cap: Optional[float] = Field(None, alias="collaboratorDailyCap")
    collaborator_total_cap: Optional[float] = Field(None, alias="collaboratorTotalCap")
    collaborator_approval_threshold: Optional[float] = Field(None, alias="collaboratorApprovalThreshold")


class ProjectPendingApprovalOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId")
    node_id: Optional[str] = Field(None, alias="nodeId")
    actor_user_id: Optional[str] = Field(None, alias="actorUserId")
    actor_display_name: str = Field("", alias="actorDisplayName")
    model: Optional[str] = None
    model_display_name: Optional[str] = Field(None, alias="modelDisplayName")
    category: Optional[str] = None
    credit_cost: float = Field(0, alias="creditCost")
    created_at: Optional[datetime] = Field(None, alias="createdAt")

class WorkflowIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str
    title: str
    flow_json: str
    workflow_id: Optional[str] = None
    expected_revision: Optional[int] = Field(None, alias="expectedRevision")

class WorkflowOut(BaseModel):
    id: str
    project_id: str
    title: str
    description: Optional[str] = None
    flow_json: str
    version: str
    revision: int = 1
    node_count: int
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class ExecuteResponse(BaseModel):
    job_ids: list[str]

class JobStatusOut(BaseModel):
    id: int
    status: str
    asset_id: Optional[str] = None
    output_assets: Optional[list] = None
    error_message: Optional[str] = None
    request_id: Optional[str] = None
    scene: Optional[str] = None
    provider: Optional[str] = None
    provider_task_id: Optional[str] = None
    result_url: Optional[str] = None
    result_text: Optional[str] = None
