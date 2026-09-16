"""从 skill_docs/*.md 加载 Skill 说明；支持 platform_settings.skill_docs 覆盖。

对齐 LibTV SKILL.md 用法：文件为默认/可恢复；运行时优先读库覆盖。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

_DOCS_DIR = Path(__file__).resolve().parent.parent / "skill_docs"
# 对外别名（openai.yaml / 导入等）
SKILL_DOCS_DIR = _DOCS_DIR

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
# ```yaml ... ``` 或 ```yml ... ```
_YAML_FENCE_RE = re.compile(
    r"```(?:yaml|yml)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)

# 允许后台覆盖的 Skill 文档 slug（与种子 / 管理页一致）
ALLOWED_SKILL_DOC_SLUGS: tuple[str, ...] = (
    "viral_remake",
    "overseas_localize",
    "product_cinematic_commercial",
    "pov_tearjerker_short",
)

# 多文件技能包：仓库目录 skill_docs/{slug}/（含 SKILL.md）
SKILL_DOC_PACKAGE_ENTRY = "SKILL.md"
_SAFE_REL_PATH_RE = re.compile(r"^[A-Za-z0-9_./\-]+\.(md|MD)$")


def _safe_slug(slug: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", (slug or "").strip())


def _safe_package_rel_path(path: str) -> str | None:
    """包内相对路径：仅允许 md，禁止 .. 与绝对路径。"""
    raw = (path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    if not _SAFE_REL_PATH_RE.match(raw):
        return None
    return raw


def skill_doc_package_dir(slug: str) -> Path | None:
    safe = _safe_slug(slug)
    if not safe:
        return None
    d = _DOCS_DIR / safe
    return d if d.is_dir() else None


def list_skill_doc_package_files_from_disk(slug: str) -> list[dict[str, str]]:
    """列出仓库技能包内全部 .md（相对路径 + 内容）。"""
    root = skill_doc_package_dir(slug)
    if root is None:
        return []
    items: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.md")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        safe_rel = _safe_package_rel_path(rel)
        if not safe_rel:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        items.append({"path": safe_rel, "content": content, "source": "file"})
    # SKILL.md 优先排最前
    items.sort(key=lambda x: (0 if x["path"] == SKILL_DOC_PACKAGE_ENTRY else 1, x["path"]))
    return items


def read_skill_doc_package_file_from_disk(slug: str, rel_path: str) -> str | None:
    root = skill_doc_package_dir(slug)
    safe_rel = _safe_package_rel_path(rel_path)
    if root is None or not safe_rel:
        return None
    path = root / safe_rel
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def is_skill_doc_package(slug: str) -> bool:
    """是否存在多文件技能包目录（含 SKILL.md）。"""
    root = skill_doc_package_dir(slug)
    return bool(root and (root / SKILL_DOC_PACKAGE_ENTRY).is_file())


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """解析简易 YAML frontmatter（key: value 行）。"""
    text = raw.lstrip("\ufeff")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta, m.group(2).strip()


def _parse_simple_yaml_pipeline(yaml_text: str) -> dict[str, Any] | None:
    """
    解析 Skill pipeline YAML（无 PyYAML 依赖的最小子集）。
    支持：
      execution: client_wizard
      entryKind: viral_remake
      steps:
        - agent: scriptwriter
          action: storyboard_from_video
          trigger: on_video_upload
          clientAction: run_analyze
    """
    text = (yaml_text or "").strip()
    if not text:
        return None
    execution = ""
    entry_kind = ""
    steps: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_steps = False

    for raw_line in text.splitlines():
        # 去掉行内注释（简易）
        if "#" in raw_line and '"' not in raw_line and "'" not in raw_line:
            raw_line = raw_line.partition("#")[0]
        line = raw_line.rstrip()
        if not line.strip():
            continue
        stripped = line.strip()

        if re.match(r"^steps\s*:", stripped):
            in_steps = True
            continue

        m_exec = re.match(r"^execution\s*:\s*(.+)$", stripped)
        if m_exec and not in_steps:
            execution = m_exec.group(1).strip().strip('"').strip("'")
            continue
        m_kind = re.match(r"^entryKind\s*:\s*(.+)$", stripped, re.I)
        if m_kind and not in_steps:
            entry_kind = m_kind.group(1).strip().strip('"').strip("'")
            continue

        if not in_steps:
            continue

        # 新 step：- agent: xxx
        dash = re.match(r"^-\s*(?:agent\s*:\s*(.+))?$", stripped)
        if dash:
            if current:
                steps.append(current)
            current = {}
            if dash.group(1):
                current["agent"] = dash.group(1).strip().strip('"').strip("'")
            continue

        if current is None:
            continue
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", stripped)
        if kv:
            key = kv.group(1).strip()
            val = kv.group(2).strip().strip('"').strip("'")
            current[key] = val

    if current:
        steps.append(current)

    if not steps and not execution and not entry_kind:
        return None
    out: dict[str, Any] = {"steps": steps}
    if execution:
        out["execution"] = execution
    if entry_kind:
        out["entryKind"] = entry_kind
    return out


def parse_pipeline_from_markdown(raw: str) -> dict[str, Any] | None:
    """从 MD 中提取第一个 yaml fence 作为 pipeline；失败则尝试 prose 列表。"""
    text = (raw or "").lstrip("\ufeff")
    section = text
    m_sec = re.search(r"\n##\s*pipeline\s*\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL | re.I)
    if m_sec:
        section = m_sec.group(1)
    for m in _YAML_FENCE_RE.finditer(section):
        parsed = _parse_simple_yaml_pipeline(m.group(1))
        if parsed and parsed.get("steps"):
            return parsed
    for m in _YAML_FENCE_RE.finditer(text):
        parsed = _parse_simple_yaml_pipeline(m.group(1))
        if parsed and parsed.get("steps"):
            return parsed

    # 人读列表：1. scriptwriter → storyboard_from_video
    prose_steps: list[dict[str, str]] = []
    for line in (section or text).splitlines():
        m = re.search(
            r"([a-z][a-z0-9_]*)\s*(?:→|->)\s*([a-z][a-z0-9_]*)",
            line.strip(),
            re.I,
        )
        if m:
            prose_steps.append({"agent": m.group(1), "action": m.group(2)})
    if prose_steps:
        return {"steps": prose_steps}
    return None


def parse_skill_markdown(raw: str, *, slug: str = "") -> dict[str, Any]:
    """解析完整 SKILL.md（含 frontmatter / summary / pipeline）。

    兼容 Agent Skills：顶层 ``name`` / ``description``；聚梦扩展可读
    ``slug`` / ``execution`` / ``entryKind``，或扁平 ``jumeng.slug`` 等
    （简易 frontmatter 解析会把 ``metadata:`` 下缩进行收成同级键）。
    """
    safe = _safe_slug(slug) or "skill"
    meta, body = _parse_frontmatter(raw)
    summary = ""
    prefer = ""
    for part in re.split(r"\n(?=##\s)", body):
        chunk = part.strip()
        if not chunk:
            continue
        title_m = re.match(r"^##\s*(.+)", chunk)
        section_title = title_m.group(1).strip() if title_m else ""
        lines = [
            ln.strip()
            for ln in chunk.splitlines()
            if ln.strip()
            and not ln.strip().startswith("#")
            and not ln.strip().startswith("```")
        ]
        text_block = "\n".join(lines[:8]).strip()
        if not text_block:
            continue
        if not summary:
            summary = text_block
        if "使用步骤" in section_title or "何时使用" in section_title:
            prefer = text_block
            break
    summary = prefer or summary
    if len(summary) > 400:
        summary = summary[:397] + "…"
    pipeline = parse_pipeline_from_markdown(raw)
    name = str(meta.get("name") or "").strip() or safe
    # 标准 name 用连字符；仓库目录/slug 用下划线
    slug_val = (
        meta.get("slug")
        or meta.get("jumeng.slug")
        or (_safe_slug(name.replace("-", "_")) if name else "")
        or safe
    )
    return {
        "slug": slug_val,
        "name": name,
        "title": meta.get("title") or meta.get("name") or safe,
        "description": meta.get("description") or "",
        "execution": meta.get("execution")
        or meta.get("jumeng.execution")
        or "",
        "entryKind": meta.get("entryKind")
        or meta.get("entry_kind")
        or meta.get("jumeng.entryKind")
        or "",
        "summary": summary,
        "markdown": body,
        "pipeline": pipeline,
        "raw": raw,
    }


def resolve_skill_lookup_slug(name_or_slug: str) -> str:
    """把 Agent Skills ``name``（连字符）或本系统 ``slug``（下划线）归一成仓库 slug。"""
    raw = (name_or_slug or "").strip()
    if not raw:
        return ""
    safe = _safe_slug(raw)
    underscored = _safe_slug(raw.replace("-", "_"))
    # 平台包优先按下划线目录名解析
    for cand in (underscored, safe):
        if not cand:
            continue
        if cand in ALLOWED_SKILL_DOC_SLUGS or is_skill_doc_package(cand):
            return cand
        if load_skill_doc_from_file(cand) is not None:
            return cand
    # 按 frontmatter name 反查平台包
    needle = safe.replace("_", "-").lower()
    for plat in ALLOWED_SKILL_DOC_SLUGS:
        doc = load_skill_doc_from_file(plat)
        if not doc:
            continue
        nm = str(doc.get("name") or "").strip().lower()
        if nm and (nm == needle or nm.replace("-", "_") == underscored):
            return plat
    return underscored or safe


@lru_cache(maxsize=32)
def load_skill_doc_from_file(slug: str) -> dict[str, Any] | None:
    """按 slug 读盘；优先 {slug}/SKILL.md 技能包，其次 {slug}.md 单文件。"""
    safe = _safe_slug(slug)
    if not safe:
        return None
    package_skill = _DOCS_DIR / safe / SKILL_DOC_PACKAGE_ENTRY
    flat = _DOCS_DIR / f"{safe}.md"
    path = package_skill if package_skill.is_file() else flat
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    doc = parse_skill_markdown(raw, slug=safe)
    doc["path"] = (
        f"{safe}/{SKILL_DOC_PACKAGE_ENTRY}"
        if path == package_skill
        else str(path.name)
    )
    doc["source"] = "file"
    doc["package"] = path == package_skill
    return doc


def load_skill_doc(slug: str) -> dict[str, Any] | None:
    """同步读文件默认（无 DB）；兼容旧调用。"""
    return load_skill_doc_from_file(slug)


def skill_doc_exists(slug: str) -> bool:
    return load_skill_doc_from_file(slug) is not None


def clear_skill_doc_cache() -> None:
    load_skill_doc_from_file.cache_clear()


def format_skill_doc_agent_context(
    doc: dict[str, Any] | None,
    *,
    max_chars: int = 3200,
) -> str:
    """从 Skill MD 抽取给澄清 / Agent Team 的必遵规格（优先「Agent 必读」节）。

    单文件 Skill 用此摘要；多文件技能包请用 ``build_skill_agent_context`` 整包注入。
    """
    if not isinstance(doc, dict):
        return ""
    title = str(doc.get("title") or doc.get("name") or doc.get("slug") or "Skill").strip()
    raw = str(doc.get("raw") or doc.get("markdown") or "")
    body = raw
    # 去掉 frontmatter，避免把 YAML 噪音喂给模型
    if body.lstrip().startswith("---"):
        _, body = _parse_frontmatter(body)
    section = ""
    m = re.search(
        r"\n##\s*Agent\s*必读\s*\n(.*?)(?=\n##\s|\Z)",
        "\n" + body,
        re.DOTALL | re.I,
    )
    if m:
        section = m.group(1).strip()
    if not section:
        # 回退：summary + 使用步骤附近正文
        summary = str(doc.get("summary") or "").strip()
        section = summary
        if len(section) < 400:
            # 取正文前若干非空行
            lines = [
                ln.strip()
                for ln in body.splitlines()
                if ln.strip() and not ln.strip().startswith("```")
            ]
            section = (section + "\n" + "\n".join(lines[:40])).strip()
    section = re.sub(r"\n{3,}", "\n\n", section).strip()
    if not section:
        return ""
    if len(section) > max_chars:
        section = section[: max_chars - 1] + "…"
    return f"Skill「{title}」规范（必须遵守）：\n{section}"


def format_skill_doc_package_context(
    files: list[dict[str, str]],
    *,
    title: str,
    max_chars: int = 28000,
) -> str:
    """整包注入：把技能包内全部 md 按路径拼进 Agent 上下文。

    编排原则写在开头：模板仅供参考，由模型按用户需求自行出方案，禁止机械套固定分镜表。
    """
    title = (title or "Skill").strip() or "Skill"
    preamble = (
        f"Skill「{title}」完整技能包如下。\n"
        "【编排原则】这是可选配方，不是必须按序执行的流水线或向导。"
        "请优先阅读 SKILL.md、references/node-layout.md、references/generation.md："
        "按**本轮最新画布**选用节点布置与生成时机；词库/模板仅供参考。"
        "先读画布再布点。视频出片须用户明确确认。"
        "禁止编造 nodeId；禁止套旧 JSON plan 字段；多镜须同批 generate，禁止一条条续跑。\n"
    )
    if not files:
        return preamble.strip()

    # 注入顺序：入口 → 节点布置 → 生成规则 → 工具表 → 其余 references → README
    # 保证 AI 先看到「怎么布点 / 何时生成」，词库等次要材料靠后（28k 上限内可截断）
    ref_priority = {
        "references/node-layout.md": 0,
        "references/generation.md": 1,
        "references/canvas-tools.md": 2,
        "references/storyboard-sheet-to-video.md": 3,
        "references/constraints.md": 4,
        "references/markets.md": 4,
        "references/demand-checklist.md": 5,
        "references/storyboard-15s-30s.md": 6,
        "references/prompt-formula-and-banks.md": 7,
        "references/category-and-channels.md": 8,
        "references/creative-brief.md": 9,
    }

    def _rank(path: str) -> tuple[int, int, str]:
        if path == SKILL_DOC_PACKAGE_ENTRY:
            return (0, 0, path)
        if path in ref_priority:
            return (1, ref_priority[path], path)
        if path.startswith("references/"):
            return (2, 50, path)
        if path.upper() == "README.MD" or path == "README.md":
            return (3, 0, path)
        return (4, 0, path)

    ordered = sorted(files, key=lambda f: _rank(str(f.get("path") or "")))
    parts: list[str] = [preamble]
    used = len(preamble)
    omitted: list[str] = []
    for idx, item in enumerate(ordered):
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "")
        if not path or not content.strip():
            continue
        header = f"\n\n===== 文件: {path} =====\n"
        chunk = header + content.strip() + "\n"
        if used + len(chunk) <= max_chars:
            parts.append(chunk)
            used += len(chunk)
            continue
        remain = max_chars - used - len(header) - 8
        if remain >= 800:
            parts.append(header + content.strip()[:remain] + "…\n")
            used = max_chars
            omitted.extend(
                str(x.get("path") or "")
                for x in ordered[idx + 1 :]
                if x.get("path")
            )
        else:
            omitted.append(path)
            omitted.extend(
                str(x.get("path") or "")
                for x in ordered[idx + 1 :]
                if x.get("path")
            )
        break
    if omitted:
        names = "、".join(p for p in omitted if p)[:200]
        parts.append(f"\n\n（篇幅限制，以下文件未全文注入：{names}）\n")
    return "".join(parts).strip()


async def resolve_skill_package_files(
    db: AsyncSession | None,
    slug: str,
) -> list[dict[str, str]]:
    """合并磁盘默认 + 库覆盖，得到技能包文件列表。"""
    safe = _safe_slug(slug)
    disk = {
        f["path"]: f["content"] for f in list_skill_doc_package_files_from_disk(safe)
    }
    ov: dict[str, str] = {}
    if db is not None:
        pkg_ov = await get_skill_doc_package_overrides(db)
        ov = pkg_ov.get(safe) or {}
    paths = sorted(
        set(disk.keys()) | set(ov.keys()),
        key=lambda p: (
            0 if p == SKILL_DOC_PACKAGE_ENTRY else 1 if p == "README.md" else 2,
            p,
        ),
    )
    out: list[dict[str, str]] = []
    for path in paths:
        content = ov.get(path) if path in ov else disk.get(path)
        if content and str(content).strip():
            out.append({"path": path, "content": str(content)})
    return out


def _user_skill_package_files(skill_row: Any | None) -> list[dict[str, str]] | None:
    """把用户自建 Skill 的 package_files 列与 doc_markdown（作为包内 SKILL.md 主文件）
    合并成文件列表，供整包注入。用户技能不落盘、不在 ALLOWED_SKILL_DOC_SLUGS 白名单
    （该白名单仅用于平台包 CMS 覆盖场景），故独立于 is_skill_doc_package 判断。
    """
    if skill_row is None:
        return None
    raw = getattr(skill_row, "package_files", None)
    if not isinstance(raw, dict) or not raw:
        return None
    files: list[dict[str, str]] = []
    doc_md = str(getattr(skill_row, "doc_markdown", None) or "").strip()
    if doc_md:
        files.append({"path": SKILL_DOC_PACKAGE_ENTRY, "content": doc_md})
    for path, content in raw.items():
        text_ = str(content or "").strip()
        safe_rel = _safe_package_rel_path(str(path))
        if safe_rel and text_:
            files.append({"path": safe_rel, "content": text_})
    return files or None


async def build_skill_agent_context(
    db: AsyncSession | None,
    slug: str,
    *,
    skill_row: Any | None = None,
    max_chars: int = 28000,
) -> str:
    """供助手 runtime 使用的 Skill 上下文：多文件包整包注入，单文件仍摘要 Agent 必读。"""
    safe = _safe_slug(slug)
    if not safe:
        return ""
    doc = await load_skill_doc_resolved(db, safe, skill_row=skill_row)
    title = str((doc or {}).get("title") or safe)
    # 用户自建多文件技能包（skills.package_files 非空）：与 doc_markdown 合并整包注入，
    # 不再局限于 ≤4KB 摘要，追平平台技能包水平
    user_files = _user_skill_package_files(skill_row)
    if user_files:
        return format_skill_doc_package_context(
            user_files, title=title, max_chars=max_chars
        )
    # 有包目录或包覆盖 → 整包
    if is_skill_doc_package(safe) or (
        db is not None and (await get_skill_doc_package_overrides(db)).get(safe)
    ):
        files = await resolve_skill_package_files(db, safe)
        if files:
            return format_skill_doc_package_context(
                files, title=title, max_chars=max_chars
            )
    return format_skill_doc_agent_context(doc, max_chars=min(max_chars, 4000))


def _normalize_package_override(val: Any) -> dict[str, str] | None:
    """覆盖可为 {path: md} 或 {files:{path: md}}。"""
    if not isinstance(val, dict):
        return None
    files_raw = val.get("files") if isinstance(val.get("files"), dict) else val
    if not isinstance(files_raw, dict):
        return None
    out: dict[str, str] = {}
    for k, v in files_raw.items():
        if str(k).startswith("_"):
            continue
        rel = _safe_package_rel_path(str(k))
        if not rel or not isinstance(v, str) or not v.strip():
            continue
        out[rel] = v
    return out or None


async def get_skill_doc_overrides(db: AsyncSession) -> dict[str, str]:
    """读取单文件覆盖（字符串）；包覆盖请用 get_skill_doc_package_overrides。"""
    from .platform_settings import _ensure_settings_row

    row = await _ensure_settings_row(db)
    raw = getattr(row, "skill_docs", None)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in raw.items():
        safe = _safe_slug(str(key))
        if safe not in ALLOWED_SKILL_DOC_SLUGS:
            continue
        if isinstance(val, str) and val.strip():
            out[safe] = val
        else:
            pkg = _normalize_package_override(val)
            if pkg and pkg.get(SKILL_DOC_PACKAGE_ENTRY):
                out[safe] = pkg[SKILL_DOC_PACKAGE_ENTRY]
    return out


async def get_skill_doc_package_overrides(db: AsyncSession) -> dict[str, dict[str, str]]:
    """读取多文件包覆盖：{slug: {rel_path: content}}。"""
    from .platform_settings import _ensure_settings_row

    row = await _ensure_settings_row(db)
    raw = getattr(row, "skill_docs", None)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for key, val in raw.items():
        safe = _safe_slug(str(key))
        if safe not in ALLOWED_SKILL_DOC_SLUGS:
            continue
        pkg = _normalize_package_override(val)
        if pkg:
            out[safe] = pkg
    return out


async def load_skill_doc_resolved(
    db: AsyncSession | None,
    slug: str,
    *,
    overrides: dict[str, str] | None = None,
    skill_row: Any | None = None,
) -> dict[str, Any] | None:
    """优先：平台库覆盖 → 技能行 doc_markdown → 仓库文件。

    返回结构与 load_skill_doc 一致，并带 source: override | db | file。
    """
    safe = _safe_slug(slug)
    if not safe:
        return None

    # 包覆盖优先取 SKILL.md
    if db is not None:
        pkg_ov = await get_skill_doc_package_overrides(db)
        pkg_files = pkg_ov.get(safe)
        if pkg_files and pkg_files.get(SKILL_DOC_PACKAGE_ENTRY):
            doc = parse_skill_markdown(pkg_files[SKILL_DOC_PACKAGE_ENTRY], slug=safe)
            doc["path"] = f"{safe}/{SKILL_DOC_PACKAGE_ENTRY}"
            doc["source"] = "override"
            doc["package"] = True
            return doc

    ov = overrides
    if ov is None and db is not None:
        ov = await get_skill_doc_overrides(db)
    text = (ov or {}).get(safe) if ov else None
    if isinstance(text, str) and text.strip():
        doc = parse_skill_markdown(text, slug=safe)
        doc["path"] = None
        doc["source"] = "override"
        doc["package"] = False
        return doc

    # 我的 Skill：读 skills.doc_markdown
    row = skill_row
    if row is None and db is not None:
        try:
            from sqlalchemy import select

            from ..models.skill import Skill

            row = (
                await db.execute(select(Skill).where(Skill.slug == slug).limit(1))
            ).scalar_one_or_none()
        except Exception:  # noqa: BLE001
            row = None
    md = str(getattr(row, "doc_markdown", None) or "").strip() if row is not None else ""
    if md:
        doc = parse_skill_markdown(md, slug=safe or slug)
        doc["path"] = None
        doc["source"] = "db"
        doc["markdown"] = md
        doc["raw"] = md
        doc["package"] = False
        return doc

    return load_skill_doc_from_file(safe)


def build_user_skill_markdown(
    *,
    slug: str,
    title: str,
    description: str,
    pipeline: list[Any] | None,
    defaults: dict[str, Any] | None = None,
    media_kind: str | None = None,
    use_cases: str | None = None,
    how_to_use: str | None = None,
    output_content: str | None = None,
) -> str:
    """为我的 Skill 生成详细 SKILL.md（按类型：节点/连线/规格 + AI 操控流程）。"""
    from .skill_node_recipe import normalize_node_recipe, recipe_markdown_sections

    safe = _safe_slug(slug) or "mine"
    name = (title or "我的 Skill").strip() or "我的 Skill"
    desc = (description or "").strip() or f"用户另存的可复用配方：{name}"
    defaults = defaults if isinstance(defaults, dict) else {}
    idea = str(defaults.get("idea") or "").strip()
    kind = str(media_kind or defaults.get("mediaKind") or "").strip().lower()
    if kind not in ("image", "video", "text", "audio"):
        kind = "video"
    use_cases_text = (use_cases or str(defaults.get("useCases") or "")).strip()
    how_to_text = (how_to_use or str(defaults.get("howToUse") or "")).strip()
    output_text = (output_content or str(defaults.get("outputContent") or "")).strip()
    recipe = normalize_node_recipe(defaults.get("nodeRecipe"), media_kind=kind)
    nodes_md, order_text, specs_md = recipe_markdown_sections(recipe)

    kind_label = {
        "image": "生图",
        "text": "文本",
        "audio": "音频",
        "video": "短视频",
    }.get(kind, "短视频")
    display_defaults = dict(defaults)
    display_defaults["mediaKind"] = kind_label
    param_keys: list[tuple[str, str]] = [
        ("mediaKind", "产出类型"),
        ("idea", "默认想法"),
        ("aspectRatio", "画幅"),
        ("clarity", "清晰度"),
        ("imageModel", "生图模型"),
        ("styleId", "风格"),
        ("controllerModel", "控制器模型"),
    ]
    if kind == "video":
        param_keys.insert(3, ("durationSec", "时长（秒）"))
        param_keys.insert(5, ("videoModel", "生视频模型"))
    if kind in ("text", "audio"):
        param_keys = [
            (k, lab)
            for k, lab in param_keys
            if k not in ("clarity", "imageModel")
        ]
    param_lines: list[str] = []
    for key, label in param_keys:
        val = str(display_defaults.get(key) or "").strip()
        if val:
            param_lines.append(f"- **{label}**：`{val}`")
    params_block = "\n".join(param_lines) if param_lines else "- （暂无额外默认参数）"

    when_use_default = (
        f"当用户需要「{name}」类能力（类型：{kind_label}）时选用。"
        "用户提供文本想法，可选附件；进入画布后由 AI 按本文件规格出方案并操控画布。"
    )
    steps_default = f"""1. 在技能页或创作框选择 **{name}**，填写内容（可加附件）后发送
