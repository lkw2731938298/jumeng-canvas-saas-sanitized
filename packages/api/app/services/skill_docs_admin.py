"""管理端 Skill MD 文档覆盖：读写 platform_settings.skill_docs。

支持：
- 单文件 Skill（viral_remake / overseas_localize）：字符串覆盖
- 多文件技能包（product_cinematic_commercial / pov_tearjerker_short 等）：{files:{rel_path: md}}
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .platform_settings import _ensure_settings_row
from .skill_docs import (
    ALLOWED_SKILL_DOC_SLUGS,
    SKILL_DOC_PACKAGE_ENTRY,
    _safe_package_rel_path,
    _safe_slug,
    clear_skill_doc_cache,
    is_skill_doc_package,
    list_skill_doc_package_files_from_disk,
    load_skill_doc_from_file,
    load_skill_doc_resolved,
    parse_skill_markdown,
    read_skill_doc_package_file_from_disk,
    get_skill_doc_package_overrides,
)


def _require_allowed_slug(slug: str) -> str:
    safe = _safe_slug(slug)
    if safe not in ALLOWED_SKILL_DOC_SLUGS:
        fail(ErrorCode.VALIDATION_ERROR, message="不支持的 Skill 文档 slug")
    return safe


def _pipeline_step_count(doc: dict[str, Any] | None) -> int:
    if not doc:
        return 0
    pipe = doc.get("pipeline")
    if isinstance(pipe, dict):
        steps = pipe.get("steps") or []
    else:
        steps = pipe or []
    return len(steps) if isinstance(steps, list) else 0


def _build_file_tree(paths: list[str]) -> list[dict[str, Any]]:
    """把相对路径列表建成简易树（供管理端目录展示）。"""
    root: dict[str, Any] = {"name": "", "path": "", "type": "dir", "children": {}}

    def ensure_dir(node: dict[str, Any], name: str, path: str) -> dict[str, Any]:
        kids = node.setdefault("children", {})
        if name not in kids:
            kids[name] = {"name": name, "path": path, "type": "dir", "children": {}}
        return kids[name]

    for rel in paths:
        parts = rel.split("/")
        node = root
        acc: list[str] = []
        for i, part in enumerate(parts):
            acc.append(part)
            cur = "/".join(acc)
            if i == len(parts) - 1:
                kids = node.setdefault("children", {})
                kids[part] = {"name": part, "path": cur, "type": "file"}
            else:
                node = ensure_dir(node, part, cur)

    def to_list(node: dict[str, Any]) -> list[dict[str, Any]]:
        kids = node.get("children") or {}
        out: list[dict[str, Any]] = []
        for name in sorted(kids.keys(), key=lambda n: (kids[n].get("type") != "dir", n)):
            child = kids[name]
            if child.get("type") == "dir":
                out.append(
                    {
                        "name": child["name"],
                        "path": child["path"],
                        "type": "dir",
                        "children": to_list(child),
                    }
                )
            else:
                out.append({"name": child["name"], "path": child["path"], "type": "file"})
        return out

    return to_list(root)


def _admin_payload(doc: dict[str, Any], *, source: str, has_override: bool) -> dict[str, Any]:
    """管理端详情：全文 + 解析摘要。"""
    pipe = doc.get("pipeline")
    steps = pipe.get("steps") if isinstance(pipe, dict) else pipe
    execution = doc.get("execution") or ""
    entry_kind = doc.get("entryKind") or ""
    if isinstance(pipe, dict):
        execution = execution or str(pipe.get("execution") or "")
        entry_kind = entry_kind or str(pipe.get("entryKind") or "")
    return {
        "slug": doc.get("slug"),
        "title": doc.get("title") or doc.get("name"),
        "description": doc.get("description") or "",
        "summary": doc.get("summary") or "",
        "document": doc.get("raw") or "",
        "markdown": doc.get("markdown") or "",
        "pipeline": steps or [],
        "pipelineStepCount": _pipeline_step_count(doc),
        "execution": execution,
        "entryKind": entry_kind,
        "source": source,
        "hasOverride": has_override,
        "package": bool(doc.get("package")),
        "activePath": SKILL_DOC_PACKAGE_ENTRY if doc.get("package") else (doc.get("path") or f"{doc.get('slug')}.md"),
        "files": [],
        "tree": [],
    }


async def list_skill_docs_for_admin(db: AsyncSession) -> list[dict[str, Any]]:
    """列表：slug / title / source / 是否覆盖 / 是否技能包。"""
    items: list[dict[str, Any]] = []
    pkg_ov = await get_skill_doc_package_overrides(db)
    for slug in ALLOWED_SKILL_DOC_SLUGS:
        doc = await load_skill_doc_resolved(db, slug)
        package = is_skill_doc_package(slug) or bool(pkg_ov.get(slug))
        file_count = 0
        if package:
            disk = list_skill_doc_package_files_from_disk(slug)
            ov_files = pkg_ov.get(slug) or {}
            paths = {f["path"] for f in disk} | set(ov_files.keys())
            file_count = len(paths) or 1
        if doc is None:
            items.append(
                {
                    "slug": slug,
                    "title": slug,
                    "source": "missing",
                    "hasOverride": False,
                    "pipelineStepCount": 0,
                    "summary": "",
                    "package": package,
                    "fileCount": file_count,
                }
            )
            continue
        items.append(
            {
                "slug": doc.get("slug") or slug,
                "title": doc.get("title") or doc.get("name") or slug,
                "source": doc.get("source") or "file",
                "hasOverride": doc.get("source") == "override" or bool(pkg_ov.get(slug)),
                "pipelineStepCount": _pipeline_step_count(doc),
                "summary": doc.get("summary") or "",
                "package": package,
                "fileCount": file_count if package else 1,
            }
        )
    return items


async def _resolve_package_files(db: AsyncSession, slug: str) -> list[dict[str, str]]:
    """合并磁盘默认 + 库覆盖，生成文件列表。"""
    disk = {f["path"]: f["content"] for f in list_skill_doc_package_files_from_disk(slug)}
    pkg_ov = await get_skill_doc_package_overrides(db)
    ov = pkg_ov.get(slug) or {}
    paths = sorted(
        set(disk.keys()) | set(ov.keys()),
        key=lambda p: (0 if p == SKILL_DOC_PACKAGE_ENTRY else 1, p),
    )
    if not paths and not is_skill_doc_package(slug):
        # 单文件：合成一条
        doc = await load_skill_doc_resolved(db, slug)
        if doc and (doc.get("raw") or "").strip():
            return [
                {
                    "path": f"{slug}.md",
                    "content": str(doc.get("raw") or ""),
                    "source": str(doc.get("source") or "file"),
                }
            ]
        return []
    out: list[dict[str, str]] = []
    for path in paths:
        if path in ov:
            out.append({"path": path, "content": ov[path], "source": "override"})
        else:
            out.append({"path": path, "content": disk.get(path) or "", "source": "file"})
    return out


async def get_skill_doc_for_admin(
    db: AsyncSession,
    slug: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """管理端读取：当前生效全文 + 若为技能包则附目录树与全部文件。"""
    safe = _require_allowed_slug(slug)
    doc = await load_skill_doc_resolved(db, safe)
    if doc is None and not is_skill_doc_package(safe):
        fail(ErrorCode.SKILL_NOT_FOUND, message="该 Skill 暂无说明文档")

    package = is_skill_doc_package(safe)
    pkg_ov = await get_skill_doc_package_overrides(db)
    has_override = (doc or {}).get("source") == "override" or bool(pkg_ov.get(safe))
    if doc is None:
        # 包存在但 SKILL 缺失时仍返回壳
        fail(ErrorCode.SKILL_NOT_FOUND, message="该 Skill 暂无说明文档")

    payload = _admin_payload(
        doc,
        source=str(doc.get("source") or "file"),
        has_override=has_override,
    )
    payload["package"] = package or bool(pkg_ov.get(safe))

    files = await _resolve_package_files(db, safe)
    payload["files"] = [
        {"path": f["path"], "source": f["source"], "size": len(f.get("content") or "")}
        for f in files
    ]
    payload["tree"] = _build_file_tree([f["path"] for f in files]) if files else []

    # 当前编辑文件
    want = _safe_package_rel_path(path or "") if path else None
    if payload["package"]:
        active = want or SKILL_DOC_PACKAGE_ENTRY
        hit = next((f for f in files if f["path"] == active), None)
        if hit is None and files:
            hit = files[0]
            active = hit["path"]
        if hit:
            payload["activePath"] = active
            payload["document"] = hit["content"]
            payload["fileSource"] = hit["source"]
            if active == SKILL_DOC_PACKAGE_ENTRY:
                # 保持解析摘要来自 SKILL.md
                pass
            else:
                payload["summary"] = f"包内文件：{active}"
        else:
            payload["activePath"] = SKILL_DOC_PACKAGE_ENTRY
    else:
        payload["activePath"] = f"{safe}.md"
        payload["fileSource"] = payload["source"]

    payload["fileCount"] = len(files) if files else 1
    return payload


async def set_skill_doc_override(db: AsyncSession, slug: str, document: str) -> dict[str, Any]:
    """保存单文件覆盖（兼容旧接口；技能包默认写到 SKILL.md）。"""
    return await set_skill_doc_file_override(
        db, slug, path=SKILL_DOC_PACKAGE_ENTRY if is_skill_doc_package(slug) else f"{slug}.md", document=document
    )


async def set_skill_doc_file_override(
    db: AsyncSession,
    slug: str,
    *,
    path: str,
    document: str,
) -> dict[str, Any]:
    """保存覆盖：单文件写字符串；技能包写 files 映射中的某一路径。"""
    safe = _require_allowed_slug(slug)
    text = (document or "").strip()
    if not text:
        fail(ErrorCode.VALIDATION_ERROR, message="文档内容不能为空")

    package = is_skill_doc_package(safe)
    rel = _safe_package_rel_path(path) if package else None
    if package:
        if not rel:
            fail(ErrorCode.VALIDATION_ERROR, message="非法的包内文件路径")
        # SKILL.md 必须可解析
        if rel == SKILL_DOC_PACKAGE_ENTRY:
            try:
                parsed = parse_skill_markdown(text, slug=safe)
            except Exception:
                fail(ErrorCode.VALIDATION_ERROR, message="SKILL.md 解析失败，请检查 Markdown 格式")
            if not (parsed.get("raw") or "").strip():
                fail(ErrorCode.VALIDATION_ERROR, message="文档内容不能为空")
        # 确认路径在包内或已是覆盖键
        disk_paths = {f["path"] for f in list_skill_doc_package_files_from_disk(safe)}
        pkg_ov = await get_skill_doc_package_overrides(db)
        known = disk_paths | set((pkg_ov.get(safe) or {}).keys())
        if rel not in known and rel != SKILL_DOC_PACKAGE_ENTRY:
            fail(ErrorCode.VALIDATION_ERROR, message="只能编辑技能包内已有文件")

        row = await _ensure_settings_row(db)
        current = dict(row.skill_docs) if isinstance(row.skill_docs, dict) else {}
        # 以磁盘为底合并已有覆盖
        base_files = {f["path"]: f["content"] for f in list_skill_doc_package_files_from_disk(safe)}
        existing = pkg_ov.get(safe) or {}
        merged = {**base_files, **existing, rel: text}
        current[safe] = {"_package": True, "files": merged}
        # 清理非法 key，保留字符串或包 dict
        cleaned: dict[str, Any] = {}
        for k, v in current.items():
            sk = _safe_slug(str(k))
            if sk not in ALLOWED_SKILL_DOC_SLUGS:
                continue
            if isinstance(v, str) and v.strip():
                cleaned[sk] = v
            elif isinstance(v, dict):
                cleaned[sk] = v
        row.skill_docs = cleaned
        row.updated_at = now_cst_naive()
        await db.flush()
        clear_skill_doc_cache()
        return await get_skill_doc_for_admin(db, safe, path=rel)

    # 单文件
    try:
        parsed = parse_skill_markdown(text, slug=safe)
    except Exception:
        fail(ErrorCode.VALIDATION_ERROR, message="文档解析失败，请检查 Markdown 格式")
    if not (parsed.get("raw") or "").strip():
        fail(ErrorCode.VALIDATION_ERROR, message="文档内容不能为空")

    row = await _ensure_settings_row(db)
    current = dict(row.skill_docs) if isinstance(row.skill_docs, dict) else {}
    current[safe] = text
    cleaned: dict[str, Any] = {}
    for k, v in current.items():
        sk = _safe_slug(str(k))
        if sk not in ALLOWED_SKILL_DOC_SLUGS:
            continue
        if isinstance(v, str) and v.strip():
            cleaned[sk] = v
        elif isinstance(v, dict):
            cleaned[sk] = v
    row.skill_docs = cleaned
    row.updated_at = now_cst_naive()
    await db.flush()
    clear_skill_doc_cache()
    return await get_skill_doc_for_admin(db, safe)


async def reset_skill_doc_override(db: AsyncSession, slug: str) -> dict[str, Any]:
    """删除覆盖 key，恢复仓库文件/技能包默认。"""
    safe = _require_allowed_slug(slug)
    row = await _ensure_settings_row(db)
    current = dict(row.skill_docs) if isinstance(row.skill_docs, dict) else {}
    if safe in current:
        del current[safe]
        row.skill_docs = current or None
        row.updated_at = now_cst_naive()
        await db.flush()
    clear_skill_doc_cache()
    file_doc = load_skill_doc_from_file(safe)
    if file_doc is None and not is_skill_doc_package(safe):
        fail(ErrorCode.SKILL_NOT_FOUND, message="仓库中无该 Skill 默认文档")
    if file_doc is None and is_skill_doc_package(safe):
        # 包存在但 SKILL 读失败
        if not read_skill_doc_package_file_from_disk(safe, SKILL_DOC_PACKAGE_ENTRY):
            fail(ErrorCode.SKILL_NOT_FOUND, message="仓库中无该 Skill 默认文档")
    return await get_skill_doc_for_admin(db, safe)
