import logging
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema 与业务 warm 解耦：DDL 并发失败时不得跳过 LLM 密钥灌入（否则 AI 控制器全空）
    try:
        from .services.db_bootstrap import ensure_mysql_schema

        await ensure_mysql_schema()
    except Exception as e:
        logger.warning("Database schema ensure skipped: %s", e)

    try:
        from .models.database import async_session
        from .services.model_catalog import sync_model_catalog
        from .services.restart_job_recovery import recover_duplicate_claim_jobs
        from .services.stale_jobs import (
            recover_exhausted_running_jobs,
            recover_interrupted_jobs_on_startup,
        )
        from .services.dev_admin import ensure_dev_admin_password
        from .services.super_admin import ensure_super_admins
        from .services.credit_lots import expire_stale_lots, migrate_user_balance_to_lots
        from .services.subscriptions import ensure_default_plans, renew_due_subscriptions
        from .services.migrate_project_covers import migrate_project_cover_oss_keys
        from .services.credential_service import seed_provider_credentials_from_env, warm_runtime_llm_keys
        from .services.model_catalog_runtime import warm_runtime_model_specs
        from .core.llm_keys import refresh_llm_keys_cache
        from .services.payment_orders import expire_stale_pending_payment_orders

        async with async_session() as session:
            await ensure_dev_admin_password(session)
            await ensure_super_admins(session)
            migrated = await migrate_user_balance_to_lots(session)
            expired = await expire_stale_lots(session)
            expired_orders = await expire_stale_pending_payment_orders(session)
            await ensure_default_plans(session)
            renewed = await renew_due_subscriptions(session)
            added = await sync_model_catalog(session)
            cred_seed = await seed_provider_credentials_from_env(session)
            # 密钥 warm 单独兜底：即使前面步骤失败也尽量再灌一次
            try:
                await warm_runtime_llm_keys(session)
                refresh_llm_keys_cache()
            except Exception as warm_exc:  # noqa: BLE001
                logger.error("LLM keys warm failed on startup: %s", warm_exc)
            catalog_loaded = await warm_runtime_model_specs(session)
            from .services.prompt_platform_runtime import warm_prompt_platform_caches

            await warm_prompt_platform_caches()
            # 启动时将启用敏感词写入 Redis 单 key，避免冷启动反复打库
            from .services.sensitive_words_cache import publish_active_sensitive_words_cache

            await publish_active_sensitive_words_cache(session)
            from .services.skills_catalog import ensure_platform_skills

            skill_added = await ensure_platform_skills(session)
            recovered = await recover_interrupted_jobs_on_startup(session)
            duplicate = await recover_duplicate_claim_jobs(session)
            exhausted = await recover_exhausted_running_jobs(session)
            await session.commit()
            if migrated:
                logger.info("Migrated legacy compute_power to credit_lots for %s user(s)", migrated)
            if expired:
                logger.info("Expired %s stale credit lot(s) on startup", expired)
            if expired_orders:
                logger.info("Expired %s stale pending payment order(s) on startup", expired_orders)
            if renewed:
                logger.info("Renewed %s subscription period(s) on startup", renewed)
            if added.get("added"):
                logger.info("Synced %s new models into catalog", added["added"])
            if added.get("updated"):
                logger.info("Backfilled pricing/metadata for %s existing models", added["updated"])
            if cred_seed.get("added"):
                logger.info("Seeded %s provider credential(s) from env", cred_seed["added"])
            if catalog_loaded:
                logger.info("Runtime model catalog loaded: %s model(s) from DB", catalog_loaded)
            if skill_added:
                logger.info("Seeded %s platform skill(s)", skill_added)
            if recovered:
                logger.info("Recovered %s interrupted generation job(s) on startup", recovered)
            if duplicate:
                logger.info("Reconciled %s DUPLICATE claim job(s) on startup", duplicate)
            if exhausted:
                logger.info("Parked %s exhausted running generation job(s) for admin", exhausted)
        await migrate_project_cover_oss_keys()
    except Exception as e:
        logger.warning("Model seed skipped: %s", e)
        # Schema/业务 seed 失败时仍尝试单独 warm 密钥（多 worker 冷启动常见）
        try:
            from .models.database import async_session
            from .services.credential_service import warm_runtime_llm_keys
            from .core.llm_keys import refresh_llm_keys_cache

            async with async_session() as session:
                await warm_runtime_llm_keys(session)
                refresh_llm_keys_cache()
                await session.commit()
            logger.info("LLM keys warm recovered after model seed skip")
        except Exception as warm_exc:  # noqa: BLE001
            logger.error("LLM keys warm recovery failed: %s", warm_exc)
    from .core.config import get_settings
    from .integrations.oss.canvas_storage import get_canvas_storage
    from .core.llm_keys import llm_keys_file, list_configured_model_ids

    storage = get_canvas_storage()
    settings = get_settings()
    if settings.canvas_storage_local_only:
        logger.info("Canvas storage: local-only writes (data/oss-local); OSS upload disabled")
    elif storage.configured:
        logger.info("Canvas OSS storage enabled (bucket=%s)", settings.oss_bucket)
    else:
        logger.warning("OSS not configured — using local mirror at data/oss-local/")

    keys_path = llm_keys_file()
    if settings.llm_keys_source.strip().lower() == "db":
        configured = list_configured_model_ids()
        logger.info(
            "LLM credentials: DB-only mode (configured models: %s)",
            ", ".join(configured) or "none — configure in admin model-providers",
        )
    elif keys_path.is_file():
        configured = list_configured_model_ids()
        logger.info("LLM keys loaded from %s (configured: %s)", keys_path, ", ".join(configured) or "none")
    else:
        logger.warning("LLM keys file not found: %s", keys_path)

    reconcile_task = None
    if settings.credit_reconcile_enabled:
        try:
            import asyncio
            from .services.credit_reconcile_loop import run_credit_reconcile_loop

            reconcile_task = asyncio.create_task(run_credit_reconcile_loop(), name="credit-reconcile")
        except Exception as exc:
            logger.warning("Credit reconcile task not started: %s", exc)

    yield

    if reconcile_task is not None:
        reconcile_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconcile_task
    try:
        from .services.redis_client import close_redis
        await close_redis()
    except Exception:
        pass
    try:
        from .models.database import engine
        await engine.dispose()
    except Exception:
        pass

