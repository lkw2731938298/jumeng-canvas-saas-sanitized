"""Backfill projects.cover_oss_key from legacy cover_url values."""

from __future__ import annotations

import logging

from sqlalchemy import select

from ..models.database import async_session
from ..models.project import Project
from .storage_urls import oss_key_from_browser_url

logger = logging.getLogger(__name__)


async def migrate_project_cover_oss_keys() -> int:
    updated = 0
    async with async_session() as session:
        result = await session.execute(
            select(Project).where(Project.cover_url.isnot(None)).where(
                (Project.cover_oss_key.is_(None)) | (Project.cover_oss_key == "")
            )
        )
        rows = result.scalars().all()
        for project in rows:
            key = oss_key_from_browser_url(project.cover_url or "")
            if not key:
                continue
            project.cover_oss_key = key
            updated += 1
        if updated:
            await session.commit()
    if updated:
        logger.info("Backfilled cover_oss_key for %s project(s)", updated)
    return updated
