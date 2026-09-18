import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


def _settings_env_file() -> str | Path | None:
    """Production uses /etc/jumeng-canvas/*.env via systemd; skip bundled packages/api/.env."""
    if os.getenv("JUMENG_CANVAS_USE_DOTENV") == "1":
        return ".env"
    if Path("/etc/jumeng-canvas/api.env").is_file() or Path("/etc/jumeng-canvas/worker.env").is_file():
        return None
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_settings_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "jumeng-canvas-api"
    debug: bool = False

    database_url: str = "mysql+aiomysql://jumeng_canvas:change-me@127.0.0.1:3306/jumeng_canvas"
    redis_url: str = "redis://localhost:6380/0"

    jwt_secret: str = "change-me-shared-secret-with-main-site"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    canvas_session_secret: str = ""
    canvas_session_ttl_seconds: int = 30 * 24 * 3600
    login_max_failures: int = 6
    login_lock_seconds: int = 20 * 60
    # 异地登录（陌生 IP）要求短信验证码二次校验；判定基于历史成功登录 IP（MySQL 权威）
    login_new_ip_sms_required: bool = True
    # 密码登录失败达到阈值后要求滑动验证码（管理后台发短信始终要求）
    captcha_enabled: bool = True
    captcha_ttl_seconds: int = 120
    captcha_ticket_ttl_seconds: int = 120
    captcha_length: int = 4  # 历史字段，滑动验证码不再使用
    captcha_slide_tolerance_px: int = 6  # 滑动拼图水平容差（像素）
    captcha_password_after_failures: int = 2
    captcha_debug_return_answer: bool = True

    registration_enabled: bool = True
    sms_registration_required: bool = True
    sms_send_cooldown_seconds: int = 60
    sms_code_ttl_seconds: int = 300
    sms_debug_return_code: bool = True
    sms_force_local_debug: bool = True
    sms_fallback_on_provider_error: bool = True
    alibaba_cloud_access_key_id: str = ""
    alibaba_cloud_access_key_secret: str = ""
    alibaba_cloud_region_id: str = "cn-hangzhou"
    sms_sign_name: str = ""
    sms_template_code: str = ""
    sms_template_param_key: str = "code"

    main_site_api_url: str = "https://legacy-v.example.com"
    main_site_internal_token: str = ""

    credits_enabled: bool = True
    default_user_credits: int = 0

    alipay_mode: str = ""
    alipay_app_id: str = ""
    alipay_private_key: str = ""
    alipay_public_key: str = ""
    alipay_gateway: str = ""
    alipay_notify_url: str = ""

    # 分类文本日志根目录（相对 packages/api 或绝对路径）；payment 等写入 logs/{category}/YYYYMMDD.log
    file_log_base_dir: str = "logs"

    comfyui_base_url: str = "http://localhost:8188"
    # 可选：本机权重根目录提示（画布不直接读盘，只走 HTTP）
    local_model_root: str = ""

    oss_endpoint: str = ""
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_object_prefix: str = "Ihuabu"

    canvas_storage_local_only: bool = False

    storage_url_mode: str = "proxy"
    oss_cdn_base_url: str = ""
    oss_signed_url_ttl_seconds: int = 86400

    worker_enabled: bool = False
    worker_lanes: str = "image"
    worker_burst_limit: int = 4
    worker_parallelism: int = 0
    worker_burst_sleep_s: float = 0.05
    worker_stale_lock_s: float = 1800.0
    worker_job_timeout_s: float = 2100.0

    model_concurrency_default: int = 1
    model_concurrency_default_image: int = 22
    model_concurrency_default_video: int = 8
    model_concurrency_default_audio: int = 8
    model_concurrency_default_text: int = 12
    model_concurrency_retry_after_s: float = 5.0
    worker_idle_sleep_s: float = 0.1

    credit_reconcile_enabled: bool = False

    db_pool_size: int = 8
    db_max_overflow: int = 8

    text_async_enabled: bool = True

    ai_mock_enabled: bool = True
    ai_mock_min_delay_s: float = 0.3
    ai_mock_max_delay_s: float = 1.5
    ai_mock_fail_rate: float = 0.0

    admin_auth_disabled: bool = True

    # 超级管理员手机号（逗号分隔）；启动时自动升为 admin + is_super_admin。默认空，由环境变量注入。
    super_admin_phones: str = ""

    dev_admin_password: str = ""

    media_async_enabled: bool = True

    api_workers: int = 2

    # P3：模型目录与密钥默认仅读 DB；本地开发可设 MODEL_CATALOG_SOURCE=dual / LLM_KEYS_SOURCE=dual
    model_catalog_source: str = "db"
    llm_keys_source: str = "db"
    llm_keys_fallback: bool = False
    # 供应商 API Key 加密主密钥；空则开发环境回退 jwt_secret
    credential_encryption_key: str = ""

    # 对外绝对地址前缀（Agent projectUrl 等）；生产唯一日常入口 https://www.example.com
    canvas_public_origin: str = ""
    # Web basePath：生产主站 www.example.com 为空；遗留 legacy-v.example.com/canvas 冻结停更；测试服可为 /canvas
    canvas_web_base_path: str = ""

@lru_cache
def get_settings() -> Settings:
    return Settings()
