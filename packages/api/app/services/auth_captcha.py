"""滑动拼图验证码 —— 挑战/票据仅存 Redis，供发短信与登录风控使用。

背景主题多样（极光 / 水晶 / 霓虹 / 水墨等），拼图块凹凸随机，前端弹窗高级玻璃态。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import math
import random
import secrets
import uuid
from typing import Any

from fastapi import Request

from ..core.config import get_settings
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .auth_request import client_ip
from .auth_volatile import volatile_delete, volatile_get, volatile_incr, volatile_set
from .redis_client import get_redis

logger = logging.getLogger(__name__)

# 拼图画布与滑块尺寸（与前端弹窗对齐）
_CAPTCHA_WIDTH = 320
_CAPTCHA_HEIGHT = 168
_PIECE_SIZE = 48
# 滑动位置容差（像素）
_DEFAULT_TOLERANCE_PX = 6

# 主题：名称 / 主色 / 辅色 / 高光（供前端滑轨点缀）
_THEMES: list[dict[str, Any]] = [
    {
        "id": "aurora",
        "accent": "#5eead4",
        "colors": ("#0f172a", "#134e4a", "#0e7490", "#67e8f9", "#a5f3fc"),
    },
    {
        "id": "crystal",
        "accent": "#60a5fa",
        "colors": ("#0b1220", "#1e3a5f", "#2563eb", "#93c5fd", "#dbeafe"),
    },
    {
        "id": "neon",
        "accent": "#e879f9",
        "colors": ("#18181b", "#4a044e", "#86198f", "#f0abfc", "#22d3ee"),
    },
    {
        "id": "sunset",
        "accent": "#fb923c",
        "colors": ("#1c1917", "#7c2d12", "#c2410c", "#fdba74", "#fde68a"),
    },
    {
        "id": "ink",
        "accent": "#a8a29e",
        "colors": ("#0c0a09", "#292524", "#57534e", "#a8a29e", "#e7e5e4"),
    },
    {
        "id": "forest",
        "accent": "#4ade80",
        "colors": ("#052e16", "#14532d", "#166534", "#86efac", "#bbf7d0"),
    },
    {
        "id": "rose",
        "accent": "#fb7185",
        "colors": ("#1a0a10", "#4c0519", "#9f1239", "#fda4af", "#ffe4e6"),
    },
    {
        "id": "gold",
        "accent": "#fbbf24",
        "colors": ("#1c1917", "#78350f", "#b45309", "#fcd34d", "#fef3c7"),
    },
]


def _challenge_key(captcha_id: str) -> str:
    return f"captcha:challenge:{captcha_id}"


def _ticket_key(ticket_id: str) -> str:
    return f"captcha:ticket:{ticket_id}"


def _fail_key(scope: str) -> str:
    return f"captcha:fail:{scope}"


def _hash_target(target_x: int, salt: str) -> str:
    """对目标偏移做 HMAC，避免 Redis 明文泄露答案。"""
    return hmac.new(
        salt.encode("utf-8"),
        str(int(target_x)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _piece_path(
    size: int,
    rng: random.Random,
    *,
    right_dir: int | None = None,
    bottom_dir: int | None = None,
    knob_scale: float | None = None,
) -> str:
    """拼图块路径；可指定右/下凹凸方向，便于真假缺口形状不一致。"""
    r = size / 5.5
    # 右侧凸起(+1)或凹进(-1)；底边同理
    rd = right_dir if right_dir in (-1, 1) else rng.choice([1, -1])
    bd = bottom_dir if bottom_dir in (-1, 1) else rng.choice([1, -1])
    # 顶部/左侧保持平滑圆角，避免裁切溢出
    ks = float(knob_scale) if knob_scale is not None else 0.95
    knob = r * max(0.7, min(1.15, ks))

    # 右凸：中段向外；右凹：中段向内
    if rd > 0:
        right_seg = (
            f"L{size},{size * 0.32} "
            f"C{size + knob},{size * 0.32} {size + knob},{size * 0.68} {size},{size * 0.68} "
        )
    else:
        right_seg = (
            f"L{size},{size * 0.32} "
            f"C{size - knob},{size * 0.32} {size - knob},{size * 0.68} {size},{size * 0.68} "
        )

    if bd > 0:
        bottom_seg = (
            f"L{size * 0.68},{size} "
            f"C{size * 0.68},{size + knob} {size * 0.32},{size + knob} {size * 0.32},{size} "
        )
    else:
        bottom_seg = (
            f"L{size * 0.68},{size} "
            f"C{size * 0.68},{size - knob} {size * 0.32},{size - knob} {size * 0.32},{size} "
        )

    return (
        f"M0,{r} "
        f"C0,{r * 0.35} {r * 0.35},0 {r},0 "
        f"L{size - r},0 "
        f"C{size - r * 0.35},0 {size},{r * 0.35} {size},{r} "
        f"{right_seg}"
        f"L{size},{size - r} "
        f"C{size},{size - r * 0.35} {size - r * 0.35},{size} {size - r},{size} "
        f"{bottom_seg}"
        f"L{r},{size} "
        f"C{r * 0.35},{size} 0,{size - r * 0.35} 0,{size - r} "
        f"L0,{r} Z"
    )


def _decoy_piece_path(
    size: int,
    rng: random.Random,
    *,
    real_right: int,
    real_bottom: int,
) -> str:
    """生成与真缺口至少一处凹凸相反的假缺口轮廓（必要时再改 knob 比例）。"""
    # 随机翻转右或底，或两者都翻，保证与真形状不同
    mode = rng.randint(0, 2)
    if mode == 0:
        dr, db = -real_right, real_bottom
    elif mode == 1:
        dr, db = real_right, -real_bottom
    else:
        dr, db = -real_right, -real_bottom
    # knob 略不同，轮廓更易区分
    knob_scale = rng.choice([0.78, 0.88, 1.05, 1.12])
    return _piece_path(
        size, rng, right_dir=dr, bottom_dir=db, knob_scale=knob_scale
    )


def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return f"rgba(255,255,255,{alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _decor_aurora(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """极光带状流动。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="45%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c2}"/>'
        f"</linearGradient>"
        f'<linearGradient id="band" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{c3}" stop-opacity="0"/>'
        f'<stop offset="40%" stop-color="{c3}" stop-opacity="0.55"/>'
        f'<stop offset="100%" stop-color="{c4}" stop-opacity="0"/>'
        f"</linearGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    for i in range(4):
        y = rng.randint(10, h - 30)
        amp = rng.randint(18, 40)
        parts.append(
            f'<path d="M0,{y} Q{w * 0.25},{y - amp} {w * 0.5},{y} '
            f'T{w},{y + amp * 0.3}" fill="none" stroke="url(#band)" '
            f'stroke-width="{rng.randint(14, 28)}" opacity="{round(rng.uniform(0.35, 0.7), 2)}"/>'
        )
        _ = i
    for _ in range(18):
        parts.append(
            f'<circle cx="{rng.randint(0, w)}" cy="{rng.randint(0, h)}" '
            f'r="{rng.uniform(0.6, 1.8):.1f}" fill="{c4}" opacity="{rng.uniform(0.3, 0.9):.2f}"/>'
        )
    return "".join(parts)


