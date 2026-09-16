"""管理后台功能权限定义与校验。

超级管理员（is_super_admin）拥有全部权限；普通管理员按 admin_permissions JSON 列表授权。
历史管理员（admin_permissions 为 NULL）兼容视为拥有全部功能权限，但不含「管理员权限设置」。
"""

from __future__ import annotations

from typing import Iterable

# —— 功能权限键（与后台导航/API 一一对应）——
PERM_DASHBOARD = "dashboard"
PERM_USERS = "users"
PERM_PROJECTS = "projects"
PERM_JOBS = "jobs"
PERM_GENERATION_LOGS = "generation_logs"
PERM_MODELS = "models"
PERM_MODEL_PROVIDERS = "model_providers"
PERM_PRICING = "pricing"
PERM_RECHARGE_TIERS = "recharge_tiers"
PERM_RECHARGE_ORDERS = "recharge_orders"
PERM_CREDIT_TRANSACTIONS = "credit_transactions"
PERM_CREDITS = "credits"
PERM_CREDIT_ACTIVITIES = "credit_activities"
PERM_INVITE_CAMPAIGN = "invite_campaign"
PERM_SUBSCRIPTION_PLANS = "subscription_plans"
PERM_STORAGE = "storage"
PERM_PROMPT_TEMPLATES = "prompt_templates"
PERM_ERROR_CODES = "error_codes"
PERM_HOMEPAGE = "homepage"
PERM_MATERIAL_LIBRARY = "material_library"
PERM_SENSITIVE_WORDS = "sensitive_words"
# 给其他管理员分配功能权限（含本权限自身）
PERM_ADMIN_PERMISSIONS = "admin_permissions"

# 目录：权限键 → 中文名 / 分组（前端勾选 UI）
ADMIN_PERMISSION_CATALOG: list[dict[str, str]] = [
    {"key": PERM_DASHBOARD, "label": "仪表盘", "group": "概览"},
    {"key": PERM_USERS, "label": "用户管理", "group": "用户与项目"},
    {"key": PERM_PROJECTS, "label": "项目列表", "group": "用户与项目"},
    {"key": PERM_ADMIN_PERMISSIONS, "label": "管理员权限设置", "group": "用户与项目"},
    {"key": PERM_JOBS, "label": "生成任务", "group": "生成与模型"},
    {"key": PERM_GENERATION_LOGS, "label": "模型调用日志", "group": "生成与模型"},
    {"key": PERM_MODELS, "label": "模型开关", "group": "生成与模型"},
    {"key": PERM_MODEL_PROVIDERS, "label": "供应商密钥", "group": "生成与模型"},
    {"key": PERM_PRICING, "label": "算力价格设置", "group": "生成与模型"},
    {"key": PERM_RECHARGE_TIERS, "label": "充值档位", "group": "算力与计费"},
    {"key": PERM_RECHARGE_ORDERS, "label": "充值记录", "group": "算力与计费"},
    {"key": PERM_CREDIT_TRANSACTIONS, "label": "算力变动记录", "group": "算力与计费"},
    {"key": PERM_CREDITS, "label": "算力对账与调账", "group": "算力与计费"},
    {"key": PERM_CREDIT_ACTIVITIES, "label": "算力活动", "group": "算力与计费"},
    {"key": PERM_INVITE_CAMPAIGN, "label": "邀请活动", "group": "算力与计费"},
    {"key": PERM_SUBSCRIPTION_PLANS, "label": "会员套餐", "group": "算力与计费"},
    {"key": PERM_STORAGE, "label": "内存管理", "group": "平台配置"},
    {"key": PERM_PROMPT_TEMPLATES, "label": "Prompt 模板", "group": "平台配置"},
    {"key": PERM_ERROR_CODES, "label": "错误编码表", "group": "平台配置"},
    {"key": PERM_HOMEPAGE, "label": "登录页/发现页内容", "group": "内容管理"},
    {"key": PERM_MATERIAL_LIBRARY, "label": "素材库（风格/特效/角色/提示词）", "group": "内容管理"},
    {"key": PERM_SENSITIVE_WORDS, "label": "敏感词", "group": "内容管理"},
]