2. 进入画布：AI 根据本 SKILL.md 与用户输入生成**创作方案**，并询问建议
3. 用户确认/补充后：AI 按方案**新建节点、连线、写入各节点内容**
4. AI 完成后询问是否开始生成；用户回复「生成 / 出图 / 全部生成」等 → **再扣算力生成**
5. 可继续在对话里改节点或补生成"""
    constraints = f"""- 产出类型固定为 **{kind_label}**（mediaKind=`{kind}`），不得改成其它大类
- 严格按「画布节点与连线」「创作规格」建节点：页数/镜头数不足时补齐，禁止无故少建
- 每个图片/视频/音频节点必须写入可执行提示词或正文；文本节点写完整可用内容
- **未经用户确认方案前**：不要批量改画布、不要扣生成算力
- **节点编排完成后**：必须再问用户是否生成；仅当用户明确表达生成意愿时才提交生成任务
- 不要丢弃用户主题；可润色但不得换成无关题材
- 生成一律走画布节点提交（与手点同价），禁止旁路扣费"""
    output_default = {
        "image": "单张生图：画面描述文本节点 + 一张图片节点；用户确认后出图。仅明确要求多张时再增加图片节点。",
        "text": "结构化文案/剧本与角色设定文本节点，内容由 AI 按方案填入。",
        "audio": "情绪说明文本 + BGM/音效音频节点（提示词已填），用户确认后生成音频。",
        "video": "剧本/角色文本 + 多镜视频节点，用户确认后按镜生成并可再剪辑。",
    }.get(kind, "按方案建节点并在用户确认后生成。")
    ai_flow = """1. **读 Skill**：加载本文件 mediaKind、规格、节点与连线、下方「画布操控速查」
