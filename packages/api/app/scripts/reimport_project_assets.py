"""Re-import project_assets from OSS assets/index.json for all projects (post UUID→BIGINT recovery)."""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select

from app.models.database import async_session
from app.models.project import Project
from app.services.asset_store import sync_project_assets_from_oss
from app.services.cache import invalidate_manifest_cache

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run(*, project_id: str | None = None) -> int:
    total_inserted = 0
    async with async_session() as db:
        query = select(Project).order_by(Project.id)
        if project_id:
            from app.core.entity_ids import parse_entity_id

            pid = parse_entity_id(project_id)
            if pid is None:
                logger.error("Invalid project id: %s", project_id)
                return 1
            query = query.where(Project.id == pid)

        result = await db.execute(query)
        projects = result.scalars().all()
        logger.info("Scanning %d project(s)", len(projects))

        for project in projects:
            inserted = await sync_project_assets_from_oss(
                db, str(project.id), project=project
            )
            if inserted:
                logger.info(
                    "project %s (%s): imported %d asset(s)",
                    project.id,
                    project.title,
                    inserted,
                )
                total_inserted += inserted
                await invalidate_manifest_cache(str(project.id))

        await db.commit()

    logger.info("Done. Total imported: %d", total_inserted)
    return 0


if __name__ == "__main__":
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(run(project_id=arg)))