def _decor_crystal(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """水晶多面体 / 3D 几何（贴近参考图质感）。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="55%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c2}"/>'
        f"</linearGradient>"
        f'<radialGradient id="glow" cx="30%" cy="30%" r="70%">'
        f'<stop offset="0%" stop-color="{c4}" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="{c0}" stop-opacity="0"/>'
        f"</radialGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
        f'<rect width="{w}" height="{h}" fill="url(#glow)"/>',
    ]
    for _ in range(7):
        cx = rng.randint(20, w - 20)
        cy = rng.randint(20, h - 20)
        s = rng.randint(28, 64)
        rot = rng.randint(0, 360)
        fill = rng.choice([c2, c3, c4])
        op = round(rng.uniform(0.18, 0.42), 2)
        # 四面体投影
        pts = f"{cx},{cy - s * 0.55} {cx + s * 0.5},{cy + s * 0.35} {cx - s * 0.5},{cy + s * 0.35}"
        parts.append(
            f'<polygon points="{pts}" fill="{fill}" opacity="{op}" '
            f'transform="rotate({rot} {cx} {cy})"/>'
        )
    for _ in range(5):
        cx = rng.randint(15, w - 15)
        cy = rng.randint(15, h - 15)
        rr = rng.randint(10, 28)
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="none" '
            f'stroke="{c4}" stroke-width="1.2" opacity="{rng.uniform(0.2, 0.45):.2f}"/>'
        )
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rr * 0.35}" fill="{c3}" '
            f'opacity="{rng.uniform(0.25, 0.55):.2f}"/>'
        )
    return "".join(parts)


def _decor_neon(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """霓虹网格 + 光晕。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="100%" stop-color="{c1}"/>'
        f"</linearGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    step = 22
    for x in range(0, w + 1, step):
        parts.append(
            f'<line x1="{x}" y1="0" x2="{x}" y2="{h}" stroke="{c2}" '
            f'stroke-width="0.6" opacity="0.25"/>'
        )
    for y in range(0, h + 1, step):
        parts.append(
            f'<line x1="0" y1="{y}" x2="{w}" y2="{y}" stroke="{c2}" '
            f'stroke-width="0.6" opacity="0.2"/>'
        )
    for _ in range(6):
        x1, y1 = rng.randint(0, w), rng.randint(0, h)
        x2, y2 = rng.randint(0, w), rng.randint(0, h)
        col = rng.choice([c3, c4])
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" '
            f'stroke-width="{rng.uniform(1.2, 2.5):.1f}" opacity="{rng.uniform(0.35, 0.7):.2f}" '
            f'stroke-linecap="round"/>'
        )
    for _ in range(4):
        cx, cy = rng.randint(20, w - 20), rng.randint(20, h - 20)
        rr = rng.randint(16, 40)
        col = rng.choice([c3, c4])
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="{_hex_rgba(col, 0.18)}"/>'
        )
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="3" fill="{col}" opacity="0.9"/>'
        )
    return "".join(parts)


