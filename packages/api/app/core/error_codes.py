"""Canvas platform error code registry — code → 中文说明."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ErrorCodeEntry:
    code: str
    message_zh: str
    category: str
    http_status: int | None = None


def _e(code: str, message_zh: str, category: str, http_status: int | None = None) -> ErrorCodeEntry:
    return ErrorCodeEntry(code=code.upper(), message_zh=message_zh, category=category, http_status=http_status)


class ErrorCode:
    """Stable string error codes — use with fail(ErrorCode.XXX)."""

    OK = "0000"
    UNKNOWN = "UNKNOWN"
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFLICT = "CONFLICT"
    REDIS_UNAVAILABLE = "REDIS_UNAVAILABLE"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    ADMIN_REQUIRED = "ADMIN_REQUIRED"
    ADMIN_PERMISSION_DENIED = "ADMIN_PERMISSION_DENIED"
    SUPER_ADMIN_PROTECTED = "SUPER_ADMIN_PROTECTED"
    REGISTRATION_DISABLED = "REGISTRATION_DISABLED"
    USER_NOT_REGISTERED = "USER_NOT_REGISTERED"
    PHONE_ALREADY_REGISTERED = "PHONE_ALREADY_REGISTERED"
    PHONE_NOT_REGISTERED = "PHONE_NOT_REGISTERED"
    INVALID_PHONE = "INVALID_PHONE"
    INVALID_PASSWORD = "INVALID_PASSWORD"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    LOGIN_SMS_REQUIRED = "LOGIN_SMS_REQUIRED"
    WEAK_PASSWORD = "WEAK_PASSWORD"
    PASSWORD_TOO_SHORT = "PASSWORD_TOO_SHORT"
    SMS_CODE_REQUIRED = "SMS_CODE_REQUIRED"
    INVALID_SMS_SCENE = "INVALID_SMS_SCENE"
    PHONE_LOCKED = "PHONE_LOCKED"
    SMS_COOLDOWN = "SMS_COOLDOWN"
    IP_RATE_LIMITED = "IP_RATE_LIMITED"
    INVALID_CODE = "INVALID_CODE"
    CODE_EXPIRED = "CODE_EXPIRED"
    SMS_LOCKED = "SMS_LOCKED"
    SMS_REDIS_UNAVAILABLE = "SMS_REDIS_UNAVAILABLE"
    SMS_SEND_FAILED = "SMS_SEND_FAILED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_INVALID = "CAPTCHA_INVALID"
    CAPTCHA_EXPIRED = "CAPTCHA_EXPIRED"
    CAPTCHA_TICKET_INVALID = "CAPTCHA_TICKET_INVALID"
    CAPTCHA_LOCKED = "CAPTCHA_LOCKED"
    CAPTCHA_REDIS_UNAVAILABLE = "CAPTCHA_REDIS_UNAVAILABLE"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    SSO_TOKEN_INVALID = "SSO_TOKEN_INVALID"
    INVALID_PROJECT_ID = "INVALID_PROJECT_ID"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PROJECT_CREATE_COOLDOWN = "PROJECT_CREATE_COOLDOWN"
    PROJECT_CREATE_DAILY_LIMIT = "PROJECT_CREATE_DAILY_LIMIT"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_FORBIDDEN = "SKILL_FORBIDDEN"
    SKILL_SAVE_NOT_READY = "SKILL_SAVE_NOT_READY"
    SKILL_PROMPT_INVALID = "SKILL_PROMPT_INVALID"
    SKILL_REVIEW_PENDING = "SKILL_REVIEW_PENDING"
    SKILL_REVIEW_INVALID = "SKILL_REVIEW_INVALID"
    SKILL_PUBLISH_NOT_READY = "SKILL_PUBLISH_NOT_READY"
    AGENT_SESSION_NOT_FOUND = "AGENT_SESSION_NOT_FOUND"
    MEMBER_ALREADY_EXISTS = "MEMBER_ALREADY_EXISTS"
    MEMBER_INVITE_PENDING = "MEMBER_INVITE_PENDING"
    MEMBER_NOT_FOUND = "MEMBER_NOT_FOUND"
    INVITE_NOT_FOUND = "INVITE_NOT_FOUND"
    MEMBER_LIMIT_REACHED = "MEMBER_LIMIT_REACHED"
    CANNOT_INVITE_SELF = "CANNOT_INVITE_SELF"
    CANNOT_INVITE_OWNER = "CANNOT_INVITE_OWNER"
    COLLABORATOR_DAILY_CAP_EXCEEDED = "COLLABORATOR_DAILY_CAP_EXCEEDED"
    COLLABORATOR_TOTAL_CAP_EXCEEDED = "COLLABORATOR_TOTAL_CAP_EXCEEDED"
    GENERATION_NOT_AWAITING_APPROVAL = "GENERATION_NOT_AWAITING_APPROVAL"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    STORAGE_WRITE_FAILED = "STORAGE_WRITE_FAILED"
    PRICING_CHANGED = "PRICING_CHANGED"
    PRICE_NOT_CONFIGURED = "PRICE_NOT_CONFIGURED"
    INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
    CREDIT_RESERVE_FAILED = "CREDIT_RESERVE_FAILED"
    ACTIVITY_CLAIM_LIMIT = "ACTIVITY_CLAIM_LIMIT"
    ACTIVITY_CONFIG_ERROR = "ACTIVITY_CONFIG_ERROR"
    ACTIVITY_NOT_FOUND = "ACTIVITY_NOT_FOUND"
    ACTIVITY_NOT_CLAIMABLE = "ACTIVITY_NOT_CLAIMABLE"
    ACTIVITY_NOT_ELIGIBLE = "ACTIVITY_NOT_ELIGIBLE"
    ACTIVITY_INVALID_SCHEDULE = "ACTIVITY_INVALID_SCHEDULE"
    ACTIVITY_INVALID_CLAIM_RULES = "ACTIVITY_INVALID_CLAIM_RULES"
    CLAIM_IN_PROGRESS = "CLAIM_IN_PROGRESS"
    CLAIM_GRANT_FAILED = "CLAIM_GRANT_FAILED"
    INVITE_CODE_INVALID = "INVITE_CODE_INVALID"
    INVITE_CODE_SELF = "INVITE_CODE_SELF"
    INVITE_REWARD_IN_PROGRESS = "INVITE_REWARD_IN_PROGRESS"
    INVITE_CAMPAIGN_NOT_FOUND = "INVITE_CAMPAIGN_NOT_FOUND"
    INVITE_CAMPAIGN_INVALID_SCHEDULE = "INVITE_CAMPAIGN_INVALID_SCHEDULE"
    INVITE_BINDING_NOT_FOUND = "INVITE_BINDING_NOT_FOUND"
    REGISTER_BONUS_IN_PROGRESS = "REGISTER_BONUS_IN_PROGRESS"
    CREDITS_DISABLED = "CREDITS_DISABLED"
    INVALID_RECHARGE_AMOUNT = "INVALID_RECHARGE_AMOUNT"
    RECHARGE_IN_PROGRESS = "RECHARGE_IN_PROGRESS"
    PAYMENT_NOT_CONFIGURED = "PAYMENT_NOT_CONFIGURED"
    PAYMENT_ORDER_NOT_FOUND = "PAYMENT_ORDER_NOT_FOUND"
    PAYMENT_ORDER_FORBIDDEN = "PAYMENT_ORDER_FORBIDDEN"
    PAYMENT_PRECREATE_FAILED = "PAYMENT_PRECREATE_FAILED"
    PAYMENT_VERIFY_FAILED = "PAYMENT_VERIFY_FAILED"
    DIRECT_RECHARGE_DISABLED = "DIRECT_RECHARGE_DISABLED"
    SUBSCRIPTION_PLAN_NOT_FOUND = "SUBSCRIPTION_PLAN_NOT_FOUND"
    SUBSCRIPTION_PLAN_INACTIVE = "SUBSCRIPTION_PLAN_INACTIVE"
    SUBSCRIPTION_PLAN_IN_USE = "SUBSCRIPTION_PLAN_IN_USE"
    ACTIVITY_IN_USE = "ACTIVITY_IN_USE"
    SUBSCRIPTION_NOT_FOUND = "SUBSCRIPTION_NOT_FOUND"
    SUBSCRIPTION_IN_PROGRESS = "SUBSCRIPTION_IN_PROGRESS"
    CREDIT_ADJUST_IN_PROGRESS = "CREDIT_ADJUST_IN_PROGRESS"
    JOB_ADMIN_IN_PROGRESS = "JOB_ADMIN_IN_PROGRESS"
    RATE_LIMITED = "RATE_LIMITED"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CONTENT_REQUIRED = "CONTENT_REQUIRED"
    PROMPT_SENSITIVE_BLOCKED = "PROMPT_SENSITIVE_BLOCKED"
    SENSITIVE_WORD_EXISTS = "SENSITIVE_WORD_EXISTS"
    SENSITIVE_WORD_NOT_FOUND = "SENSITIVE_WORD_NOT_FOUND"
    GENERATION_FAILED = "GENERATION_FAILED"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    INVALID_JOB_ID = "INVALID_JOB_ID"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    INVALID_ASSET_CATEGORY = "INVALID_ASSET_CATEGORY"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    DOCUMENT_TOO_MANY_PAGES = "DOCUMENT_TOO_MANY_PAGES"
    UPLOAD_RATE_LIMITED = "UPLOAD_RATE_LIMITED"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    COVER_INVALID_TYPE = "COVER_INVALID_TYPE"
    COVER_EMPTY = "COVER_EMPTY"
    VIDEO_REF_INACCESSIBLE = "VIDEO_REF_INACCESSIBLE"
    MULTIMODAL_MODEL_REQUIRED = "MULTIMODAL_MODEL_REQUIRED"
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"
    DIRECTOR_SCENE_NOT_FOUND = "DIRECTOR_SCENE_NOT_FOUND"
    NODE_GROUP_NOT_FOUND = "NODE_GROUP_NOT_FOUND"
    INVALID_STORAGE_KEY = "INVALID_STORAGE_KEY"
    MODEL_BUNDLE_EMPTY = "MODEL_BUNDLE_EMPTY"
    MODEL_BUNDLE_NO_MAIN = "MODEL_BUNDLE_NO_MAIN"
    INVALID_FILENAME = "INVALID_FILENAME"
    INVALID_GENERATION_OPTIONS = "INVALID_GENERATION_OPTIONS"
    QUOTE_BATCH_TOO_LARGE = "QUOTE_BATCH_TOO_LARGE"
    INVALID_CONSUME_PRIORITY = "INVALID_CONSUME_PRIORITY"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    PROVIDER_NOT_FOUND = "PROVIDER_NOT_FOUND"
    PROMPT_TOOL_NOT_FOUND = "PROMPT_TOOL_NOT_FOUND"
    PROMPT_TEMPLATE_NOT_FOUND = "PROMPT_TEMPLATE_NOT_FOUND"
    CANNOT_MODIFY_SELF = "CANNOT_MODIFY_SELF"
    INVALID_ROLE = "INVALID_ROLE"
    NO_DEFAULT_PRESETS = "NO_DEFAULT_PRESETS"
    PLAN_CODE_CONFLICT = "PLAN_CODE_CONFLICT"
    PHONE_REQUIRED = "PHONE_REQUIRED"
    ADMIN_REASON_TOO_SHORT = "ADMIN_REASON_TOO_SHORT"
    INVALID_ADMIN_ACTION = "INVALID_ADMIN_ACTION"
    FORCE_SUCCEED_ASSET_REQUIRED = "FORCE_SUCCEED_ASSET_REQUIRED"
    ASSET_NOT_IN_PROJECT = "ASSET_NOT_IN_PROJECT"
    OUTPUT_LOSS_ACK_REQUIRED = "OUTPUT_LOSS_ACK_REQUIRED"
    JOB_UPSTREAM_NO_TASK_ID = "JOB_UPSTREAM_NO_TASK_ID"
    PROMPT_FIELDS_REQUIRED = "PROMPT_FIELDS_REQUIRED"
    NO_BUNDLED_DEFAULTS = "NO_BUNDLED_DEFAULTS"
    ACCESS_DENIED = "ACCESS_DENIED"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    UPSTREAM_HTTP_ERROR = "UPSTREAM_HTTP_ERROR"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SUBMIT_ERROR = "SUBMIT_ERROR"
    POLL_ERROR = "POLL_ERROR"
    TASK_FAILED = "TASK_FAILED"
    TIMEOUT = "TIMEOUT"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    MISSING_TASK_ID = "MISSING_TASK_ID"
    INVALID_TASK_ID = "INVALID_TASK_ID"
    INVALID_MEDIA = "INVALID_MEDIA"
    INVALID_ROUTE = "INVALID_ROUTE"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    DOWNLOAD_TOO_LARGE = "DOWNLOAD_TOO_LARGE"
    DASHSCOPE_ERROR = "DASHSCOPE_ERROR"
    TTS_ERROR = "TTS_ERROR"
    INVALID_ID = "INVALID_ID"
    NOT_FOUND = "NOT_FOUND"
    INVALID_DATETIME = "INVALID_DATETIME"
    EXPORT_TOO_LARGE = "EXPORT_TOO_LARGE"
    INVALID_PRICING_MODE = "INVALID_PRICING_MODE"
    INVALID_COST_PRICE = "INVALID_COST_PRICE"
    INVALID_CREDIT_TYPE = "INVALID_CREDIT_TYPE"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    STORAGE_QUOTA_EXCEEDED = "STORAGE_QUOTA_EXCEEDED"
    INVALID_STORAGE_QUOTA = "INVALID_STORAGE_QUOTA"
    PUBLICATION_NOT_FOUND = "PUBLICATION_NOT_FOUND"
    PUBLICATION_NOT_PUBLIC = "PUBLICATION_NOT_PUBLIC"
    PUBLICATION_VIDEO_REQUIRED = "PUBLICATION_VIDEO_REQUIRED"
    PUBLICATION_COVER_REQUIRED = "PUBLICATION_COVER_REQUIRED"
    PUBLICATION_CATEGORY_INVALID = "PUBLICATION_CATEGORY_INVALID"
    PUBLICATION_PROJECT_REQUIRED = "PUBLICATION_PROJECT_REQUIRED"
    PUBLICATION_REVIEW_PENDING = "PUBLICATION_REVIEW_PENDING"
    PUBLICATION_REVIEW_INVALID = "PUBLICATION_REVIEW_INVALID"


ERROR_CODE_ENTRIES: tuple[ErrorCodeEntry, ...] = (
    _e("0000", "OK", "meta", 200),
    _e("UNKNOWN", "操作失败，请稍后重试", "meta", 500),
    _e("BAD_REQUEST", "请求无效", "meta", 400),
    _e("VALIDATION_ERROR", "请求参数无效", "meta", 422),
    _e("INTERNAL_ERROR", "服务器内部错误", "meta", 500),
    _e("SERVICE_UNAVAILABLE", "服务暂不可用，请稍后重试", "meta", 503),
    _e("CONFLICT", "操作冲突，请稍后重试", "meta", 409),
    _e("REDIS_UNAVAILABLE", "缓存服务暂不可用，请稍后重试", "meta", 503),
    _e("UNAUTHORIZED", "未登录或登录已失效，请重新登录", "auth", 401),
    _e("FORBIDDEN", "无权访问该资源", "auth", 403),
    _e("ADMIN_REQUIRED", "需要管理员权限", "auth", 403),
    _e("ADMIN_PERMISSION_DENIED", "当前账号缺少该后台功能权限", "auth", 403),
    _e("SUPER_ADMIN_PROTECTED", "不能修改超级管理员的角色、状态或权限", "admin", 403),
    _e("REGISTRATION_DISABLED", "当前已关闭注册", "auth", 403),
    _e("USER_NOT_REGISTERED", "该手机号尚未在聚梦画布注册，请先注册", "auth", 403),
    _e("PHONE_ALREADY_REGISTERED", "该手机号已注册，请直接登录", "auth", 409),
    _e("PHONE_NOT_REGISTERED", "该手机号尚未注册", "auth", 404),
    _e("INVALID_PHONE", "请输入有效的 11 位手机号", "auth", 422),
    _e("INVALID_PASSWORD", "手机号或密码错误", "auth", 401),
    _e("LOGIN_LOCKED", "密码错误次数过多，请稍后再试", "auth", 403),
    _e("LOGIN_SMS_REQUIRED", "检测到异地登录，请输入短信验证码完成验证", "auth", 401),
    _e("WEAK_PASSWORD", "密码至少 8 位，且必须包含字母、数字和特殊字符", "auth", 422),
    _e("PASSWORD_TOO_SHORT", "密码至少 6 位", "auth", 400),
    _e("SMS_CODE_REQUIRED", "请先获取并填写短信验证码", "auth", 422),
    _e("INVALID_SMS_SCENE", "无效的验证码场景", "auth", 400),
    _e("PHONE_LOCKED", "请求过于频繁，请稍后再试", "auth", 429),
    _e("SMS_COOLDOWN", "验证码发送过于频繁，请稍后再试", "auth", 429),
    _e("IP_RATE_LIMITED", "请求过于频繁，请稍后再试", "auth", 429),
    _e("INVALID_CODE", "验证码错误", "auth", 400),
    _e("CODE_EXPIRED", "验证码已过期", "auth", 400),
    _e("SMS_LOCKED", "验证码错误次数过多，请稍后再试", "auth", 403),
    _e("SMS_REDIS_UNAVAILABLE", "短信验证码服务暂不可用，请稍后重试", "auth", 503),
    _e("SMS_SEND_FAILED", "短信发送失败，请稍后再试", "auth", 503),
    _e("CAPTCHA_REQUIRED", "请先完成滑动验证", "auth", 400),
    _e("CAPTCHA_INVALID", "滑动验证未通过，请重试", "auth", 400),
    _e("CAPTCHA_EXPIRED", "验证码已过期，请刷新后重试", "auth", 400),
    _e("CAPTCHA_TICKET_INVALID", "验证凭证无效或已使用，请重新验证", "auth", 400),
    _e("CAPTCHA_LOCKED", "验证错误次数过多，请稍后再试", "auth", 423),
    _e("CAPTCHA_REDIS_UNAVAILABLE", "验证码服务暂不可用，请稍后重试", "auth", 503),
    _e("ACCOUNT_DISABLED", "账号已被禁用，请联系管理员", "auth", 403),
    _e("SSO_TOKEN_INVALID", "SSO 登录凭证无效或已过期", "auth", 401),
    _e("INVALID_PROJECT_ID", "项目 ID 无效", "project", 400),
    _e("PROJECT_NOT_FOUND", "项目不存在或无权访问", "project", 404),
    _e("PROJECT_CREATE_COOLDOWN", "新建过快，请稍后再试", "project", 429),
    _e("PROJECT_CREATE_DAILY_LIMIT", "今日新建项目已达上限", "project", 429),
    _e("SKILL_NOT_FOUND", "Skill 不存在或已下架", "agent", 404),
    _e("SKILL_FORBIDDEN", "无权操作该 Skill", "agent", 403),
    _e("SKILL_SAVE_NOT_READY", "会话尚未完成，无法另存为 Skill", "agent", 409),
    _e("SKILL_PROMPT_INVALID", "请用一句话描述想创建的 Skill", "agent", 422),
    _e("SKILL_REVIEW_PENDING", "Skill 已在审核中，请勿重复提交", "agent", 409),
    _e("SKILL_REVIEW_INVALID", "当前审核状态不允许该操作", "agent", 409),
    _e("SKILL_PUBLISH_NOT_READY", "请先完善 Skill 说明文档后再发布", "agent", 422),
    _e("AGENT_SESSION_NOT_FOUND", "Agent 会话不存在", "agent", 404),
    _e("MEMBER_ALREADY_EXISTS", "该用户已是协作成员", "project", 409),
    _e("MEMBER_INVITE_PENDING", "已向该用户发送邀请，等待对方接受", "project", 409),
    _e("INVITE_NOT_FOUND", "协作邀请不存在或已处理", "project", 404),
    _e("MEMBER_NOT_FOUND", "协作成员不存在", "project", 404),
    _e("MEMBER_LIMIT_REACHED", "协作成员已达上限", "project", 409),
    _e("CANNOT_INVITE_SELF", "不能邀请自己", "project", 400),
    _e("CANNOT_INVITE_OWNER", "项目创建者无需邀请", "project", 400),
    _e("COLLABORATOR_DAILY_CAP_EXCEEDED", "已超过本项目今日协作消耗上限", "project", 402),
    _e("COLLABORATOR_TOTAL_CAP_EXCEEDED", "已超过本项目协作消耗总上限", "project", 402),
    _e("GENERATION_NOT_AWAITING_APPROVAL", "该生成任务不在待审批状态", "project", 409),
    _e("WORKFLOW_NOT_FOUND", "工作流不存在", "project", 404),
    _e("REVISION_CONFLICT", "工作流已被其他会话更新，请刷新后重试", "project", 409),
    _e("STORAGE_WRITE_FAILED", "工作流或媒体存储失败", "project", 502),
    _e("PRICING_CHANGED", "价格已更新，请确认后重试", "credits", 409),
    _e("PRICE_NOT_CONFIGURED", "未设置价格，请联系管理员设置该功能的算力价格", "credits", 400),
    _e("INSUFFICIENT_CREDITS", "算力不足，请充值后再试", "credits", 402),
    _e("CREDIT_RESERVE_FAILED", "算力预扣失败，请稍后重试", "credits", 402),
    _e("ACTIVITY_CLAIM_LIMIT", "已领取过", "credits", 409),
    _e("ACTIVITY_CONFIG_ERROR", "活动配置错误：缺少模型", "credits", 500),
    _e("ACTIVITY_NOT_FOUND", "活动不存在", "credits", 404),
    _e("ACTIVITY_NOT_CLAIMABLE", "活动当前不可领取", "credits", 400),
    _e("ACTIVITY_NOT_ELIGIBLE", "未满足活动领取条件", "credits", 403),
    _e("ACTIVITY_INVALID_SCHEDULE", "活动结束时间须晚于开始时间", "credits", 400),
    _e("ACTIVITY_INVALID_CLAIM_RULES", "活动领取条件配置无效", "credits", 400),
    _e("CLAIM_IN_PROGRESS", "领取处理中", "credits", 409),
    _e("CLAIM_GRANT_FAILED", "领取记录已创建但算力入账失败，请联系客服", "credits", 500),
    _e("INVITE_CODE_INVALID", "邀请码无效", "credits", 400),
    _e("INVITE_CODE_SELF", "不能使用自己的邀请码", "credits", 400),
    _e("INVITE_REWARD_IN_PROGRESS", "邀请奖励处理中，请稍后再试", "credits", 409),
    _e("INVITE_CAMPAIGN_NOT_FOUND", "邀请活动不存在", "credits", 404),
    _e("INVITE_CAMPAIGN_INVALID_SCHEDULE", "邀请活动结束时间须晚于开始时间", "credits", 400),
    _e("INVITE_BINDING_NOT_FOUND", "邀请记录不存在", "credits", 404),
    _e("REGISTER_BONUS_IN_PROGRESS", "注册赠送处理中，请稍后再试", "credits", 409),
    _e("CREDITS_DISABLED", "算力系统未启用", "credits", 503),
    _e("INVALID_RECHARGE_AMOUNT", "充值金额无效", "credits", 400),
    _e("RECHARGE_IN_PROGRESS", "充值处理中，请稍后再试", "credits", 409),
    _e("PAYMENT_NOT_CONFIGURED", "在线支付暂不可用，请稍后再试", "credits", 503),
    _e("PAYMENT_ORDER_NOT_FOUND", "支付订单不存在", "credits", 404),
    _e("PAYMENT_ORDER_FORBIDDEN", "无权查看该支付订单", "credits", 403),
    _e("PAYMENT_PRECREATE_FAILED", "创建支付订单失败", "credits", 502),
    _e("PAYMENT_VERIFY_FAILED", "支付结果校验失败", "credits", 400),
    _e("DIRECT_RECHARGE_DISABLED", "请使用支付宝扫码充值", "credits", 403),
    _e("SUBSCRIPTION_PLAN_NOT_FOUND", "会员套餐不存在", "credits", 404),
    _e("SUBSCRIPTION_PLAN_INACTIVE", "该会员套餐已下架", "credits", 400),
    _e("SUBSCRIPTION_PLAN_IN_USE", "该套餐已有用户订阅记录，不能删除，请改为下架", "credits", 409),
    _e("ACTIVITY_IN_USE", "该活动已有用户领取记录，不能删除，请改为结束", "credits", 409),
    _e("SUBSCRIPTION_NOT_FOUND", "当前没有有效会员订阅", "credits", 404),
    _e("SUBSCRIPTION_IN_PROGRESS", "订阅处理中，请稍后再试", "credits", 409),
    _e("CREDIT_ADJUST_IN_PROGRESS", "算力调整处理中，请稍后再试", "credits", 409),
    _e("JOB_ADMIN_IN_PROGRESS", "该任务正在处理中，请稍后再试", "credits", 409),
    _e("RATE_LIMITED", "生成请求过于频繁，请稍后再试", "generation", 429),
    _e("MODEL_UNAVAILABLE", "所选模型不可用", "generation", 400),
    _e("CONTENT_REQUIRED", "请填写生成内容", "generation", 400),
    _e("PROMPT_SENSITIVE_BLOCKED", "提示词包含违规内容，请修改后重试", "generation", 400),
    _e("SENSITIVE_WORD_EXISTS", "该敏感词已存在", "admin", 409),
    _e("SENSITIVE_WORD_NOT_FOUND", "敏感词不存在", "admin", 404),
    _e("GENERATION_FAILED", "生成任务失败", "generation", 500),
    _e("JOB_NOT_FOUND", "生成任务不存在", "generation", 404),
    _e("INVALID_JOB_ID", "任务 ID 无效", "generation", 400),
    _e("ASSET_NOT_FOUND", "资产不存在", "storage", 404),
    _e("INVALID_ASSET_CATEGORY", "无效的资产分类", "storage", 400),
    _e("INVALID_FILE_TYPE", "不允许的文件类型", "storage", 400),
    _e("DOCUMENT_TOO_MANY_PAGES", "文档页数超过上限（最多 50 页）", "storage", 400),
    _e("UPLOAD_RATE_LIMITED", "上传过于频繁，请稍后再试", "storage", 429),
    _e("BATCH_TOO_LARGE", "批量查询条数超过上限", "storage", 400),
    _e("COVER_INVALID_TYPE", "仅支持 JPG、PNG、GIF、WebP、BMP 封面图片", "project", 400),
    _e("COVER_EMPTY", "封面文件为空", "project", 400),
    _e("VIDEO_REF_INACCESSIBLE", "参考视频无法访问，请确认 OSS 已配置且视频已上传到项目素材库", "generation", 400),
    _e("MULTIMODAL_MODEL_REQUIRED", "参考图片/视频生文需要使用支持多模态的豆包 Pro 模型", "generation", 400),
    _e("LLM_REQUEST_FAILED", "大模型请求失败", "upstream", 502),
    _e("DIRECTOR_SCENE_NOT_FOUND", "导演场景不存在", "project", 404),
    _e("NODE_GROUP_NOT_FOUND", "节点组不存在", "project", 404),
    _e("INVALID_STORAGE_KEY", "存储路径无效", "storage", 400),
    _e("MODEL_BUNDLE_EMPTY", "至少上传一个模型文件", "storage", 400),
    _e("MODEL_BUNDLE_NO_MAIN", "压缩包中需要包含 .gltf 或 .glb 主文件", "storage", 400),
    _e("INVALID_FILENAME", "文件名无效", "storage", 400),
    _e("INVALID_GENERATION_OPTIONS", "生成选项 JSON 无效", "credits", 400),
    _e("QUOTE_BATCH_TOO_LARGE", "批量报价最多 50 项", "credits", 400),
    _e("INVALID_CONSUME_PRIORITY", "算力消耗顺序配置无效", "credits", 400),
    _e("MODEL_NOT_FOUND", "模型不存在", "admin", 404),
    _e("PROVIDER_NOT_FOUND", "供应商不存在", "admin", 404),
    _e("PROMPT_TOOL_NOT_FOUND", "提示词工具不存在", "admin", 404),
    _e("PROMPT_TEMPLATE_NOT_FOUND", "提示词模板不存在", "admin", 404),
    _e("CANNOT_MODIFY_SELF", "不能修改自己的管理员角色或禁用自己", "admin", 400),
    _e("INVALID_ROLE", "角色必须是 user 或 admin", "admin", 400),
    _e("NO_DEFAULT_PRESETS", "该模型分类无默认预设", "admin", 400),
    _e("PLAN_CODE_CONFLICT", "会员套餐 code 已存在", "admin", 409),
    _e("PHONE_REQUIRED", "用户无手机号，无法清除风控状态", "admin", 400),
    _e("ADMIN_REASON_TOO_SHORT", "操作说明至少 10 个字符", "admin", 400),
    _e("INVALID_ADMIN_ACTION", "不支持的管理操作", "admin", 400),
    _e("FORCE_SUCCEED_ASSET_REQUIRED", "标为成功必须提供 assetId", "admin", 400),
    _e("ASSET_NOT_IN_PROJECT", "assetId 不属于该项目或不存在", "admin", 400),
    _e("OUTPUT_LOSS_ACK_REQUIRED", "任务已有输出产物，强制失败需确认", "admin", 400),
    _e("JOB_UPSTREAM_NO_TASK_ID", "任务无上游 task id，无法同步", "admin", 400),
    _e("PROMPT_FIELDS_REQUIRED", "tool、category 和 key 为必填项", "admin", 400),
    _e("NO_BUNDLED_DEFAULTS", "该工具无内置默认模板", "admin", 404),
    _e("ACCESS_DENIED", "无权访问该文件", "storage", 403),
    _e("OBJECT_NOT_FOUND", "文件不存在", "storage", 404),
    _e("UPSTREAM_ERROR", "上游 AI 服务异常", "upstream", 502),
    _e("UPSTREAM_HTTP_ERROR", "上游服务 HTTP 请求失败", "upstream", 502),
    _e("NOT_CONFIGURED", "上游 API 密钥未配置", "upstream", 500),
    _e("SUBMIT_ERROR", "上游任务提交失败", "upstream", 502),
    _e("POLL_ERROR", "上游任务状态查询失败", "upstream", 502),
    _e("TASK_FAILED", "上游生成任务失败", "upstream", 502),
    _e("TIMEOUT", "上游生成任务超时", "upstream", 504),
    _e("GATEWAY_TIMEOUT", "上游网关超时，任务可能仍在处理，请联系管理员核对后再提交", "upstream", 504),
    _e("EMPTY_OUTPUT", "任务完成但未返回有效结果", "upstream", 502),
    _e("MISSING_TASK_ID", "上游未返回任务 ID", "upstream", 502),
    _e("INVALID_TASK_ID", "上游任务 ID 无效", "upstream", 400),
    _e("INVALID_MEDIA", "参考图/音视频无效或不可访问", "upstream", 400),
    _e("INVALID_ROUTE", "未知的上游生成路由", "upstream", 400),
    _e("DOWNLOAD_FAILED", "媒体文件下载失败", "upstream", 502),
    _e("DOWNLOAD_TOO_LARGE", "媒体文件过大", "upstream", 413),
    _e("DASHSCOPE_ERROR", "百炼/万相 API 返回错误", "upstream", 502),
    _e("TTS_ERROR", "语音合成失败", "upstream", 502),
    _e("INVALID_ID", "ID 格式无效", "admin", 400),
    _e("NOT_FOUND", "资源不存在", "admin", 404),
    _e("INVALID_DATETIME", "日期时间筛选格式无效", "admin", 400),
    _e("EXPORT_TOO_LARGE", "导出条数超过上限，请缩小筛选范围", "admin", 400),
    _e("INVALID_PRICING_MODE", "计价模式无效", "admin", 400),
    _e("INVALID_COST_PRICE", "成本价格式无效", "admin", 400),
    _e("INVALID_CREDIT_TYPE", "算力类型无效", "admin", 400),
    _e("USER_NOT_FOUND", "用户不存在", "admin", 404),
    _e("STORAGE_QUOTA_EXCEEDED", "云存储空间不足，请删除素材或开通会员", "storage", 402),
    _e("INVALID_STORAGE_QUOTA", "存储配额配置无效", "storage", 422),
    _e("PUBLICATION_NOT_FOUND", "发布的工作流不存在", "project", 404),
    _e("PUBLICATION_NOT_PUBLIC", "该工作流未公开，无法查看或复制", "project", 403),
    _e("PUBLICATION_VIDEO_REQUIRED", "请选择关联项目中的视频素材", "project", 400),
    _e("PUBLICATION_COVER_REQUIRED", "请选择关联项目中的封面图片", "project", 400),
    _e("PUBLICATION_CATEGORY_INVALID", "请选择有效的分类", "project", 400),
    _e("PUBLICATION_PROJECT_REQUIRED", "请选择要关联的项目", "project", 400),
    _e("PUBLICATION_REVIEW_PENDING", "工作流已在审核中", "project", 409),
    _e("PUBLICATION_REVIEW_INVALID", "当前审核状态不允许该操作", "project", 409),
)

ERROR_CODE_MAP: dict[str, ErrorCodeEntry] = {entry.code: entry for entry in ERROR_CODE_ENTRIES}


def normalize_error_code(code: str | None) -> str | None:
    if not code or not str(code).strip():
        return None
    return str(code).strip().upper().replace("-", "_")


def get_error_entry(code: str | None) -> ErrorCodeEntry | None:
    normalized = normalize_error_code(code)
    if not normalized:
        return None
    return ERROR_CODE_MAP.get(normalized)


def get_error_message_zh(code: str | None, *, fallback: str | None = None) -> str | None:
    entry = get_error_entry(code)
    if entry:
        return entry.message_zh
    return fallback


def format_upstream_job_error_message(*, code: str | None, message: str | None) -> str:
    """Prefer upstream detail over generic catalog text when recording job failures."""
    detail = (message or "").strip()
    if detail:
        return detail[:2000]
    normalized = normalize_error_code(code)
    return (get_error_message_zh(normalized) or "上游错误")[:2000]


def resolve_error_message(
    detail: Any,
    *,
    fallback: str | None = None,
) -> str:
    """Resolve user-facing Chinese message from API error detail."""
    if isinstance(detail, dict):
        code = detail.get("code")
        msg = detail.get("message")
        mapped = get_error_message_zh(str(code) if code else None, fallback=str(msg) if msg else None)
        if mapped:
            return mapped
        if msg:
            return str(msg)
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if fallback:
        return fallback
    return "操作失败，请稍后重试"


def api_error_detail(
    code: str,
    *,
    message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build structured error payload (legacy HTTPException detail helper)."""
    entry = get_error_entry(code)
    normalized = normalize_error_code(code) or "UNKNOWN"
    payload: dict[str, Any] = {
        "code": normalized,
        "message": message or (entry.message_zh if entry else message) or "操作失败",
    }
    payload.update(extra)
    return payload


def list_error_codes() -> list[dict[str, Any]]:
    return [
        {
            "code": entry.code,
            "messageZh": entry.message_zh,
            "category": entry.category,
            "httpStatus": entry.http_status,
        }
        for entry in ERROR_CODE_ENTRIES
    ]


__all__ = [
    "ERROR_CODE_ENTRIES",
    "ERROR_CODE_MAP",
    "ErrorCode",
    "ErrorCodeEntry",
    "api_error_detail",
    "format_upstream_job_error_message",
    "get_error_entry",
    "get_error_message_zh",
    "list_error_codes",
    "normalize_error_code",
    "resolve_error_message",
]
