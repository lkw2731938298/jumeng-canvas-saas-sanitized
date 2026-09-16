"""Project cover URL helpers — DB stores oss key, API returns CDN / current-mode public URLs."""

from __future__ import annotations

from ..models.project import Project
from .storage_urls import oss_key_from_browser_url, persistable_media_url, public_url_for_key


def resolve_project_cover_url(project: Project) -> str | None:
    key = (project.cover_oss_key or "").strip()
    if not key:
        key = oss_key_from_browser_url(project.cover_url or "") or ""
    if key:
        return public_url_for_key(key)
    return project.cover_url or None


def assign_project_cover(project: Project, *, oss_key: str, legacy_url: str = "") -> None:
    project.cover_oss_key = oss_key
    # 中文：遗留 cover_url 字段写稳定公共 URL（或 key），禁止签权串
    project.cover_url = persistable_media_url(legacy_url) or oss_key
