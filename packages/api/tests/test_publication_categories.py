"""作品广场分类：权威读自发现页 galleryFilters。"""

from app.services.discover_page import (
    default_discover_page,
    get_publication_category_options,
    normalize_discover_page,
)


def test_normalize_corner_popup_defaults_and_ratio():
    raw = normalize_discover_page({})
    popup = raw["cornerPopup"]
    assert popup["enabled"] is False
    assert popup["aspectRatio"] == "16:9"
    assert popup["showWhenLoggedIn"] is True
    filled = normalize_discover_page(
        {
            "cornerPopup": {
                "enabled": True,
                "text": "hello",
                "imageFill": True,
                "aspectRatio": "9:16",
                "showWhenLoggedOut": False,
            }
        }
    )
    p2 = filled["cornerPopup"]
    assert p2["enabled"] is True
    assert p2["text"] == "hello"
    assert p2["imageFill"] is True
    assert p2["aspectRatio"] == "9:16"
    assert p2["showWhenLoggedOut"] is False

    raw = normalize_discover_page({"galleryFilters": ["电商带货", "短剧漫剧"]})
    assert raw["galleryFilters"][0] == "全部"
    assert "电商带货" in raw["galleryFilters"]


def test_default_categories_exclude_all():
    filters = default_discover_page()["galleryFilters"]
    assert "全部" in filters
    cats = [c for c in filters if c != "全部"]
    assert "短剧漫剧" in cats
    assert "全部" not in cats


async def _fake_settings_with_filters(db, filters):  # noqa: ARG001
    return {"galleryFilters": filters}


def test_get_publication_category_options_strips_all(monkeypatch):
    import asyncio
    from app.services import discover_page as dp

    async def fake_get(_db):
        return {"galleryFilters": ["全部", "短剧漫剧", "自定义类"]}

    monkeypatch.setattr(dp, "get_discover_page_settings", fake_get)
    cats = asyncio.run(get_publication_category_options(None))  # type: ignore[arg-type]
    assert cats == ["短剧漫剧", "自定义类"]