2. **出方案**：结合用户输入（及附件）给出可执行方案（格数/镜头、风格、每节点要点），用选项请用户确认或改细节
3. **用户同意后**：按节点清单创建/更新画布节点、建立连线、填入**可执行** content/prompt（此时只消耗对话编排算力，不扣媒体生成）
4. **询问生成**：编排完成后明确询问「是否开始生成」
5. **用户同意生成**：对目标节点提交生成（预扣→执行→结算），进度同步到对话
6. **续聊改画布**：严格遵守服务端注入的《画布操作说明书》全文（节点 id+名称、句柄、禁令、JSON plan）"""

    when_use = use_cases_text or when_use_default
    steps_section = how_to_text or steps_default
    output_section = output_text or output_default
    idea_line = f"默认想法：{idea}" if idea else "默认想法：以技能标题与用户当次输入为创作种子。"
    _ = pipeline  # 兼容旧调用；正文以 nodeRecipe 为准

    from .agent_canvas_manual import canvas_manual_for_skill_doc

    canvas_cheat = canvas_manual_for_skill_doc()

    # 按类型补充「每节点应写什么」细则，避免 AI 只建空壳
    per_node_guide = {
        "image": """- **画面描述**：主题、主体、风格、构图与禁忌
- **生成图（image）**：完整生图提示词；画幅/清晰度写入 generationOptions；默认 pageCount=1（单张）
- **连线**：画面描述 → 生成图；仅用户明确要求多张时再增加图片节点""",
        "video": """- **创意说明 / 剧本**：分场、对白、节奏
