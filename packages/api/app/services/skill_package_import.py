"""Agent Skills / Codex 风格技能包 zip 导入（只吃 markdown，不执行 scripts）。

校验 SKILL.md frontmatter；references/*.md 入库；scripts/** 记入 unsupportedFiles。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from .skill_docs import parse_skill_markdown

# Agent Skills name：小写字母数字连字符，1–64，不首尾连字符、无连续 --
_SKILL_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}$")
_MAX_ZIP_BYTES = 2 * 1024 * 1024
_MAX_MD_FILES = 12
_MAX_TOTAL_MD_CHARS = 48000
_MAX_SKILL_MD_CHARS = 50000


@dataclass
class SkillPackageImportResult:
    """解压并校验后的可入库技能包。"""

    name: str
    description: str
    title: str
    slug_hint: str
    skill_md: str
    package_files: dict[str, str] = field(default_factory=dict)
    unsupported_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entry_kind: str = ""
    execution: str = ""


def _normalize_zip_path(name: str) -> str:
    path = str(name or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _find_skill_md_root(names: list[str]) -> tuple[str, str]:
    """返回 (SKILL.md 在 zip 内的路径, 包根前缀)。包根可为 '' 或 'skill-name/'。"""
    candidates: list[str] = []
    for n in names:
        p = _normalize_zip_path(n)
        if not p or p.endswith("/"):
            continue
        base = p.rsplit("/", 1)[-1]
        if base.lower() == "skill.md":
            candidates.append(p)
    if not candidates:
        raise ValueError("zip 内未找到 SKILL.md（根目录或第一层子目录）")
    # 优先根目录；否则取路径段数最少的
    candidates.sort(key=lambda x: (x.count("/"), len(x)))
    skill_path = candidates[0]
    if "/" not in skill_path:
        return skill_path, ""
    prefix = skill_path.rsplit("/", 1)[0] + "/"
    # 仅允许一层子目录作为包根（Codex 常见 skill-name/SKILL.md）
    if prefix.count("/") > 1:
        # 仍接受更深路径，但相对路径从该 SKILL.md 所在目录起算
        pass
    return skill_path, prefix


def validate_agent_skills_name(name: str) -> str:
    n = str(name or "").strip().lower()
    if not n or not _SKILL_NAME_RE.match(n):
        raise ValueError(
            "SKILL.md 的 name 须为 1–64 位小写字母/数字/连字符，"
            "且不首尾连字符、无连续 --（Agent Skills 标准）"
        )
    return n


def parse_skill_package_zip(data: bytes) -> SkillPackageImportResult:
    """解析 zip 字节 → 可入库结构；非法则 ValueError。"""
    raw = data or b""
    if not raw:
        raise ValueError("空文件")
    if len(raw) > _MAX_ZIP_BYTES:
        raise ValueError(f"zip 过大（上限 {_MAX_ZIP_BYTES // 1024}KB）")
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 zip 文件") from exc

    names = [info.filename for info in zf.infolist() if not info.is_dir()]
    skill_path, prefix = _find_skill_md_root(names)

    def rel_from_root(zip_path: str) -> str:
        p = _normalize_zip_path(zip_path)
        if prefix and p.startswith(prefix):
            return p[len(prefix) :]
        return p

    try:
        skill_bytes = zf.read(skill_path)
    except KeyError as exc:
        raise ValueError("无法读取 SKILL.md") from exc
    try:
        skill_text = skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md 须为 UTF-8 文本") from exc
    if len(skill_text) > _MAX_SKILL_MD_CHARS:
        skill_text = skill_text[:_MAX_SKILL_MD_CHARS]

    parsed = parse_skill_markdown(skill_text, slug="")
    name = validate_agent_skills_name(str(parsed.get("name") or ""))
    description = str(parsed.get("description") or "").strip()
    if not description:
        raise ValueError("SKILL.md frontmatter 缺少 description")
    if len(description) > 1024:
        description = description[:1024]
        warnings_early = ["description 已截断至 1024 字符"]
    else:
        warnings_early = []

    title = str(parsed.get("title") or name).strip()[:128] or name
    slug_hint = str(parsed.get("slug") or name.replace("-", "_")).strip()
    entry_kind = str(parsed.get("entryKind") or "").strip()
    execution = str(parsed.get("execution") or "").strip()

    package_files: dict[str, str] = {}
    unsupported: list[str] = []
    warnings = list(warnings_early)
    total_chars = 0

    for info in zf.infolist():
        if info.is_dir():
            continue
        zip_path = _normalize_zip_path(info.filename)
        if not zip_path or zip_path == _normalize_zip_path(skill_path):
            continue
        rel = rel_from_root(zip_path)
        if not rel or ".." in rel.split("/"):
            continue
        lower = rel.lower()
        if lower.startswith("scripts/") or "/scripts/" in lower:
            unsupported.append(rel)
            continue
        if lower == "agents/openai.yaml" or lower.endswith("/agents/openai.yaml"):
            # 入库策略文件（非 MCP）；供 allow_implicit_invocation 读取
            try:
                body = zf.read(info.filename).decode("utf-8")
            except (UnicodeDecodeError, KeyError):
                unsupported.append(rel)
                continue
            package_files["agents/openai.yaml"] = body.strip()
            continue
        if not lower.endswith(".md"):
            unsupported.append(rel)
            continue
        # 只收 references/ 与根下其它 md（assets 说明等）
        if not (
            lower.startswith("references/")
            or lower.startswith("assets/")
            or "/" not in lower
        ):
            unsupported.append(rel)
            continue
        try:
            body = zf.read(info.filename).decode("utf-8")
        except (UnicodeDecodeError, KeyError):
            unsupported.append(rel)
            warnings.append(f"跳过无法解码的文件：{rel}")
            continue
        body = body.strip()
        if not body:
            continue
        if len(package_files) >= _MAX_MD_FILES:
            warnings.append("参考 md 文件数超上限，其余已忽略")
            break
        room = _MAX_TOTAL_MD_CHARS - total_chars
        if room <= 0:
            warnings.append("参考文档总字符超上限，其余已截断")
            break
        if len(body) > room:
            body = body[:room]
        package_files[rel] = body
        total_chars += len(body)

    if re.search(r"(?i)compatibility\s*:[^\n]*(git|docker|shell|mcp)", skill_text):
        warnings.append("compatibility 声明含 git/docker/shell/MCP：指令可用、脚本不可用")
        description = (description.rstrip("。") + "（指令可用、脚本不可用）")[:1024]

    if unsupported:
        warnings.append(
            f"有 {len(unsupported)} 个非 markdown/脚本文件未入库（unsupportedFiles）"
        )

    # 确保入库的 SKILL.md 保留完整 frontmatter + 正文
    skill_md = skill_text if skill_text.endswith("\n") else skill_text + "\n"

    return SkillPackageImportResult(
        name=name,
        description=description,
        title=title,
        slug_hint=slug_hint,
        skill_md=skill_md,
        package_files=package_files,
        unsupported_files=sorted(set(unsupported)),
        warnings=warnings,
        entry_kind=entry_kind,
        execution=execution or "agent_recipe",
    )


def import_result_to_preview_dict(result: SkillPackageImportResult) -> dict[str, Any]:
    """API 预览/响应用驼峰字段。"""
    return {
        "name": result.name,
        "description": result.description,
        "title": result.title,
        "slugHint": result.slug_hint,
        "packageFiles": dict(result.package_files),
        "unsupportedFiles": list(result.unsupported_files),
        "warnings": list(result.warnings),
        "entryKind": result.entry_kind or None,
        "execution": result.execution or None,
        "docMarkdownPreview": (result.skill_md[:500] + "…")
        if len(result.skill_md) > 500
        else result.skill_md,
    }
