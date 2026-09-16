"""发现页（/discover）运营内容配置：读写 ``platform_settings.discover_page``。

与登录页背景 ``auth_grid_images`` 隔离；未配置时回退内置默认（对齐 huabu 静态数据）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from .platform_settings import _ensure_settings_row
from .storage_urls import normalize_browser_storage_url, persistable_media_url

# 默认封面池：算力活动无封面时轮询使用
_DEFAULT_ACTIVITY_COVERS = [
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=85",
    "https://images.unsplash.com/photo-1634986666676-ec8fd927c23d?auto=format&fit=crop&w=800&q=85",
    "https://images.unsplash.com/photo-1574629810360-7efbbe195018?auto=format&fit=crop&w=800&q=85",
]


def _new_id() -> str:
    return uuid.uuid4().hex


def default_discover_page() -> dict[str, Any]:
    """内置默认发现页配置（与前端 huabuData 对齐）。"""
    return {
        "heroTitle": "想象",
        "heroEm": "正在发生的画面",
        "creationPlaceholder": "拖拽 / 粘贴 🏞️ 图片到这里，试试技能 skill、风格、资产",
        "heroVideos": [],
        # Hero 提示芯片改由前端拉取公开 Skill 目录；此处不再塞 Unsplash 假数据
        "promptSuggestions": [],
        "storyFeature": {
            "label": "剧情故事创作",
            "imageUrl": "https://images.unsplash.com/photo-1578632767115-351597cf2477?auto=format&fit=crop&w=400&q=85",
            "href": "/skills",
        },
        "highlightFeatures": [
            {
                "id": _new_id(),
                "label": "一键出海",
                "subtitle": "一键本地化出海短视频",
                "tone": "purple",
                "isNew": True,
                "href": "#overseas-localize",
                "sortOrder": 0,
            },
            {
                "id": _new_id(),
                "label": "爆款复刻",
                "subtitle": "拆解爆款结构快速复刻",
                "tone": "blue",
                "isNew": True,
                "href": "#viral-remake",
                "sortOrder": 1,
            },
            {
                "id": _new_id(),
                "label": "剧本智能分集",
                "subtitle": "长剧本智能拆分为分集",
                "tone": "indigo",
                "isNew": True,
                "href": "/skills",
                "sortOrder": 2,
            },
            {
                "id": _new_id(),
                "label": "角色设计",
                "subtitle": "统一角色外观与设定",
                "tone": "gold",
                "isNew": False,
                "href": "/skills",
                "sortOrder": 3,
            },
        ],
        "skillMachine": {
            "label": "Skill 技能制造机",
            "href": "/skills",
            "imageUrl": "",
        },
        "galleryFilters": [
            "全部",
            "短剧漫剧",
            "电商带货",
            "IP口播",
            "AI获客",
            "达人探店",
            "AI信息流",
            "商业广告",
            "动漫游戏",
            "教育生活",
        ],
        "galleryItems": _default_gallery_items(),
        # 右上角运营弹窗：默认关闭，避免空配置打扰用户
        "cornerPopup": _default_corner_popup(),
    }


def _default_corner_popup() -> dict[str, Any]:
    """发现页右上角弹窗默认值（关闭）。"""
    return {
        "enabled": False,
        "text": "",
        "imageUrl": "",
        "imageFill": False,
        "aspectRatio": "16:9",
        "showWhenLoggedIn": True,
        "showWhenLoggedOut": True,
    }


def _default_gallery_items() -> list[dict[str, Any]]:
    seeds = [
        ("坠入绯红星海", "Astrid Lab", "photo-1494790108377-be9c29b29330", "《坠入绯红星海》科幻感星际短片", "photo-1534447677768-be436bb09401", "全部"),
        ("潮汐之后", "Mori Studio", "photo-1527980965255-d3b416303d12", "《潮汐之后》治愈系海洋短片", "photo-1507525428034-b723cf961d3e", "短剧漫剧"),
        ("机械梦境 2049", "KIO", "photo-1500648767791-00dcc994a43e", "《机械梦境 2049》赛博朋克概念", "photo-1519608487953-e999c86e7455", "动漫游戏"),
        ("雾中的来信", "Still Frame", "photo-1535713875002-d1d0cf377fde", "《雾中的来信》文艺氛围短片", "photo-1486911278844-a81c5267e227", "全部"),
        ("霓虹夜行者", "Pixel Wave", "photo-1506794778202-cad84cf45f1d", "《霓虹夜行者》都市夜景实验", "photo-1514525253161-7a46d19cd819", "商业广告"),
        ("春日物语", "Luna Film", "photo-1544005313-94ddf0286df2", "《春日物语》清新生活短片", "photo-1493246507139-91e8fad9978e", "教育生活"),
        ("星际漫游", "Nova Studio", "photo-1472099645785-5658abf4ff4e", "《星际漫游》太空题材概念片", "photo-1451187580459-43490279c0fa", "动漫游戏"),
        ("古韵长安", "墨染文化", "photo-1580489944761-15a19d654956", "《古韵长安》新中式美学 TVC", "photo-1508804185872-d7badad00f7d", "商业广告"),
        ("深海回响", "Blue Frame", "photo-1517841905240-472988babdf9", "《深海回响》水下世界视觉实验", "photo-1559827260-dc66d52bef19", "全部"),
        ("云端之上", "Skyline", "photo-1524504388940-b1c1722653e1", "《云端之上》航拍风光纪录片", "photo-1469474968028-56623f02e42e", "全部"),
        ("午夜列车", "Night Owl", "photo-1506794778202-cad84cf45f1d", "《午夜列车》悬疑氛围短片", "photo-1470071459604-3b5ec3a7fe05", "短剧漫剧"),
        ("琥珀时光", "Amber Works", "photo-1534528741775-53994a69daeb", "《琥珀时光》复古胶片叙事", "photo-1516035069371-29a1b244cc32", "全部"),
        ("雨巷记忆", "墨色映像", "photo-1507003211169-0a1dd7228f2d", "《雨巷记忆》江南意境短片", "photo-1501594907352-04cda38ebc29", "短剧漫剧"),
        ("银色轨道", "Orbit Lab", "photo-1527980965255-d3b416303d12", "《银色轨道》未来交通概念片", "photo-1486312338219-ce68d2c6f44d", "全部"),
        ("花开四季", "Bloom Studio", "photo-1544005313-94ddf0286df2", "《花开四季》自然风光纪录", "photo-1490750967868-88aa4486c946", "教育生活"),
        ("暗巷追光", "Noir Frame", "photo-1500648767791-00dcc994a43e", "《暗巷追光》黑色电影风格", "photo-1519501025264-65ba15a82390", "全部"),
        ("沙漠回声", "Dune Vision", "photo-1472099645785-5658abf4ff4e", "《沙漠回声》旷野叙事短片", "photo-1509316785289-025f5b846b35", "全部"),
        ("镜中世界", "Mirror Lab", "photo-1438761681033-6461ffad8d80", "《镜中世界》超现实视觉实验", "photo-1506905925346-21bda4d32df4", "全部"),
        ("极光旅人", "Aurora Film", "photo-1494790108377-be9c29b29330", "《极光旅人》北境风光纪录", "photo-1531366936337-7c912a4589a7", "全部"),
        ("纸飞机", "Paper Wing", "photo-1517841905240-472988babdf9", "《纸飞机》治愈系生活短片", "photo-1501785888041-af3ef285b470", "短剧漫剧"),
        ("钢铁森林", "Urban Core", "photo-1506794778202-cad84cf45f1d", "《钢铁森林》都市建筑美学", "photo-1480714378408-67cf0d13bc1b", "全部"),
        ("青瓷梦", "青石造物", "photo-1580489944761-15a19d654956", "《青瓷梦》东方器物美学", "photo-1515405295579-ba7b45403062", "商业广告"),
        ("数字尘埃", "Glitch Works", "photo-1535713875002-d1d0cf377fde", "《数字尘埃》故障艺术短片", "photo-1550745165-9bc0b252726f", "动漫游戏"),
        ("海边邮局", "Tide Letter", "photo-1524504388940-b1c1722653e1", "《海边邮局》温情叙事短片", "photo-1507525428034-b723cf961d3e", "短剧漫剧"),
        ("星尘拾荒", "Cosmic Dust", "photo-1472099645785-5658abf4ff4e", "《星尘拾荒》太空歌剧概念", "photo-1462331940025-496dfbfc7564", "动漫游戏"),
        ("红墙往事", "朱门映像", "photo-1494790108377-be9c29b29330", "《红墙往事》古建人文纪录", "photo-1547981609-4b6bfe67ca0b", "教育生活"),
    ]
    items: list[dict[str, Any]] = []
    for i, (title, author, avatar, caption, photo, category) in enumerate(seeds):
        items.append(
            {
                "id": _new_id(),
                "title": title,
                "author": author,
                "avatarUrl": f"https://images.unsplash.com/{avatar}?auto=format&fit=crop&w=80&h=80&q=80",
                "caption": caption,
                "imageUrl": f"https://images.unsplash.com/{photo}?auto=format&fit=crop&w=1000&q=85",
                "category": category,
                "scope": "templates",
                "sortOrder": i,
                "isActive": True,
            }
        )
    return items


def _as_str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value in (1, "1", "true", "True", "yes"):
        return True
    if value in (0, "0", "false", "False", "no"):
        return False
    return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_discover_page(raw: Any) -> dict[str, Any]:
    """校验并规范化发现页配置；非法字段丢弃，缺失字段用默认值补齐。"""
    base = default_discover_page()
    if not isinstance(raw, dict) or not raw:
        return base

    hero_videos: list[dict[str, Any]] = []
    for i, entry in enumerate(raw.get("heroVideos") or raw.get("hero_videos") or []):
        if not isinstance(entry, dict):
            continue
        url = _as_str(entry.get("url") or entry.get("videoUrl") or entry.get("video_url"))
        if not url:
            continue
        hero_videos.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "url": url,
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    hero_videos.sort(key=lambda x: (x["sortOrder"], x["id"]))

    prompts: list[dict[str, Any]] = []
    for i, entry in enumerate(raw.get("promptSuggestions") or raw.get("prompt_suggestions") or []):
        if not isinstance(entry, dict):
            continue
        label = _as_str(entry.get("label"))
        if not label:
            continue
        prompts.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "label": label,
                "imageUrl": _as_str(entry.get("imageUrl") or entry.get("image_url")),
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    prompts.sort(key=lambda x: (x["sortOrder"], x["id"]))

    story_raw = raw.get("storyFeature") or raw.get("story_feature") or {}
    if not isinstance(story_raw, dict):
        story_raw = {}
    story = {
        "label": _as_str(story_raw.get("label"), base["storyFeature"]["label"]),
        "imageUrl": _as_str(story_raw.get("imageUrl") or story_raw.get("image_url"), base["storyFeature"]["imageUrl"]),
        "href": _as_str(story_raw.get("href"), base["storyFeature"]["href"]),
    }

    highlights: list[dict[str, Any]] = []
    allowed_tones = {"purple", "blue", "indigo", "gold"}
    # 旧配置无 subtitle 时，按标题回填内置默认小文案
    default_subs = {
        str(h.get("label") or ""): str(h.get("subtitle") or "")
        for h in base["highlightFeatures"]
        if isinstance(h, dict)
    }
    for i, entry in enumerate(raw.get("highlightFeatures") or raw.get("highlight_features") or []):
        if not isinstance(entry, dict):
            continue
        label = _as_str(entry.get("label"))
        if not label:
            continue
        tone = _as_str(entry.get("tone"), "purple")
        if tone not in allowed_tones:
            tone = "purple"
        # 小文案：有字段则尊重（含空串=不展示）；旧配置无字段时按标题回填默认
        if "subtitle" in entry or "subTitle" in entry or "description" in entry:
            subtitle = _as_str(
                entry.get("subtitle") or entry.get("subTitle") or entry.get("description")
            )
        else:
            subtitle = default_subs.get(label, "")
        highlights.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "label": label,
                "subtitle": subtitle[:64],
                "tone": tone,
                "isNew": _as_bool(entry.get("isNew", entry.get("is_new")), False),
                "href": _as_str(entry.get("href"), "/skills"),
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    highlights.sort(key=lambda x: (x["sortOrder"], x["id"]))

    machine_raw = raw.get("skillMachine") or raw.get("skill_machine") or {}
    if not isinstance(machine_raw, dict):
        machine_raw = {}
    skill_machine = {
        "label": _as_str(machine_raw.get("label"), base["skillMachine"]["label"]),
        "href": _as_str(machine_raw.get("href"), base["skillMachine"]["href"]),
        "imageUrl": _as_str(
            machine_raw.get("imageUrl") or machine_raw.get("image_url"),
            base["skillMachine"]["imageUrl"],
        ),
    }

    filters_raw = raw.get("galleryFilters") or raw.get("gallery_filters")
    if isinstance(filters_raw, list) and filters_raw:
        gallery_filters = [str(x).strip() for x in filters_raw if str(x).strip()]
    else:
        gallery_filters = list(base["galleryFilters"])
    if "全部" not in gallery_filters:
        gallery_filters.insert(0, "全部")

    gallery: list[dict[str, Any]] = []
    allowed_scopes = {"templates", "published", "purchased", "all"}
    for i, entry in enumerate(raw.get("galleryItems") or raw.get("gallery_items") or []):
        if not isinstance(entry, dict):
            continue
        title = _as_str(entry.get("title"))
        image_url = _as_str(entry.get("imageUrl") or entry.get("image_url"))
        if not title or not image_url:
            continue
        scope = _as_str(entry.get("scope"), "templates")
        if scope not in allowed_scopes:
            scope = "templates"
        gallery.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "title": title,
                "author": _as_str(entry.get("author"), "匿名"),
                "avatarUrl": _as_str(entry.get("avatarUrl") or entry.get("avatar_url")),
                "caption": _as_str(entry.get("caption"), title),
                "imageUrl": image_url,
                "category": _as_str(entry.get("category"), "全部"),
                "scope": scope,
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
                "isActive": _as_bool(entry.get("isActive", entry.get("is_active")), True),
            }
        )
    gallery.sort(key=lambda x: (x["sortOrder"], x["id"]))

    popup_raw = raw.get("cornerPopup") or raw.get("corner_popup") or {}
    if not isinstance(popup_raw, dict):
        popup_raw = {}
    ratio = _as_str(popup_raw.get("aspectRatio") or popup_raw.get("aspect_ratio"), "16:9")
    if ratio not in ("16:9", "9:16"):
        ratio = "16:9"
    corner_popup = {
        "enabled": _as_bool(popup_raw.get("enabled"), False),
        "text": _as_str(popup_raw.get("text"))[:2000],
        "imageUrl": _as_str(popup_raw.get("imageUrl") or popup_raw.get("image_url")),
        "imageFill": _as_bool(popup_raw.get("imageFill", popup_raw.get("image_fill")), False),
        "aspectRatio": ratio,
        "showWhenLoggedIn": _as_bool(
            popup_raw.get("showWhenLoggedIn", popup_raw.get("show_when_logged_in")),
            True,
        ),
        "showWhenLoggedOut": _as_bool(
            popup_raw.get("showWhenLoggedOut", popup_raw.get("show_when_logged_out")),
            True,
        ),
    }

    return {
        "heroTitle": _as_str(raw.get("heroTitle") or raw.get("hero_title"), base["heroTitle"]),
        "heroEm": _as_str(raw.get("heroEm") or raw.get("hero_em"), base["heroEm"]),
        "creationPlaceholder": _as_str(
            raw.get("creationPlaceholder") or raw.get("creation_placeholder"),
            base["creationPlaceholder"],
        ),
        "heroVideos": hero_videos,
        "promptSuggestions": prompts or base["promptSuggestions"],
        "storyFeature": story,
        "highlightFeatures": highlights or base["highlightFeatures"],
        "skillMachine": skill_machine,
        "galleryFilters": gallery_filters,
        "galleryItems": gallery or base["galleryItems"],
        "cornerPopup": corner_popup,
    }


def _resign_media_url(url: str) -> str:
    """能解析出 OSS key 则按当前模式重签（cdn 即稳定 CDN）；外链原样返回。"""
    value = str(url or "").strip()
    if not value:
        return value
    return normalize_browser_storage_url(value) or value


def _persist_discover_media_url(url: str) -> str:
    """落库发现页媒体：本站 OSS 写 CDN/公共 URL，禁止签权串；外链保留。"""
    value = str(url or "").strip()
    if not value:
        return value
    persisted = persistable_media_url(value)
    return persisted if persisted else value


def _persist_discover_page_media(payload: dict[str, Any]) -> dict[str, Any]:
    """保存前把可解析的本站媒体洗成稳定公共 URL。"""
    out = dict(payload)

    videos: list[dict[str, Any]] = []
    for item in out.get("heroVideos") or []:
        if not isinstance(item, dict):
            continue
        videos.append({**item, "url": _persist_discover_media_url(str(item.get("url") or ""))})
    out["heroVideos"] = videos

    prompts: list[dict[str, Any]] = []
    for item in out.get("promptSuggestions") or []:
        if not isinstance(item, dict):
            continue
        prompts.append(
            {**item, "imageUrl": _persist_discover_media_url(str(item.get("imageUrl") or ""))}
        )
    out["promptSuggestions"] = prompts

    story = out.get("storyFeature")
    if isinstance(story, dict):
        out["storyFeature"] = {
            **story,
            "imageUrl": _persist_discover_media_url(str(story.get("imageUrl") or "")),
        }

    machine = out.get("skillMachine")
    if isinstance(machine, dict):
        out["skillMachine"] = {
            **machine,
            "imageUrl": _persist_discover_media_url(str(machine.get("imageUrl") or "")),
        }

    gallery: list[dict[str, Any]] = []
    for item in out.get("galleryItems") or []:
        if not isinstance(item, dict):
            continue
        gallery.append(
            {
                **item,
                "imageUrl": _persist_discover_media_url(str(item.get("imageUrl") or "")),
                "avatarUrl": _persist_discover_media_url(str(item.get("avatarUrl") or "")),
            }
        )
    out["galleryItems"] = gallery
    popup = out.get("cornerPopup")
    if isinstance(popup, dict):
        out["cornerPopup"] = {
            **popup,
            "imageUrl": _persist_discover_media_url(str(popup.get("imageUrl") or "")),
        }
    return out


def resign_discover_page_media(payload: dict[str, Any]) -> dict[str, Any]:
    """读取/返回前重签发现页 OSS 媒体 URL，避免库内过期签名导致浏览器 403。"""
    out = dict(payload)

    videos: list[dict[str, Any]] = []
    for item in out.get("heroVideos") or []:
        if not isinstance(item, dict):
            continue
        videos.append({**item, "url": _resign_media_url(str(item.get("url") or ""))})
    out["heroVideos"] = videos

    prompts: list[dict[str, Any]] = []
    for item in out.get("promptSuggestions") or []:
        if not isinstance(item, dict):
            continue
        prompts.append(
            {**item, "imageUrl": _resign_media_url(str(item.get("imageUrl") or ""))}
        )
    out["promptSuggestions"] = prompts

    story = out.get("storyFeature")
    if isinstance(story, dict):
        out["storyFeature"] = {
            **story,
            "imageUrl": _resign_media_url(str(story.get("imageUrl") or "")),
        }

    machine = out.get("skillMachine")
    if isinstance(machine, dict):
        out["skillMachine"] = {
            **machine,
            "imageUrl": _resign_media_url(str(machine.get("imageUrl") or "")),
        }

    gallery: list[dict[str, Any]] = []
    for item in out.get("galleryItems") or []:
        if not isinstance(item, dict):
            continue
        gallery.append(
            {
                **item,
                "imageUrl": _resign_media_url(str(item.get("imageUrl") or "")),
                "avatarUrl": _resign_media_url(str(item.get("avatarUrl") or "")),
            }
        )
    out["galleryItems"] = gallery
    popup = out.get("cornerPopup")
    if isinstance(popup, dict):
        out["cornerPopup"] = {
            **popup,
            "imageUrl": _resign_media_url(str(popup.get("imageUrl") or "")),
        }
    return out


async def get_discover_page_settings(db: AsyncSession) -> dict[str, Any]:
    """读取发现页配置；库中为空时返回默认并带 updated_at。"""
    row = await _ensure_settings_row(db)
    raw = getattr(row, "discover_page", None)
    payload = resign_discover_page_media(normalize_discover_page(raw))
    return {
        **payload,
        "updatedAt": row.updated_at,
    }


async def get_publication_category_options(db: AsyncSession) -> list[str]:
    """作品广场 / 发布 / 审核用的分类选项（不含「全部」）。

    权威源：发现页运营配置 galleryFilters（管理端「发现页」与「工作流发布·分类设置」共用）。
    """
    settings = await get_discover_page_settings(db)
    filters = settings.get("galleryFilters") or []
    out: list[str] = []
    seen: set[str] = set()
    for item in filters:
        name = str(item or "").strip()
        if not name or name == "全部" or name in seen:
            continue
        seen.add(name)
        out.append(name[:32])
    if out:
        return out
    # 兜底与默认 galleryFilters 一致
    return [
        c
        for c in (default_discover_page()["galleryFilters"] or [])
        if c and c != "全部"
    ]


async def set_discover_page_settings(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """整表保存发现页配置。"""
    row = await _ensure_settings_row(db)
    normalized = _persist_discover_page_media(normalize_discover_page(payload))
    # 落库不含 updatedAt；媒体字段已洗成 CDN/公共 URL
    row.discover_page = {k: v for k, v in normalized.items()}
    row.updated_at = now_cst_naive()
    await db.flush()
    return {
        **resign_discover_page_media(normalized),
        "updatedAt": row.updated_at,
    }


def activity_cover_fallback(index: int) -> str:
    """算力活动无封面时的默认图。"""
    return _DEFAULT_ACTIVITY_COVERS[index % len(_DEFAULT_ACTIVITY_COVERS)]
