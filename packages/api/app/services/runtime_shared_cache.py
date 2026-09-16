"""跨进程运行时快照：模型目录 + 密钥配置写入 Redis，API/Worker 分机部署时共享。

- 模型目录：List 存 name 索引 + 每条 spec 独立 SET（cache:model:catalog:spec:{name}:runtime）
- 密钥：单 key SET 存整包 LlmKeysConfig（cache:credential:runtime:snapshot:default）
- DB 写后由 warm/publish 同步；各进程 refresh 时从 Redis 拉取到本地 L1，避免仅进程内快照不一致。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from ..common.utils.redis_cache import get_cache_version
from ..common.utils.redis_keys import build_cache_key
from ..common.utils.redis_store import delete_keys, lrange_all, optional_redis_client, replace_list
from ..core.llm_keys import LlmKeysConfig
from ..core.model_registry import CanvasModelSpec, Capability, VideoMode

logger = logging.getLogger(__name__)

# 运行时共享缓存 TTL（秒）；仍以 cache:ver 为主失效手段
RUNTIME_SHARED_CACHE_TTL_SEC = 7 * 24 * 3600

CATALOG_NAMES_LIST_KEY = build_cache_key("model", "catalog", "names")


def catalog_spec_key(model_name: str) -> str:
    return build_cache_key("model", f"catalog:spec:{model_name}", "runtime")


CREDENTIAL_SNAPSHOT_KEY = build_cache_key("credential", "runtime", "snapshot")


def _envelope(ver: int, data: Any) -> str:
    return json.dumps({"ver": ver, "data": data}, ensure_ascii=False)


def _parse_envelope(raw: str, *, expected_ver: int) -> Any | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("ver") != expected_ver:
        return None
    return payload.get("data")


def model_spec_to_dict(spec: CanvasModelSpec) -> dict[str, Any]:
    d = asdict(spec)
    d["capabilities"] = list(spec.capabilities)
    return d


def model_spec_from_dict(data: dict[str, Any]) -> CanvasModelSpec:
    caps_raw = data.get("capabilities") or []
    capabilities: tuple[Capability, ...] = tuple(
        c for c in caps_raw if isinstance(c, str) and c
    )  # type: ignore[return-value]

    video_mode_raw = data.get("video_mode")
    video_mode: VideoMode | None = None
    if isinstance(video_mode_raw, str) and video_mode_raw in ("r2v", "i2v", "lip_sync", "t2v"):
        video_mode = video_mode_raw  # type: ignore[assignment]

    impl_raw = str(data.get("implementation") or "reserved").lower()
    implementation = impl_raw if impl_raw in ("live", "reserved") else "reserved"

    params_extra = data.get("parameters_extra")
    if not isinstance(params_extra, dict):
        params_extra = {}

    return CanvasModelSpec(
        name=str(data["name"]),
        display_name=str(data.get("display_name") or data["name"]),
        provider=data.get("provider") or "comfyui",  # type: ignore[arg-type]
        model_type=str(data.get("model_type") or "checkpoint"),
        category=data.get("category") or "image",  # type: ignore[arg-type]
        description=str(data.get("description") or ""),
        sort_order=int(data.get("sort_order") or 0),
        upstream_model=str(data.get("upstream_model") or ""),
        capabilities=capabilities,
        video_mode=video_mode,
        parameters_extra=params_extra,
        implementation=implementation,  # type: ignore[arg-type]
    )


async def publish_model_catalog_to_redis(
    specs: dict[str, CanvasModelSpec],
    *,
    ver: int | None = None,
) -> bool:
    """DB warm 后发布全量模型规格到 Redis（List 索引 + 逐条 SET）。

    注意：多 key 写入禁止用 transaction=True（Cluster 下会 CROSSSLOT 失败），
    否则管理端启停模型等写操作会整单回滚。
    """
    client = await optional_redis_client()
    if client is None:
        return False

    version = ver if ver is not None else await get_cache_version("model")
    old_names = await lrange_all(CATALOG_NAMES_LIST_KEY)
    new_names = sorted(specs.keys())

    # 非事务 pipeline：逐 key SET/DEL，避免 Redis Cluster CROSSSLOT
    pipe = client.pipeline(transaction=False)
    for name, spec in specs.items():
        pipe.set(
            catalog_spec_key(name),
            _envelope(version, model_spec_to_dict(spec)),
            ex=RUNTIME_SHARED_CACHE_TTL_SEC,
        )
    removed = set(old_names) - set(new_names)
    for name in removed:
        pipe.delete(catalog_spec_key(name))
    await pipe.execute()

    await replace_list(CATALOG_NAMES_LIST_KEY, new_names, ttl=RUNTIME_SHARED_CACHE_TTL_SEC)
    logger.info(
        "Published model catalog to Redis: %s model(s), ver=%s",
        len(new_names),
        version,
    )
    return True


async def load_model_catalog_from_redis() -> dict[str, CanvasModelSpec] | None:
    """从 Redis 加载全量模型规格；版本不一致或 Redis 不可用时返回 None。"""
    client = await optional_redis_client()
    if client is None:
        return None

    ver = await get_cache_version("model")
    names = await lrange_all(CATALOG_NAMES_LIST_KEY)
    if not names:
        return None

    keys = [catalog_spec_key(name) for name in names]
    raw_values = await client.mget(keys)
    loaded: dict[str, CanvasModelSpec] = {}
    for name, raw in zip(names, raw_values, strict=False):
        if not raw:
            logger.warning("Missing Redis catalog spec for model %s", name)
            return None
        data = _parse_envelope(raw, expected_ver=ver)
        if not isinstance(data, dict):
            return None
        try:
            loaded[name] = model_spec_from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Invalid Redis catalog spec for %s: %s", name, exc)
            return None
    return loaded


async def publish_llm_keys_to_redis(
    cfg: LlmKeysConfig,
    *,
    ver: int | None = None,
) -> bool:
    """发布整包密钥配置到 Redis（VPC 内共享；与进程内快照同等敏感级别）。"""
    client = await optional_redis_client()
    if client is None:
        return False

    version = ver if ver is not None else await get_cache_version("credential")
    await client.set(
        CREDENTIAL_SNAPSHOT_KEY,
        _envelope(version, cfg.model_dump()),
        ex=RUNTIME_SHARED_CACHE_TTL_SEC,
    )
    logger.info("Published LLM keys snapshot to Redis, ver=%s", version)
    return True


async def load_llm_keys_from_redis() -> LlmKeysConfig | None:
    """从 Redis 加载密钥整包快照。"""
    client = await optional_redis_client()
    if client is None:
        return None

    ver = await get_cache_version("credential")
    raw = await client.get(CREDENTIAL_SNAPSHOT_KEY)
    if not raw:
        return None
    data = _parse_envelope(raw, expected_ver=ver)
    if not isinstance(data, dict):
        return None
    try:
        return LlmKeysConfig.model_validate(data)
    except Exception as exc:
        logger.warning("Invalid Redis credential snapshot: %s", exc)
        return None


async def clear_runtime_shared_cache() -> None:
    """密钥失效时清理 Redis 运行时快照（模型目录靠 bump model 版本自然失效）。"""
    names = await lrange_all(CATALOG_NAMES_LIST_KEY)
    keys = [CREDENTIAL_SNAPSHOT_KEY, CATALOG_NAMES_LIST_KEY]
    keys.extend(catalog_spec_key(name) for name in names)
    await delete_keys(*keys)