from .core.errors import register_exception_handlers

app = FastAPI(title="Jumeng Canvas API", version="0.1.0", lifespan=lifespan)
register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

from .api.v1 import auth, projects, workflows, generations, models as models_api, node_texts, drawing_drafts, director_scenes, video_editor_drafts, node_groups, assets, storage, text_generation, media_generation, canvas_tools, prompt_templates, prompt_config, credits, credit_activities, subscriptions, payments, error_codes, site, workflow_publications, viral_remake, voices, skills, agent, agent_keys, openapi_agent, material_library, notifications, referral, local_models
from .api.v1.admin import router as admin_router

app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(projects.router, prefix="/api/v1/projects", tags=["projects"])
app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["workflows"])
app.include_router(generations.router, prefix="/api/v1/generations", tags=["generations"])
app.include_router(models_api.router, prefix="/api/v1/models", tags=["models"])
app.include_router(local_models.router, prefix="/api/v1", tags=["local-models"])
app.include_router(node_texts.router, prefix="/api/v1/node-texts", tags=["node-texts"])
app.include_router(drawing_drafts.router, prefix="/api/v1/drawing-drafts", tags=["drawing-drafts"])
app.include_router(director_scenes.router, prefix="/api/v1/director-scenes", tags=["director-scenes"])
app.include_router(video_editor_drafts.router, prefix="/api/v1/video-editor-drafts", tags=["video-editor-drafts"])
app.include_router(node_groups.router, prefix="/api/v1/node-groups", tags=["node-groups"])
app.include_router(assets.router, prefix="/api/v1/assets", tags=["assets"])
app.include_router(storage.router, prefix="/api/v1/storage", tags=["storage"])
app.include_router(text_generation.router, prefix="/api/v1/text", tags=["text"])
app.include_router(media_generation.router, prefix="/api/v1/media", tags=["media"])
app.include_router(voices.router, prefix="/api/v1/voices", tags=["voices"])
# 宫格切分等本地画布工具：固定算力扣费（不经上游）
app.include_router(canvas_tools.router, prefix="/api/v1/canvas-tools", tags=["canvas-tools"])
# 爆款复刻：切镜关键帧（多模态拉片分析仍走 text/generate）
app.include_router(viral_remake.router, prefix="/api/v1/viral-remake", tags=["viral-remake"])
app.include_router(skills.router, prefix="/api/v1/skills", tags=["skills"])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(agent_keys.router, prefix="/api/v1/agent", tags=["agent-keys"])
app.include_router(openapi_agent.router, prefix="/api/v1/openapi", tags=["openapi"])
app.include_router(credits.router, prefix="/api/v1/credits", tags=["credits"])
app.include_router(credit_activities.router, prefix="/api/v1/credits", tags=["credits"])
app.include_router(referral.router, prefix="/api/v1/referral", tags=["referral"])
app.include_router(subscriptions.router, prefix="/api/v1/subscriptions", tags=["subscriptions"])
app.include_router(payments.router, prefix="/api/v1/payments", tags=["payments"])
app.include_router(prompt_templates.router, prefix="/api/v1/prompt-templates", tags=["prompt-templates"])
app.include_router(prompt_config.router, prefix="/api/v1/prompt-config", tags=["prompt-config"])
app.include_router(site.router, prefix="/api/v1/site", tags=["site"])
app.include_router(workflow_publications.router, prefix="/api/v1/workflow-publications", tags=["workflow-publications"])
# 平台素材库：风格/特效/角色（用户只读）
app.include_router(material_library.router, prefix="/api/v1/material-library", tags=["material-library"])
app.include_router(notifications.router, prefix="/api/v1", tags=["notifications"])
app.include_router(error_codes.router, prefix="/api/v1", tags=["meta"])
app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])

@app.get("/health")
async def health():
    return {"status": "ok"}
