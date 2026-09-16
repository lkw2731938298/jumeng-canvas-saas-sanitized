"""注册赠送：点数规范化与终身唯一键。"""

from app.models.register_bonus import REGISTER_BONUS_KEY
from app.services.register_bonus import normalize_register_bonus_amount


def test_normalize_register_bonus_amount():
    assert normalize_register_bonus_amount(None) == 0
    assert normalize_register_bonus_amount(-3) == 0
    assert normalize_register_bonus_amount("12") == 12
    assert normalize_register_bonus_amount(1_000_001) == 1_000_000


def test_bonus_key_is_stable_lifetime_key():
    # UNIQUE(bonus_key, user_id) 依赖固定业务键，禁止运行时乱改
    assert REGISTER_BONUS_KEY == "register_bonus"