def _decor_sunset(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """日落山峦剪影。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{c1}"/>'
        f'<stop offset="55%" stop-color="{c2}"/>'
        f'<stop offset="100%" stop-color="{c3}"/>'
        f"</linearGradient>"
        f'<radialGradient id="sun" cx="70%" cy="35%" r="40%">'
        f'<stop offset="0%" stop-color="{c4}" stop-opacity="0.95"/>'
        f'<stop offset="45%" stop-color="{c3}" stop-opacity="0.35"/>'
        f'<stop offset="100%" stop-color="{c2}" stop-opacity="0"/>'
        f"</radialGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
        f'<rect width="{w}" height="{h}" fill="url(#sun)"/>',
    ]
    # 远山
    y_base = int(h * 0.62)
    pts1 = [f"0,{h}", f"0,{y_base}"]
    x = 0
    while x <= w:
        pts1.append(f"{x},{y_base - rng.randint(8, 36)}")
        x += rng.randint(28, 48)
    pts1.append(f"{w},{y_base}")
    pts1.append(f"{w},{h}")
    parts.append(f'<polygon points="{" ".join(pts1)}" fill="{_hex_rgba(c0, 0.45)}"/>')
    y_base2 = int(h * 0.72)
    pts2 = [f"0,{h}", f"0,{y_base2}"]
    x = 0
    while x <= w:
        pts2.append(f"{x},{y_base2 - rng.randint(6, 28)}")
        x += rng.randint(24, 40)
    pts2.append(f"{w},{y_base2}")
    pts2.append(f"{w},{h}")
    parts.append(f'<polygon points="{" ".join(pts2)}" fill="{_hex_rgba(c0, 0.7)}"/>')
    return "".join(parts)


def _decor_ink(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """水墨晕染。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><radialGradient id="bg" cx="40%" cy="40%" r="80%">'
        f'<stop offset="0%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c0}"/>'
        f"</radialGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    for _ in range(10):
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        rr = rng.randint(20, 70)
        col = rng.choice([c2, c3, c4])
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="{_hex_rgba(col, rng.uniform(0.08, 0.28))}"/>'
        )
    # 几笔书法曲线
    for _ in range(3):
        x0, y0 = rng.randint(10, w // 3), rng.randint(20, h - 20)
        x1, y1 = rng.randint(w // 3, 2 * w // 3), rng.randint(10, h - 10)
        x2, y2 = rng.randint(2 * w // 3, w - 10), rng.randint(20, h - 20)
        parts.append(
            f'<path d="M{x0},{y0} Q{x1},{y1} {x2},{y2}" fill="none" '
            f'stroke="{c4}" stroke-width="{rng.uniform(1.5, 4):.1f}" '
            f'opacity="{rng.uniform(0.25, 0.55):.2f}" stroke-linecap="round"/>'
        )
    return "".join(parts)


def _decor_forest(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """林间光斑。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="100%" stop-color="{c1}"/>'
        f"</linearGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    for _ in range(8):
        x = rng.randint(-10, w)
        top = rng.randint(-5, 20)
        bw = rng.randint(18, 40)
        parts.append(
            f'<polygon points="{x},{h} {x + bw // 2},{top} {x + bw},{h}" '
            f'fill="{_hex_rgba(c2, rng.uniform(0.25, 0.5))}"/>'
        )
    for _ in range(12):
        parts.append(
            f'<circle cx="{rng.randint(0, w)}" cy="{rng.randint(0, h)}" '
            f'r="{rng.randint(3, 12)}" fill="{_hex_rgba(c4, rng.uniform(0.12, 0.4))}"/>'
        )
    parts.append(
        f'<circle cx="{int(w * 0.75)}" cy="{int(h * 0.28)}" r="22" '
        f'fill="{_hex_rgba(c3, 0.35)}"/>'
    )
    return "".join(parts)


def _decor_rose(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """玫瑰雾面气泡。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="1" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="50%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c2}"/>'
        f"</linearGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    for _ in range(14):
        cx, cy = rng.randint(0, w), rng.randint(0, h)
        rr = rng.randint(8, 36)
        col = rng.choice([c3, c4])
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="{_hex_rgba(col, rng.uniform(0.12, 0.4))}"/>'
        )
        parts.append(
            f'<circle cx="{cx - rr * 0.25}" cy="{cy - rr * 0.25}" r="{rr * 0.2}" '
            f'fill="{_hex_rgba("#ffffff", 0.25)}"/>'
        )
    return "".join(parts)


def _decor_gold(rng: random.Random, w: int, h: int, colors: tuple[str, ...]) -> str:
    """暗金箔片。"""
    c0, c1, c2, c3, c4 = colors
    parts = [
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{c0}"/>'
        f'<stop offset="60%" stop-color="{c1}"/>'
        f'<stop offset="100%" stop-color="{c2}"/>'
        f"</linearGradient></defs>",
        f'<rect width="{w}" height="{h}" fill="url(#bg)"/>',
    ]
    for _ in range(16):
        x, y = rng.randint(0, w), rng.randint(0, h)
        s = rng.randint(6, 18)
        rot = rng.randint(0, 360)
        col = rng.choice([c3, c4, c2])
        parts.append(
            f'<rect x="{x}" y="{y}" width="{s}" height="{s * 0.55}" rx="1" '
            f'fill="{col}" opacity="{rng.uniform(0.2, 0.55):.2f}" '
            f'transform="rotate({rot} {x + s / 2} {y})"/>'
        )
    for _ in range(5):
        cx, cy = rng.randint(10, w - 10), rng.randint(10, h - 10)
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{rng.randint(2, 5)}" fill="{c4}" '
            f'opacity="{rng.uniform(0.4, 0.85):.2f}"/>'
        )
    return "".join(parts)


_DECOR_BUILDERS = {
    "aurora": _decor_aurora,
    "crystal": _decor_crystal,
    "neon": _decor_neon,
    "sunset": _decor_sunset,
    "ink": _decor_ink,
    "forest": _decor_forest,
    "rose": _decor_rose,
    "gold": _decor_gold,
}


def _build_decor(seed: int, theme_id: str, colors: tuple[str, ...]) -> str:
    rng = random.Random(seed)
    builder = _DECOR_BUILDERS.get(theme_id) or _decor_crystal
    return builder(rng, _CAPTCHA_WIDTH, _CAPTCHA_HEIGHT, colors)


def _local_detail_layer(
    rng: random.Random,
    *,
    colors: tuple[str, ...],
    foci: list[tuple[int, int]],
) -> str:
    """在缺口附近叠加细碎纹理，使裁下的拼图块有可辨识内容（而非纯色块）。"""
    c3, c4 = colors[3], colors[4]
    parts: list[str] = []
    for fx, fy in foci:
        for _ in range(14):
            cx = fx + rng.randint(-6, _PIECE_SIZE + 6)
            cy = fy + rng.randint(-6, _PIECE_SIZE + 6)
            kind = rng.randint(0, 2)
            op = round(rng.uniform(0.35, 0.85), 2)
            col = rng.choice([c3, c4, "#ffffff"])
            if kind == 0:
                parts.append(
                    f'<circle cx="{cx}" cy="{cy}" r="{rng.uniform(1.2, 3.5):.1f}" '
                    f'fill="{col}" opacity="{op}"/>'
                )
            elif kind == 1:
                s = rng.randint(3, 9)
                rot = rng.randint(0, 360)
                parts.append(
                    f'<rect x="{cx}" y="{cy}" width="{s}" height="{max(2, s // 2)}" rx="0.5" '
                    f'fill="{col}" opacity="{op}" transform="rotate({rot} {cx} {cy})"/>'
                )
            else:
                parts.append(
                    f'<line x1="{cx}" y1="{cy}" x2="{cx + rng.randint(-10, 10)}" '
                    f'y2="{cy + rng.randint(-10, 10)}" stroke="{col}" '
                    f'stroke-width="{rng.uniform(0.8, 1.8):.1f}" opacity="{op}" '
                    f'stroke-linecap="round"/>'
                )
    return "".join(parts)


def _pick_decoy_holes(
    *,
    target_x: int,
    target_y: int,
    rng: random.Random,
) -> list[tuple[int, int]]:
    """选 1 个虚假缺口位置（同 Y，X 远离真缺口，避免重叠）。"""
    w, pz = _CAPTCHA_WIDTH, _PIECE_SIZE
    min_x = pz + 10
    max_x = w - pz - 16
    min_gap = pz + 18
    for _ in range(48):
        fx = rng.randint(min_x, max_x)
        if abs(fx - target_x) < min_gap:
            continue
        return [(fx, target_y)]
    # 兜底：尽量放到另一侧
    if target_x < w // 2:
        fx = min(max_x, target_x + min_gap + rng.randint(0, 24))
    else:
        fx = max(min_x, target_x - min_gap - rng.randint(0, 24))
    return [(fx, target_y)]


def _hole_cutouts_mask(cuts: list[tuple[int, int, str]], w: int, h: int) -> str:
    """白色保留、黑色挖空；每个缺口可有独立 path（真假形状可不同）。"""
    parts = [
        '<mask id="holeMask" maskUnits="userSpaceOnUse" '
        f'x="0" y="0" width="{w}" height="{h}">',
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="white"/>',
    ]
    for hx, hy, path in cuts:
        parts.append(f'<path transform="translate({hx},{hy})" d="{path}" fill="black"/>')
    parts.append("</mask>")
    return "".join(parts)


def _hole_inset_overlays(cuts: list[tuple[int, int, str]]) -> str:
    """缺口描边（各用自身 path）。空洞由 mask 挖透暗底板呈现。"""
    parts: list[str] = []
    for hx, hy, path in cuts:
        parts.append(
            f'<g transform="translate({hx},{hy})" fill="none">'
            f'<path d="{path}" stroke="rgba(0,0,0,0.7)" stroke-width="3.5" opacity="0.45"/>'
            f'<path d="{path}" stroke="rgba(255,255,255,0.22)" stroke-width="1.1"/>'
            f"</g>"
        )
    return "".join(parts)


def _svg_to_data_url(svg: str) -> str:
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _render_slide_challenge(
    *,
    target_x: int,
    target_y: int,
    seed: int,
    theme: dict[str, Any],
    decoy_holes: list[tuple[int, int]] | None = None,
) -> tuple[str, str]:
    """渲染「真正抠掉的拼图块」背景 + 可拖动裁切片；虚假缺口使用不同轮廓。"""
    w, h, pz = _CAPTCHA_WIDTH, _CAPTCHA_HEIGHT, _PIECE_SIZE
    rng = random.Random(seed ^ 0x5A5A)
    real_right = rng.choice([1, -1])
    real_bottom = rng.choice([1, -1])
    # 真缺口 / 滑块共用同一 path
    real_path = _piece_path(
        pz, rng, right_dir=real_right, bottom_dir=real_bottom, knob_scale=0.95
    )
    colors = tuple(theme["colors"])
    accent = str(theme.get("accent") or "#60a5fa")

    decoys = list(decoy_holes or [])
    # 假缺口：凹凸方向与真缺口不一致
    cuts: list[tuple[int, int, str]] = [(target_x, target_y, real_path)]
    for fx, fy in decoys:
        decoy_path = _decoy_piece_path(
            pz, rng, real_right=real_right, real_bottom=real_bottom
        )
        cuts.append((fx, fy, decoy_path))

    foci = [(hx, hy) for hx, hy, _ in cuts]

    # 完整画面 = 主题装饰 + 缺口附近细碎纹理（拼图块从这里裁）
    base_decor = _build_decor(seed, str(theme["id"]), colors)  # type: ignore[arg-type]
    details = _local_detail_layer(rng, colors=colors, foci=foci)
    full_scene = f"{base_decor}{details}"

    # 背景：底板 + mask 挖空（各缺口独立 path）+ 描边
    bg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f"<defs>{_hole_cutouts_mask(cuts, w, h)}</defs>"
        f'<rect width="{w}" height="{h}" fill="#07090f"/>'
        f'<rect width="{w}" height="{h}" fill="rgba(255,255,255,0.03)"/>'
        f'<g mask="url(#holeMask)">{full_scene}</g>'
        f"{_hole_inset_overlays(cuts)}"
        f"</svg>"
    )

    # 滑块：仅按真缺口 path 裁切
    overflow = 14
    slider_w = pz + overflow
    slider_h = pz + overflow
    slider = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{slider_w}" height="{slider_h}" '
        f'viewBox="0 0 {slider_w} {slider_h}">'
        f"<defs>"
        f'<clipPath id="pieceClip"><path d="{real_path}"/></clipPath>'
        f'<filter id="pieceShadow" x="-40%" y="-40%" width="180%" height="180%">'
        f'<feDropShadow dx="1.5" dy="2.5" stdDeviation="2.2" flood-opacity="0.55"/>'
        f"</filter>"
        f'<linearGradient id="pieceEdge" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="#ffffff" stop-opacity="0.95"/>'
        f'<stop offset="100%" stop-color="{accent}" stop-opacity="0.85"/>'
        f"</linearGradient>"
        f"</defs>"
        f'<g filter="url(#pieceShadow)" clip-path="url(#pieceClip)" '
        f'transform="translate({-target_x},{-target_y})">'
        f"{full_scene}"
        f"</g>"
        f'<path d="{real_path}" fill="none" stroke="url(#pieceEdge)" stroke-width="1.8"/>'
        f"</svg>"
    )
    return _svg_to_data_url(bg), _svg_to_data_url(slider)