- **角色**：外观与身份锁提示
- **镜头 N（video）**：运镜、动作、时长；generationOptions 用字符串 id；镜头数=specs.clipCount
- 或使用分镜表工具链（storyboard_table→…→batch_videos）""",
        "text": """- **原始素材**：用户原文
- **文案/剧本**：扩写后的完整可用文本
- **角色设定**：人设与口吻
- 连线：素材→文案→角色""",
        "audio": """- **情绪/剧本**：情绪、场景、节奏需求
- **BGM**：风格/BPM/乐器提示词；generationMode=music
- **音效**：关键 SFX 描述
- 连线：情绪→BGM、情绪→音效""",
    }.get(kind, "- 按上方节点清单逐项填写可执行内容")

    # 我的 Skill 默认按技能包形态：agent_recipe + 必读 references（与平台包对齐）
    body = f"""---
name: {name}
slug: {safe}
title: {name}
description: {desc.replace(chr(10), ' ')}
execution: agent_recipe
entryKind: mine
mediaKind: {kind}
---

# {name}

> **对助手**：可选配方，不是向导。每轮先读画布。  
> **必读配套**：`references/flow.md`（流程）· `references/node-layout.md`（布点）· `references/generation.md`（何时生成）· `references/canvas-tools.md`

{desc}

