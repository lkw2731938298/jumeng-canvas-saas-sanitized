"""算力活动领取资格：注册时间窗 / 累计充值 / 有效会员。

claim_rules（camelCase，存 credit_activities.claim_rules JSON）示例：
{
  "registeredFrom": "2026-07-01T00:00:00+08:00",
  "registeredTo": "2026-07-31T23:59:59+08:00",
  "minRechargeFen": 10000,          # 累计实付分（仅 order_type=recharge 且 completed）
  "minRechargeCredits": 500,        # 累计入账算力点（同上口径）
  "requireActiveMember": true       # 须当前有效会员
}
条件之间为 AND；空对象 / null = 无额外限制。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import as_cst_aware, parse_query_datetime, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.credit import CreditRechargeOrder
from ..models.credit_activity import CreditActivity
from ..models.user import User
from .subscriptions import get_user_active_subscription

# 充值口径：仅统计用户付费充值单（不含会员开通单）
_RECHARGE_ORDER_TYPE = "recharge"
_RECHARGE_COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class UserClaimProfile:
    """用户领取资格画像（列表/领取共用，避免 N+1）。"""

    registered_at: datetime | None
    recharge_fen: int
    recharge_credits: int
    is_active_member: bool


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    """单次资格判定结果。"""

    eligible: bool
    reasons: list[str]


def _empty_rules() -> dict[str, Any]:
    return {}


def normalize_claim_rules(raw: Any) -> dict[str, Any] | None:
    """校验并规范化管理端传入的领取条件；无效则 fail。

    返回 None 表示无额外限制（落库 NULL）；非空 dict 只含已设置字段。
    """
    if raw is None:
        return None
    if raw is False or raw == "" or raw == {}:
        return None
    if not isinstance(raw, dict):
        fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="领取条件须为对象")

    out: dict[str, Any] = {}

    # 注册时间窗（东八区）
    for key, camel in (("registeredFrom", "registeredFrom"), ("registered_from", "registeredFrom")):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or val == "":
            continue
        try:
            parsed = parse_query_datetime(str(val))
        except ValueError:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="注册开始时间格式无效")
        if parsed is None:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="注册开始时间格式无效")
        out[camel] = to_cst_iso(parsed)
        break

    for key, camel in (("registeredTo", "registeredTo"), ("registered_to", "registeredTo")):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or val == "":
            continue
        try:
            parsed = parse_query_datetime(str(val))
        except ValueError:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="注册结束时间格式无效")
        if parsed is None:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="注册结束时间格式无效")
        out[camel] = to_cst_iso(parsed)
        break

    from_iso = out.get("registeredFrom")
    to_iso = out.get("registeredTo")
    if from_iso and to_iso:
        from_dt = parse_query_datetime(from_iso)
        to_dt = parse_query_datetime(to_iso)
        if from_dt is not None and to_dt is not None and to_dt < from_dt:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="注册结束时间须晚于或等于开始时间")

    # 累计充值金额（分）
    for key, camel in (("minRechargeFen", "minRechargeFen"), ("min_recharge_fen", "minRechargeFen")):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or val == "":
            continue
        try:
            fen = int(val)
        except (TypeError, ValueError):
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="最低充值金额（分）须为整数")
        if fen < 1:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="最低充值金额（分）须 ≥ 1")
        out[camel] = fen
        break

    # 累计充值入账算力点
    for key, camel in (
        ("minRechargeCredits", "minRechargeCredits"),
        ("min_recharge_credits", "minRechargeCredits"),
    ):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or val == "":
            continue
        try:
            credits = int(val)
        except (TypeError, ValueError):
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="最低充值算力点须为整数")
        if credits < 1:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="最低充值算力点须 ≥ 1")
        out[camel] = credits
        break

    # 须有效会员
    for key, camel in (
        ("requireActiveMember", "requireActiveMember"),
        ("require_active_member", "requireActiveMember"),
    ):
        if key not in raw:
            continue
        val = raw.get(key)
        if val is None or val == "":
            continue
        if isinstance(val, bool):
            if val:
                out[camel] = True
        elif str(val).lower() in ("1", "true", "yes"):
            out[camel] = True
        elif str(val).lower() in ("0", "false", "no"):
            pass  # 不要求会员时省略字段
        else:
            fail(ErrorCode.ACTIVITY_INVALID_CLAIM_RULES, message="requireActiveMember 须为布尔值")
        break

    return out or None


def _soft_parse_claim_rules(raw: Any) -> dict[str, Any]:
    """宽松读取库内规则（不 fail）；用于列表/领取判定，避免脏数据阻断。"""
    if not raw or not isinstance(raw, dict):
        return _empty_rules()
    out: dict[str, Any] = {}
    for src, dest in (
        ("registeredFrom", "registeredFrom"),
        ("registered_from", "registeredFrom"),
        ("registeredTo", "registeredTo"),
        ("registered_to", "registeredTo"),
    ):
        if src not in raw:
            continue
        val = raw.get(src)
        if val is None or val == "":
            continue
        try:
            parsed = parse_query_datetime(str(val))
        except ValueError:
            continue
        if parsed is None:
            continue
        out[dest] = to_cst_iso(parsed)
    for src, dest in (
        ("minRechargeFen", "minRechargeFen"),
        ("min_recharge_fen", "minRechargeFen"),
    ):
        if src not in raw:
            continue
        try:
            fen = int(raw.get(src))
        except (TypeError, ValueError):
            continue
        if fen >= 1:
            out[dest] = fen
    for src, dest in (
        ("minRechargeCredits", "minRechargeCredits"),
        ("min_recharge_credits", "minRechargeCredits"),
    ):
        if src not in raw:
            continue
        try:
            credits = int(raw.get(src))
        except (TypeError, ValueError):
            continue
        if credits >= 1:
            out[dest] = credits
    for src in ("requireActiveMember", "require_active_member"):
        if src not in raw:
            continue
        val = raw.get(src)
        if val is True or str(val).lower() in ("1", "true", "yes"):
            out["requireActiveMember"] = True
        break
    return out


def claim_rules_from_activity(activity: CreditActivity) -> dict[str, Any]:
    """读取活动上的规则（宽松）。"""
    return _soft_parse_claim_rules(getattr(activity, "claim_rules", None))


def serialize_claim_rules(activity: CreditActivity) -> dict[str, Any] | None:
    """API 输出用：无规则返回 None。"""
    rules = claim_rules_from_activity(activity)
    return rules or None


def describe_claim_rules(rules: dict[str, Any] | None) -> list[str]:
    """管理端/用户端可读条件摘要。"""
    if not rules:
        return []
    lines: list[str] = []
    from_s = rules.get("registeredFrom")
    to_s = rules.get("registeredTo")
    if from_s or to_s:
        left = (from_s or "不限")[:19].replace("T", " ")
        right = (to_s or "不限")[:19].replace("T", " ")
        lines.append(f"注册时间：{left} ~ {right}")
    fen = rules.get("minRechargeFen")
    if fen is not None:
        yuan = int(fen) / 100
        lines.append(f"累计充值 ≥ {yuan:g} 元")
    credits = rules.get("minRechargeCredits")
    if credits is not None:
        lines.append(f"累计充值算力 ≥ {int(credits)} 点")
    if rules.get("requireActiveMember"):
        lines.append("须为有效会员")
    return lines


async def load_user_claim_profile(db: AsyncSession, user: User) -> UserClaimProfile:
    """一次查出用户注册时间、累计充值、是否有效会员。"""
    fen_row = await db.execute(
        select(
            func.coalesce(func.sum(CreditRechargeOrder.pay_amount_fen), 0),
            func.coalesce(func.sum(CreditRechargeOrder.amount), 0),
        ).filter(
            CreditRechargeOrder.user_id == user.id,
            CreditRechargeOrder.status == _RECHARGE_COMPLETED,
            CreditRechargeOrder.order_type == _RECHARGE_ORDER_TYPE,
        )
    )
    fen_sum, credit_sum = fen_row.one()
    sub = await get_user_active_subscription(db, user.id)
    return UserClaimProfile(
        registered_at=user.created_at,
        recharge_fen=int(fen_sum or 0),
        recharge_credits=int(credit_sum or 0),
        is_active_member=sub is not None,
    )


def evaluate_eligibility(
    rules: dict[str, Any] | None,
    profile: UserClaimProfile,
) -> EligibilityResult:
    """按 AND 判定用户是否满足领取条件。"""
    if not rules:
        return EligibilityResult(eligible=True, reasons=[])

    reasons: list[str] = []
    registered = as_cst_aware(profile.registered_at)

    from_raw = rules.get("registeredFrom")
    if from_raw:
        try:
            from_naive = parse_query_datetime(str(from_raw))
        except ValueError:
            from_naive = None
        from_aware = as_cst_aware(from_naive)
        if from_aware is not None and (registered is None or registered < from_aware):
            reasons.append("注册时间未达到活动要求的起始时间")

    to_raw = rules.get("registeredTo")
    if to_raw:
        try:
            to_naive = parse_query_datetime(str(to_raw))
        except ValueError:
            to_naive = None
        to_aware = as_cst_aware(to_naive)
        if to_aware is not None and (registered is None or registered > to_aware):
            reasons.append("注册时间超出活动要求的截止时间")

    min_fen = rules.get("minRechargeFen")
    if min_fen is not None:
        need = int(min_fen)
        if profile.recharge_fen < need:
            yuan = need / 100
            have_yuan = profile.recharge_fen / 100
            reasons.append(f"累计充值不足（需 ≥ {yuan:g} 元，当前 {have_yuan:g} 元）")

    min_credits = rules.get("minRechargeCredits")
    if min_credits is not None:
        need = int(min_credits)
        if profile.recharge_credits < need:
            reasons.append(
                f"累计充值算力不足（需 ≥ {need} 点，当前 {profile.recharge_credits} 点）"
            )

    if rules.get("requireActiveMember") and not profile.is_active_member:
        reasons.append("须为有效会员方可领取")

    return EligibilityResult(eligible=len(reasons) == 0, reasons=reasons)


def assert_user_eligible(
    activity: CreditActivity,
    profile: UserClaimProfile,
) -> None:
    """领取路径强制校验；不满足则 403 ACTIVITY_NOT_ELIGIBLE。"""
    rules = claim_rules_from_activity(activity)
    result = evaluate_eligibility(rules, profile)
    if result.eligible:
        return
    message = "；".join(result.reasons) if result.reasons else "未满足活动领取条件"
    fail(
        ErrorCode.ACTIVITY_NOT_ELIGIBLE,
        message=message,
        content={
            "reasons": result.reasons,
            "claimRules": rules or None,
        },
    )