async def _require_redis_for_captcha() -> None:
    if await get_redis() is None:
        fail(ErrorCode.CAPTCHA_REDIS_UNAVAILABLE)


def captcha_enabled() -> bool:
    return bool(get_settings().captcha_enabled)


async def create_captcha_challenge() -> dict[str, Any]:
    """生成滑动拼图挑战，目标偏移哈希写入 Redis。"""
    settings = get_settings()
    await _require_redis_for_captcha()

    captcha_id = str(uuid.uuid4())
    salt = secrets.token_hex(8)
    ttl = max(int(settings.captcha_ttl_seconds or 120), 60)
    seed = secrets.randbelow(1_000_000_000)
    theme = secrets.choice(_THEMES)

    # 目标 X：避开左右边缘，保证拼图块完整可见
    min_x = _PIECE_SIZE + 10
    max_x = _CAPTCHA_WIDTH - _PIECE_SIZE - 16
    target_x = secrets.randbelow(max_x - min_x + 1) + min_x
    target_y = secrets.randbelow(_CAPTCHA_HEIGHT - _PIECE_SIZE - 28) + 14
    # 虚假缺口：视觉与真缺口一致，仅真缺口坐标可校验通过
    decoy_rng = random.Random(seed ^ 0xC0FFEE)
    decoy_holes = _pick_decoy_holes(
        target_x=target_x, target_y=target_y, rng=decoy_rng
    )

    await volatile_set(
        _challenge_key(captcha_id),
        {
            "target_hash": _hash_target(target_x, salt),
            "salt": salt,
            "target_y": target_y,
        },
        ttl,
    )

    bg, slider = _render_slide_challenge(
        target_x=target_x,
        target_y=target_y,
        seed=seed,
        theme=theme,
        decoy_holes=decoy_holes,
    )
    payload: dict[str, Any] = {
        "captchaId": captcha_id,
        "backgroundImage": bg,
        "sliderImage": slider,
        "puzzleY": target_y,
        "imageWidth": _CAPTCHA_WIDTH,
        "imageHeight": _CAPTCHA_HEIGHT,
        "sliderSize": _PIECE_SIZE,
        "themeId": theme["id"],
        "themeAccent": theme["accent"],
        "expiresIn": ttl,
        # 兼容旧字段
        "imageBase64": bg,
        "answer": "",
    }
    if settings.captcha_debug_return_answer:
        payload["answer"] = str(target_x)
    return payload