ALL_ADMIN_PERMISSION_KEYS: frozenset[str] = frozenset(
    item["key"] for item in ADMIN_PERMISSION_CATALOG
)

# 历史管理员缺省：除「权限设置」外全部功能
_LEGACY_DEFAULT_PERMISSIONS: frozenset[str] = ALL_ADMIN_PERMISSION_KEYS - {
    PERM_ADMIN_PERMISSIONS
}

# 导航 path 前缀 → 所需权限（前端过滤侧栏）
NAV_PATH_PERMISSION: dict[str, str] = {
    "/admin": PERM_DASHBOARD,
    "/admin/users": PERM_USERS,
    "/admin/register-bonus": PERM_USERS,
    "/admin/projects": PERM_PROJECTS,
    "/admin/admin-permissions": PERM_ADMIN_PERMISSIONS,
    "/admin/jobs": PERM_JOBS,
    "/admin/generation-logs": PERM_GENERATION_LOGS,
    "/admin/models": PERM_MODELS,
    "/admin/model-providers": PERM_MODEL_PROVIDERS,
    "/admin/pricing": PERM_PRICING,
    "/admin/recharge-tiers": PERM_RECHARGE_TIERS,
    "/admin/recharge-orders": PERM_RECHARGE_ORDERS,
    "/admin/credit-transactions": PERM_CREDIT_TRANSACTIONS,
    "/admin/credits": PERM_CREDITS,
    "/admin/credit-activities": PERM_CREDIT_ACTIVITIES,
    "/admin/invite-campaign": PERM_INVITE_CAMPAIGN,
    "/admin/subscription-plans": PERM_SUBSCRIPTION_PLANS,
    "/admin/storage": PERM_STORAGE,
    "/admin/prompt-templates": PERM_PROMPT_TEMPLATES,
    "/admin/error-codes": PERM_ERROR_CODES,
    "/admin/content/homepage": PERM_HOMEPAGE,
    "/admin/content/discover": PERM_HOMEPAGE,
    "/admin/content/material-library": PERM_MATERIAL_LIBRARY,
    "/admin/content/skill-docs": PERM_HOMEPAGE,
    "/admin/skill-docs": PERM_HOMEPAGE,
    "/admin/content/sensitive-words": PERM_SENSITIVE_WORDS,
}


def parse_super_admin_phones(raw: str | None) -> list[str]:
    """解析 SUPER_ADMIN_PHONES 环境变量（逗号分隔）。"""
    if not raw:
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def normalize_permission_list(raw: object) -> list[str] | None:
    """规范化入库权限列表；非法键丢弃。None 表示「未设置/历史兼容」。"""
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = str(item or "").strip()
        if key not in ALL_ADMIN_PERMISSION_KEYS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def resolved_permissions(user) -> list[str]:
    """返回该用户实际生效的权限键列表（超管 = 全部）。"""
    if getattr(user, "role", None) != "admin":
        return []
    if bool(getattr(user, "is_super_admin", False)):
        return sorted(ALL_ADMIN_PERMISSION_KEYS)
    raw = getattr(user, "admin_permissions", None)
    if raw is None:
        return sorted(_LEGACY_DEFAULT_PERMISSIONS)
    return normalize_permission_list(raw) or []


def admin_has_permission(user, permission: str) -> bool:
    """当前管理员是否拥有指定功能权限。"""
    if getattr(user, "role", None) != "admin":
        return False
    if bool(getattr(user, "is_super_admin", False)):
        return True
    return permission in resolved_permissions(user)


def admin_has_any_permission(user, permissions: Iterable[str]) -> bool:
    return any(admin_has_permission(user, p) for p in permissions)
