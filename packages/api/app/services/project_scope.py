"""Project ID normalization and OSS key scoping helpers."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import parse_entity_id

_HEX32_FOLDER = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def _dashed_uuid_from_hex32(hexed: str) -> str:
    if not _HEX32_FOLDER.match(hexed):
        return hexed
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"


def new_storage_folder() -> str:
    """Allocate a new OSS folder segment (32-char UUID hex, no dashes)."""
    return uuid.uuid4().hex


def normalize_project_id(project_id: str | int) -> str:
    """Validate and return canonical numeric project id string."""
    parsed = parse_entity_id(project_id)
    if parsed is None:
        raise ValueError("Invalid project id")
    return str(parsed)


def project_storage_folder(project) -> str:
    """Return project's OSS folder; required for all storage operations."""
    folder = str(getattr(project, "storage_folder", "") or "").strip()
    if not folder:
        raise ValueError("Project storage_folder is not configured")
    return folder


def project_id_folder(project_id: str | int, storage_folder: str | None = None) -> str:
    """Folder segment used under canvas/projects/{folder}/..."""
    folder = (storage_folder or "").strip()
    if not folder:
        raise ValueError("storage_folder is required for OSS paths")
    return folder


def assert_safe_storage_key(oss_key: str) -> str:
    key = str(oss_key or "").strip().lstrip("/")
    if not key or ".." in key or "\\" in key:
        raise ValueError("Invalid storage key")
    return key


def oss_projects_folder_from_key(oss_key: str) -> str | None:
    """Return the folder segment under .../projects/{folder}/..."""
    try:
        key = assert_safe_storage_key(oss_key)
    except ValueError:
        return None
    parts = key.split("/")
    try:
        idx = parts.index("projects")
        folder = parts[idx + 1]
    except (ValueError, IndexError):
        return None
    return folder or None


def _storage_folder_lookup_candidates(folder: str) -> list[str]:
    """Normalize OSS folder segments for projects.storage_folder lookup."""
    raw = str(folder or "").strip()
    if not raw:
        return []
    candidates = [raw]
    hexed = raw.replace("-", "").lower()
    if hexed != raw:
        candidates.append(hexed)
    if _HEX32_FOLDER.match(hexed):
        dashed = _dashed_uuid_from_hex32(hexed)
        if dashed not in candidates:
            candidates.append(dashed)
    return list(dict.fromkeys(candidates))


def project_id_from_oss_key(oss_key: str) -> str | None:
    """Extract numeric project id when the OSS folder segment is digits.

    Paths under projects.storage_folder (32-char hex) require
    resolve_project_id_from_oss_key() with a DB session.
    """
    folder = oss_projects_folder_from_key(oss_key)
    if not folder:
        return None
    if folder.isdigit():
        return folder
    parsed = parse_entity_id(folder)
    return str(parsed) if parsed is not None else None


async def resolve_project_id_from_oss_key(
    db: AsyncSession,
    oss_key: str,
) -> str | None:
    """Resolve numeric project id from an OSS key (supports storage_folder paths)."""
    folder = oss_projects_folder_from_key(oss_key)
    if not folder:
        return None
    if folder.isdigit():
        return folder

    from ..models.project import Project

    for candidate in _storage_folder_lookup_candidates(folder):
        hexed = candidate.replace("-", "").lower()
        if _HEX32_FOLDER.match(hexed):
            result = await db.execute(
                select(Project.id).where(Project.storage_folder == hexed)
            )
            pid = result.scalar_one_or_none()
            if pid is not None:
                return str(pid)

    parsed = parse_entity_id(folder)
    if parsed is not None:
        return str(parsed)
    return None


def _normalize_folder_segment(folder: str) -> str:
    return str(folder or "").replace("-", "").lower()


def _folder_matches_project(
    folder: str,
    project_id: str | int,
    storage_folder: str | None,
) -> bool:
    pid = str(parse_entity_id(project_id) or project_id)
    if folder.isdigit():
        return folder == pid

    key_variants = {
        _normalize_folder_segment(candidate)
        for candidate in _storage_folder_lookup_candidates(folder)
    }
    if storage_folder:
        project_variants = {
            _normalize_folder_segment(candidate)
            for candidate in _storage_folder_lookup_candidates(storage_folder)
        }
        return bool(key_variants & project_variants)
    return False


def oss_key_belongs_to_project(
    oss_key: str,
    project_id: str,
    *,
    storage_folder: str | None = None,
) -> bool:
    try:
        key = assert_safe_storage_key(oss_key)
    except ValueError:
        return False

    folder = oss_projects_folder_from_key(key)
    if not folder:
        return False
    return _folder_matches_project(folder, project_id, storage_folder)