async def verify_captcha_and_issue_ticket(
    *,
    captcha_id: str,
    slide_x: float | None = None,
    captcha_code: str | None = None,
    phone: str,
    scene: str,
    request: Request | None = None,
) -> dict[str, Any]:
    """校验滑动位置并签发一次性 ticket（绑定手机号与场景）。"""
    settings = get_settings()
    await _require_redis_for_captcha()

    cid = (captcha_id or "").strip()
    # 兼容：slideX 优先；旧客户端可能把偏移写在 captchaCode
    x_raw: float | None = slide_x
    if x_raw is None and captcha_code is not None and str(captcha_code).strip() != "":
        try:
            x_raw = float(str(captcha_code).strip())
        except ValueError:
            x_raw = None
    if not cid or x_raw is None or not math.isfinite(x_raw):
        fail(ErrorCode.CAPTCHA_REQUIRED)

    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    scene_norm = (scene or "").strip() or "user_login"
    ip = client_ip(request) if request is not None else ""

    raw = await volatile_get(_challenge_key(cid))
    if not isinstance(raw, dict) or not raw.get("target_hash") or not raw.get("salt"):
        fail(ErrorCode.CAPTCHA_EXPIRED)

    expected = str(raw["target_hash"])
    salt = str(raw["salt"])
    submitted = int(round(float(x_raw)))
    tolerance = max(
        1,
        int(getattr(settings, "captcha_slide_tolerance_px", None) or _DEFAULT_TOLERANCE_PX),
    )

    # 在容差范围内逐点比对 HMAC（目标未知，仅允许小窗口）
    matched = False
    for candidate in range(submitted - tolerance, submitted + tolerance + 1):
        if hmac.compare_digest(expected, _hash_target(candidate, salt)):
            matched = True
            break

    if not matched:
        scope = phone_digits or ip or "anon"
        fails = await volatile_incr(_fail_key(scope), 1800)
        await volatile_delete(_challenge_key(cid))
        if fails >= 8:
            fail(ErrorCode.CAPTCHA_LOCKED, content={"waitSeconds": 1800}, http_status=423)
        fail(ErrorCode.CAPTCHA_INVALID)

    # 挑战一次性作废
    await volatile_delete(_challenge_key(cid))

    ticket_id = str(uuid.uuid4())
    ticket_ttl = max(int(settings.captcha_ticket_ttl_seconds or 120), 60)
    await volatile_set(
        _ticket_key(ticket_id),
        {
            "phone": phone_digits,
            "scene": scene_norm,
            "ip": ip,
        },
        ticket_ttl,
    )
    return {
        "captchaTicket": ticket_id,
        "expiresIn": ticket_ttl,
    }


async def consume_captcha_ticket(
    *,
    ticket: str | None,
    phone: str,
    scene: str,
    required: bool = True,
) -> None:
    """消费滑动验证码票据；captcha 未启用时跳过。"""
    if not captcha_enabled():
        return
    if not required:
        return

    await _require_redis_for_captcha()
    tid = (ticket or "").strip()
    if not tid:
        fail(ErrorCode.CAPTCHA_REQUIRED)

    phone_digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    scene_norm = (scene or "").strip() or "user_login"

    raw = await volatile_get(_ticket_key(tid))
    if not isinstance(raw, dict):
        fail(ErrorCode.CAPTCHA_TICKET_INVALID)

    # 一次性消费
    await volatile_delete(_ticket_key(tid))

    stored_phone = str(raw.get("phone") or "")
    stored_scene = str(raw.get("scene") or "")
    if stored_phone and phone_digits and stored_phone != phone_digits:
        fail(ErrorCode.CAPTCHA_TICKET_INVALID)
    if stored_scene and stored_scene != scene_norm:
        fail(ErrorCode.CAPTCHA_TICKET_INVALID)