## Agent 必读

用中文说你要做什么，然后调工具。禁止向用户解释 tempId / canvasOps / 节点类型内部名。

## 使用场景

{when_use}

{idea_line}

## 如何使用

{steps_section}

## 输出内容

{output_section}

## 创作规格

{specs_md}

## 画布节点与连线

{nodes_md}

## 各节点应写内容（强制细则）

{per_node_guide}

## 编排顺序

{order_text}

## AI 操控流程（强制）

{ai_flow}

## 画布操控说明书（Skill 内嵌速查）

{canvas_cheat}

## 默认参数

{params_block}

## 对助手的约束

{constraints}
"""
    return body.strip() + "\n"


# ---------- Agent Skills 渐进披露（目录常驻 / 正文与 references 按需）----------

_SKILL_BODY_MAX_CHARS = 12000
_SKILL_FILE_MAX_CHARS = 8000


def _skill_entry_line(
    doc: dict[str, Any],
    *,
    source: str = "platform",
    allow_implicit: bool = True,
) -> str:
    """单行技能目录：name + description（隐式触发依据）。"""
    name = str(doc.get("name") or doc.get("slug") or "").strip() or "?"
    slug = str(doc.get("slug") or "").strip()
    desc = str(doc.get("description") or doc.get("summary") or "").strip()
    if len(desc) > 240:
        desc = desc[:237] + "…"
    extra = f" slug={slug}" if slug and slug != name.replace("-", "_") else ""
    implicit = "" if allow_implicit else " · 仅显式点名"
    return f"- name=`{name}`{extra} · {source}{implicit} · {desc or '（无 description）'}"


async def list_skill_catalog_entries(
    db: AsyncSession | None,
    *,
    bound_skill: Any | None = None,
) -> list[dict[str, str]]:
    """平台三技能 + 本会话绑定技能（若非平台）→ 目录条目。"""
    from .skill_openai_policy import (
        load_platform_skill_openai_policy,
        load_skill_openai_policy_from_package_files,
    )

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for plat in ALLOWED_SKILL_DOC_SLUGS:
        doc = await load_skill_doc_resolved(db, plat) if db is not None else load_skill_doc_from_file(plat)
        if not doc:
            continue
        slug = str(doc.get("slug") or plat).strip() or plat
        seen.add(slug)
        policy = load_platform_skill_openai_policy(plat)
        allow_imp = bool(policy.get("allowImplicitInvocation", True))
        entries.append(
            {
                "name": str(doc.get("name") or slug),
                "slug": slug,
                "description": str(doc.get("description") or ""),
                "source": "platform",
                "allowImplicitInvocation": allow_imp,
                "line": _skill_entry_line(doc, source="platform", allow_implicit=allow_imp),
            }
        )
    if bound_skill is not None:
        bslug = _safe_slug(str(getattr(bound_skill, "slug", "") or ""))
        if bslug and bslug not in seen:
            doc = await load_skill_doc_resolved(db, bslug, skill_row=bound_skill)
            if doc:
                pkg = getattr(bound_skill, "package_files", None)
                policy = load_skill_openai_policy_from_package_files(
                    pkg if isinstance(pkg, dict) else None
                )
                allow_imp = bool(policy.get("allowImplicitInvocation", True))
                entries.append(
                    {
                        "name": str(doc.get("name") or getattr(bound_skill, "title", None) or bslug),
                        "slug": bslug,
                        "description": str(
                            doc.get("description")
                            or getattr(bound_skill, "description", None)
                            or ""
                        ),
                        "source": "bound",
                        "allowImplicitInvocation": allow_imp,
                        "line": _skill_entry_line(
                            {
                                **doc,
                                "name": doc.get("name")
                                or getattr(bound_skill, "title", None)
                                or bslug,
                                "description": doc.get("description")
                                or getattr(bound_skill, "description", None)
                                or "",
                            },
                            source="bound",
                            allow_implicit=allow_imp,
                        ),
                    }
                )
    return entries


async def format_skill_catalog_index(
    db: AsyncSession | None,
    *,
    bound_skill: Any | None = None,
) -> str:
    """注入 system 的【技能目录】（短），对齐 Codex 只常驻 name+description。"""
    from .skill_openai_policy import format_implicit_policy_hint

    entries = await list_skill_catalog_entries(db, bound_skill=bound_skill)
    lines = [
        "【技能目录】仅 name + description。匹配任务或用户点名（芯片 / $name）时先 `load_skill`；"
        "需要 `references/*.md` 时再 `load_skill_file`。禁止把目录当整包流程；本环境不执行 scripts/。",
    ]
    if not entries:
        lines.append("（当前无可用技能包）")
        return "\n".join(lines)
    for e in entries:
        lines.append(e["line"])
    hint = format_implicit_policy_hint(entries)
    if hint:
        lines.append(hint)
    return "\n".join(lines)


async def load_skill_body_text(
    db: AsyncSession | None,
    name_or_slug: str,
    *,
    skill_row: Any | None = None,
    max_chars: int = _SKILL_BODY_MAX_CHARS,
) -> str:
    """按需返回一份 SKILL.md 正文（不含 references 整包）。"""
    key = (name_or_slug or "").strip()
    if not key:
        return "失败：请传 name 或 slug。"
    slug = resolve_skill_lookup_slug(key)
    # 绑定技能可用 skill_row 覆盖解析
    row = skill_row
    if row is not None:
        bslug = _safe_slug(str(getattr(row, "slug", "") or ""))
        if bslug and (
            bslug == slug
            or bslug == _safe_slug(key.replace("-", "_"))
            or str(getattr(row, "title", "") or "").strip() == key
        ):
            slug = bslug
    doc = await load_skill_doc_resolved(db, slug, skill_row=row)
    if doc is None and row is not None:
        doc = await load_skill_doc_resolved(db, slug, skill_row=row)
    if doc is None:
        # 再试直接用 key 当 slug（用户自建）
        alt = _safe_slug(key.replace("-", "_"))
        doc = await load_skill_doc_resolved(db, alt, skill_row=row)
        if doc:
            slug = alt
    if not doc:
        known = ", ".join(ALLOWED_SKILL_DOC_SLUGS)
        return (
            f"失败：未知技能 `{key}`。平台技能 slug：{known}。"
            "也可用 Agent Skills 的 name（如 product-cinematic-commercial）。"
        )
    title = str(doc.get("title") or doc.get("name") or slug)
    name = str(doc.get("name") or slug)
    body = str(doc.get("markdown") or "").strip()
    if not body:
        raw = str(doc.get("raw") or "")
        _, body = _parse_frontmatter(raw)
        body = body.strip()
    # 多文件包：确保读到的是 SKILL.md（覆盖可能在 package files）
    if db is not None and (is_skill_doc_package(slug) or row is not None):
        files = _user_skill_package_files(row) if row is not None else None
        if not files:
            files = await resolve_skill_package_files(db, slug)
        for item in files or []:
            if str(item.get("path") or "") == SKILL_DOC_PACKAGE_ENTRY:
                content = str(item.get("content") or "")
                _, pkg_body = _parse_frontmatter(content)
                if pkg_body.strip():
                    body = pkg_body.strip()
                break
    if len(body) > max_chars:
        body = body[: max_chars - 1] + "…"
    preamble = (
        f"Skill「{title}」SKILL.md（name=`{name}` slug=`{slug}`）。"
        "可选配方，非强制流水线。只用系统画布工具执行；禁止发明工具名；"
        "正文若提 scripts/MCP：本环境不执行，请按文字步骤用画布工具或跳过。"
        "细节见 references/ 时调用 load_skill_file。\n\n"
    )
    return preamble + body


async def load_skill_package_file_text(
    db: AsyncSession | None,
    name_or_slug: str,
    rel_path: str,
    *,
    skill_row: Any | None = None,
    max_chars: int = _SKILL_FILE_MAX_CHARS,
) -> str:
    """按需返回技能包内单个 md（references/...）。"""
    key = (name_or_slug or "").strip()
    safe_rel = _safe_package_rel_path(rel_path)
    if not key:
        return "失败：请传 name 或 slug。"
    if not safe_rel:
        return "失败：path 非法（仅允许包内相对 .md，禁止 ..）。"
    slug = resolve_skill_lookup_slug(key)
    row = skill_row
    if row is not None:
        bslug = _safe_slug(str(getattr(row, "slug", "") or ""))
        if bslug and bslug in (slug, _safe_slug(key.replace("-", "_"))):
            slug = bslug
    content: str | None = None
    user_files = _user_skill_package_files(row)
    if user_files:
        for item in user_files:
            if str(item.get("path") or "") == safe_rel:
                content = str(item.get("content") or "")
                break
    if content is None and db is not None:
        files = await resolve_skill_package_files(db, slug)
        for item in files:
            if str(item.get("path") or "") == safe_rel:
                content = str(item.get("content") or "")
                break
    if content is None:
        content = read_skill_doc_package_file_from_disk(slug, safe_rel)
    if not content or not str(content).strip():
        return f"失败：技能 `{slug}` 中找不到 `{safe_rel}`。"
    text = str(content).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return f"Skill `{slug}` / `{safe_rel}`：\n\n{text}"


async def format_runtime_skill_context(
    db: AsyncSession | None,
    *,
    bound_skill: Any | None = None,
) -> str:
    """助手 system 技能段：目录常驻；若会话已绑定则预载一份 SKILL.md（非整包）。"""
    catalog = await format_skill_catalog_index(db, bound_skill=bound_skill)
    if bound_skill is None:
        return catalog
    slug = str(getattr(bound_skill, "slug", "") or "").strip()
    body = await load_skill_body_text(db, slug or "skill", skill_row=bound_skill)
    if body.startswith("失败："):
        title = getattr(bound_skill, "title", None) or slug
        return (
            f"{catalog}\n\n"
            f"当前绑定 Skill「{title}」。按用户目标选用工具，不要当阶段机。"
        )
    return (
        f"{catalog}\n\n"
        f"【本轮已绑定技能 · 已预载 SKILL.md】\n{body}"
    )

