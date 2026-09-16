"""Recover workflow DB rows + OSS pointers from legacy nodes.json files."""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select

from app.core.datetime_util import now_cst_naive
from app.models.database import async_session
from app.models.project import Project, Workflow
from app.services.workflow_recovery import (
    make_flow_pointer,
    pick_best_workflow_candidate,
    project_folder_from_workflow_key,
    scan_workflow_candidates,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def _fix_orphan_workflows(db) -> int:
    fixed = 0
    result = await db.execute(select(Workflow).where(Workflow.project_id.is_(None)))
    orphans = result.scalars().all()
    for wf in orphans:
        try:
            import json

            ptr = json.loads(wf.flow_json or "{}")
            oss_key = ptr.get("ossKey") if isinstance(ptr, dict) else None
        except json.JSONDecodeError:
            oss_key = None
        folder = project_folder_from_workflow_key(str(oss_key or ""))
        if not folder:
            continue
        project_result = await db.execute(
            select(Project).where(Project.storage_folder == folder)
        )
        project = project_result.scalar_one_or_none()
        if not project:
            continue
        wf.project_id = project.id
        fixed += 1
        logger.info("Fixed orphan workflow %s -> project %s", wf.id, project.id)
    return fixed


async def run(*, project_id: str | None = None) -> int:
    updated = 0
    created = 0
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
        logger.info("Scanning %d project(s) for workflow recovery", len(projects))

        orphans_fixed = await _fix_orphan_workflows(db)
        if orphans_fixed:
            logger.info("Fixed %d orphan workflow(s)", orphans_fixed)

        for project in projects:
            folder = str(project.storage_folder or "").strip()
            if not folder:
                continue
            candidates = await asyncio.to_thread(
                scan_workflow_candidates, str(project.id), folder
            )
            best = pick_best_workflow_candidate(candidates)
            if not best or best.node_count <= 0:
                continue

            wf_result = await db.execute(
                select(Workflow)
                .where(Workflow.project_id == project.id)
                .order_by(Workflow.updated_at.desc())
            )
            workflows = wf_result.scalars().all()
            pointer = make_flow_pointer(best.oss_key)
            node_count = str(best.node_count)

            if workflows:
                wf = workflows[0]
                try:
                    import json

                    current_ptr = json.loads(wf.flow_json or "{}")
                    current_key = current_ptr.get("ossKey") if isinstance(current_ptr, dict) else None
                except json.JSONDecodeError:
                    current_key = None
                current_count = int(wf.node_count or "0")
                if current_key == best.oss_key and current_count >= best.node_count:
                    continue
                wf.flow_json = pointer
                wf.node_count = node_count
                wf.updated_at = now_cst_naive()
                updated += 1
                logger.info(
                    "Updated workflow %s for project %s (%s): %d nodes",
                    wf.id,
                    project.id,
                    project.title,
                    best.node_count,
                )
                continue

            wf = Workflow(
                project_id=project.id,
                title="恢复的工作流",
                flow_json=pointer,
                node_count=node_count,
                revision=1,
                status="draft",
            )
            db.add(wf)
            created += 1
            logger.info(
                "Created workflow for project %s (%s): %d nodes",
                project.id,
                project.title,
                best.node_count,
            )

        await db.commit()

    logger.info("Done. updated=%d created=%d", updated, created)
    return 0


if __name__ == "__main__":
    arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    raise SystemExit(asyncio.run(run(project_id=arg)))
