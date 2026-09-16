"""工作流发布服务基础单测（分类校验 / 资产收集）。"""

from __future__ import annotations

from app.services.asset_copy import collect_asset_ids_from_json, remap_asset_ids_in_json
from app.services.workflow_publications import PUBLICATION_CATEGORIES


def test_publication_categories_match_gallery_filters_without_all():
    assert "全部" not in PUBLICATION_CATEGORIES
    assert "短剧漫剧" in PUBLICATION_CATEGORIES
    assert len(PUBLICATION_CATEGORIES) == 9


def test_collect_asset_ids_from_flow_json():
    flow = {
        "nodes": [
            {"id": "a", "data": {"params": {"assetId": "11"}}},
            {"id": "b", "data": {"params": {"asset_id": "22", "imageUrl": "https://x"}}},
        ],
        "extra": {"assetId": "11"},
    }
    ids = collect_asset_ids_from_json(flow)
    assert ids == ["11", "22"]


def test_remap_clears_urls_and_maps_ids():
    flow = {
        "nodes": [
            {
                "id": "a",
                "data": {
                    "params": {
                        "assetId": "11",
                        "imageUrl": "https://signed.example/x",
                    }
                },
            }
        ]
    }
    out = remap_asset_ids_in_json(flow, {"11": "99"})
    params = out["nodes"][0]["data"]["params"]
    assert params["assetId"] == "99"
    assert "imageUrl" not in params
