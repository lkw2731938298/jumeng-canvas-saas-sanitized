"""MySQL schema bootstrap on API startup."""

from __future__ import annotations

import logging

from sqlalchemy import text

from ..models.database import engine, Base

logger = logging.getLogger(__name__)


async def ensure_mysql_schema() -> None:
    """Create ORM tables and auxiliary indexes for MySQL."""
    from ..models import (  # noqa: F401
        auth_event,
        asset,
        credit,
        credit_activity,
        credit_lot,
        credit_transaction,
        job,
        job_admin_action,
        generation_call_log,
        model_provider,
        node_group,
        platform_settings,
        project,
        sensitive_word,  # noqa: F401 — 敏感词表
        sms_code,
        subscription,
        user,
        user_voice_clone,  # noqa: F401 — 百炼复刻音色
        workflow_publication,  # noqa: F401 — 工作流发布/点赞/使用
        skill,  # noqa: F401 — Skill 目录
        agent_session,  # noqa: F401 — Agent 会话
        project_graph,  # noqa: F401 — Project Graph
        user_agent_key,  # noqa: F401 — Agent Access Key
        material_library,  # noqa: F401 — 平台素材库（风格/特效/角色/提示词）
        user_notification,  # noqa: F401 — 站内通知
        invite_campaign,  # noqa: F401 — 用户邀请活动/绑定/发奖
        register_bonus,  # noqa: F401 — 首次注册赠送
    )
    from ..models import project_member  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        extra_indexes = [
            "CREATE INDEX ix_credit_lots_user_status ON credit_lots (user_id, status, expires_at)",
            "CREATE INDEX ix_user_subscriptions_user_status ON user_subscriptions (user_id, status, current_period_end)",
            "CREATE UNIQUE INDEX uq_subscription_grants_user_period ON subscription_grants (user_id, period_key)",
            "CREATE UNIQUE INDEX uq_credit_reservations_job_user_idem ON credit_reservations (job_id, user_id, idempotency_key)",
            "CREATE UNIQUE INDEX uq_credit_transactions_job_user_ref ON credit_transactions (job_id, user_id, reference_key)",
            "CREATE UNIQUE INDEX uq_credit_recharge_orders_user_idem ON credit_recharge_orders (user_id, idempotency_key)",
            "CREATE UNIQUE INDEX uq_credit_admin_adjust_orders_user_idem ON credit_admin_adjust_orders (user_id, idempotency_key)",
            "CREATE UNIQUE INDEX ix_users_phone_unique ON users (phone)",
            "CREATE INDEX ix_generation_jobs_lane_pending ON generation_jobs (lane, created_at)",
            "CREATE INDEX ix_generation_jobs_created_at ON generation_jobs (created_at)",
            "CREATE INDEX ix_projects_storage_folder ON projects (storage_folder)",
            "CREATE INDEX ix_generation_call_logs_job_phase ON generation_call_logs (job_id, phase)",
            "CREATE INDEX ix_generation_call_logs_created_at ON generation_call_logs (created_at)",
            "CREATE UNIQUE INDEX uq_skill_favorites_user_skill ON skill_favorites (user_id, skill_id)",
            "CREATE UNIQUE INDEX uq_user_notifications_user_dedupe ON user_notifications (user_id, dedupe_key)",
            "CREATE INDEX ix_user_notifications_user_unread ON user_notifications (user_id, is_read, created_at)",
        ]
        for ddl in extra_indexes:
            try:
                await conn.execute(text(ddl))
            except Exception as exc:
                if "Duplicate key name" in str(exc) or "already exists" in str(exc).lower():
                    continue
                logger.debug("Index DDL skipped (%s): %s", ddl[:60], exc)

        extra_columns = [
            "ALTER TABLE generation_jobs ADD COLUMN upstream_credit_cost INT NULL AFTER credit_cost",
            "ALTER TABLE credit_transactions ADD COLUMN job_id BIGINT UNSIGNED NULL AFTER model_name",
            "ALTER TABLE credit_transactions ADD COLUMN reference_key VARCHAR(128) NULL AFTER job_id",
            "ALTER TABLE credit_activity_claims ADD COLUMN claim_seq INT NOT NULL DEFAULT 1 AFTER user_id",
            "ALTER TABLE credit_activity_claims MODIFY COLUMN lot_id BIGINT UNSIGNED NULL",
            "ALTER TABLE generation_jobs ADD COLUMN anomaly_reason VARCHAR(64) NULL AFTER error_message",
            "ALTER TABLE generation_jobs ADD COLUMN anomaly_detected_at DATETIME NULL AFTER anomaly_reason",
            "ALTER TABLE generation_jobs ADD COLUMN anomaly_detail TEXT NULL AFTER anomaly_detected_at",
            "ALTER TABLE generation_jobs ADD COLUMN last_admin_action VARCHAR(32) NULL AFTER anomaly_detail",
            "ALTER TABLE generation_jobs ADD COLUMN last_admin_action_at DATETIME NULL AFTER last_admin_action",
            "ALTER TABLE generation_jobs ADD COLUMN last_admin_operator_id BIGINT UNSIGNED NULL AFTER last_admin_action_at",
            "ALTER TABLE generation_jobs ADD COLUMN last_admin_note TEXT NULL AFTER last_admin_operator_id",
            "ALTER TABLE generation_jobs ADD COLUMN worker_claim_id VARCHAR(64) NULL AFTER last_admin_note",
            "ALTER TABLE generation_jobs ADD COLUMN actor_user_id BIGINT UNSIGNED NULL AFTER user_id",
            "ALTER TABLE projects ADD COLUMN cover_oss_key TEXT NULL AFTER cover_url",
            "ALTER TABLE projects ADD COLUMN isdel TINYINT(1) NOT NULL DEFAULT 0 AFTER is_public",
            "ALTER TABLE projects ADD COLUMN collaborator_daily_cap INT NULL AFTER is_public",
            "ALTER TABLE projects ADD COLUMN collaborator_total_cap INT NULL AFTER collaborator_daily_cap",
            "ALTER TABLE projects ADD COLUMN collaborator_approval_threshold INT NULL AFTER collaborator_total_cap",
            "ALTER TABLE projects ADD COLUMN storage_folder VARCHAR(64) NULL AFTER owner_id",
            "ALTER TABLE generation_jobs DROP COLUMN submit_no",
            "ALTER TABLE platform_settings ADD COLUMN homepage_background_mode VARCHAR(32) NOT NULL DEFAULT 'default' AFTER registration_enabled",
            "ALTER TABLE platform_settings ADD COLUMN homepage_background_oss_key TEXT NULL AFTER homepage_background_mode",
            "ALTER TABLE platform_settings ADD COLUMN auth_grid_images JSON NULL AFTER homepage_background_oss_key",
            "ALTER TABLE platform_settings ADD COLUMN default_storage_gb INT NOT NULL DEFAULT 2 AFTER auth_grid_images",
            "ALTER TABLE platform_settings ADD COLUMN recharge_tiers JSON NULL AFTER default_storage_gb",
            "ALTER TABLE platform_settings ADD COLUMN canvas_tool_pricing JSON NULL AFTER recharge_tiers",
            "ALTER TABLE platform_settings ADD COLUMN canvas_tool_models JSON NULL AFTER canvas_tool_pricing",
            "ALTER TABLE platform_settings ADD COLUMN discover_page JSON NULL AFTER canvas_tool_models",
            "ALTER TABLE platform_settings ADD COLUMN site_footer JSON NULL AFTER discover_page",
            "ALTER TABLE platform_settings ADD COLUMN model_ui_tags JSON NULL AFTER site_footer",
            "ALTER TABLE platform_settings ADD COLUMN model_ui_series JSON NULL AFTER model_ui_tags",
            "ALTER TABLE workflow_publications ADD COLUMN flow_oss_key TEXT NULL AFTER video_oss_key",
            "ALTER TABLE workflow_publications ADD COLUMN cover_asset_id VARCHAR(64) NULL AFTER video_oss_key",
            "ALTER TABLE workflow_publications ADD COLUMN cover_oss_key TEXT NULL AFTER cover_asset_id",
            "ALTER TABLE credit_activities ADD COLUMN cover_url TEXT NULL AFTER description",
            # 领取资格：注册时间 / 累计充值金额或算力点 / 是否有效会员
            "ALTER TABLE credit_activities ADD COLUMN claim_rules JSON NULL AFTER claimed_count",
            "ALTER TABLE subscription_plans ADD COLUMN storage_gb INT NOT NULL DEFAULT 0 AFTER monthly_credits",
            "ALTER TABLE users ADD COLUMN storage_used_bytes BIGINT UNSIGNED NOT NULL DEFAULT 0 AFTER compute_power",
            "ALTER TABLE users ADD COLUMN is_super_admin TINYINT(1) NOT NULL DEFAULT 0 AFTER role",
            "ALTER TABLE users ADD COLUMN admin_permissions JSON NULL AFTER is_super_admin",
            "ALTER TABLE credit_recharge_orders ADD COLUMN pay_amount_fen INT NOT NULL DEFAULT 0 AFTER amount",
            "ALTER TABLE credit_recharge_orders ADD COLUMN order_type VARCHAR(32) NOT NULL DEFAULT 'recharge' AFTER pay_amount_fen",
            "ALTER TABLE credit_recharge_orders ADD COLUMN payment_channel VARCHAR(32) NOT NULL DEFAULT 'direct' AFTER order_type",
            "ALTER TABLE credit_recharge_orders ADD COLUMN plan_id BIGINT UNSIGNED NULL AFTER payment_channel",
            "ALTER TABLE credit_recharge_orders ADD COLUMN alipay_trade_no VARCHAR(64) NULL AFTER plan_id",
            "ALTER TABLE credit_recharge_orders ADD COLUMN expires_at DATETIME NULL AFTER status",
            "ALTER TABLE canvas_node_groups ADD COLUMN tags JSON NULL AFTER asset_ids",
            "ALTER TABLE agent_sessions ADD COLUMN style_id VARCHAR(64) NULL AFTER skill_id",
            "ALTER TABLE agent_sessions ADD COLUMN credit_cost INT NOT NULL DEFAULT 0 AFTER style_id",
            "ALTER TABLE agent_sessions ADD COLUMN credit_reservation_id VARCHAR(128) NULL AFTER credit_cost",
            "ALTER TABLE agent_sessions ADD COLUMN credit_status VARCHAR(32) NULL AFTER credit_reservation_id",
            "ALTER TABLE agent_sessions ADD COLUMN pricing_version INT NULL AFTER credit_status",
            "ALTER TABLE agent_sessions ADD COLUMN credit_breakdown JSON NULL AFTER pricing_version",
            "ALTER TABLE agent_sessions ADD COLUMN brief_json JSON NULL AFTER credit_breakdown",
            "ALTER TABLE platform_settings ADD COLUMN agent_skill_pricing JSON NULL AFTER canvas_tool_pricing",
            # Skill MD 管理覆盖（爆款 / 出海）
            "ALTER TABLE platform_settings ADD COLUMN skill_docs JSON NULL AFTER model_ui_series",
            # 我的 Skill：另存时自动生成的 SKILL.md 正文
            "ALTER TABLE skills ADD COLUMN doc_markdown TEXT NULL AFTER pipeline",
            # Skill 社区发布审核
            "ALTER TABLE skills ADD COLUMN review_status VARCHAR(16) NOT NULL DEFAULT 'none' AFTER status",
            "ALTER TABLE skills ADD COLUMN review_note TEXT NULL AFTER review_status",
            "ALTER TABLE skills ADD COLUMN reviewed_at DATETIME NULL AFTER review_note",
            "ALTER TABLE skills ADD COLUMN reviewed_by BIGINT UNSIGNED NULL AFTER reviewed_at",
            # 工作流发布审核（历史公开条目默认 approved）
            "ALTER TABLE workflow_publications ADD COLUMN review_status VARCHAR(16) NOT NULL DEFAULT 'approved' AFTER is_public",
            "ALTER TABLE workflow_publications ADD COLUMN review_note TEXT NULL AFTER review_status",
            "ALTER TABLE workflow_publications ADD COLUMN reviewed_at DATETIME NULL AFTER review_note",
            "ALTER TABLE workflow_publications ADD COLUMN reviewed_by BIGINT UNSIGNED NULL AFTER reviewed_at",
            # 审核时间线 + Skill 分类配置
            "ALTER TABLE skills ADD COLUMN review_history JSON NULL AFTER reviewed_by",
            "ALTER TABLE workflow_publications ADD COLUMN review_history JSON NULL AFTER reviewed_by",
            "ALTER TABLE platform_settings ADD COLUMN skill_categories JSON NULL AFTER skill_docs",
            # 登录弹窗左侧图
            "ALTER TABLE platform_settings ADD COLUMN login_modal_oss_key TEXT NULL AFTER auth_grid_images",
            # 首次注册赠送
            "ALTER TABLE platform_settings ADD COLUMN register_bonus_enabled TINYINT(1) NOT NULL DEFAULT 0 AFTER login_modal_oss_key",
            "ALTER TABLE platform_settings ADD COLUMN register_bonus_amount INT NOT NULL DEFAULT 0 AFTER register_bonus_enabled",
            # 登录弹窗用户协议 / 隐私政策
            "ALTER TABLE platform_settings ADD COLUMN legal_documents JSON NULL AFTER register_bonus_amount",
            # 算力一位小数：lot / 余额缓存 / 预扣 / 流水 / 任务扣费
            "ALTER TABLE credit_lots MODIFY COLUMN amount_initial DECIMAL(14,1) NOT NULL",
            "ALTER TABLE credit_lots MODIFY COLUMN amount_remaining DECIMAL(14,1) NOT NULL",
            "ALTER TABLE users MODIFY COLUMN compute_power DECIMAL(14,1) NOT NULL DEFAULT 0",
            "ALTER TABLE credit_reservations MODIFY COLUMN amount DECIMAL(14,1) NOT NULL",
            "ALTER TABLE credit_transactions MODIFY COLUMN delta DECIMAL(14,1) NOT NULL",
            "ALTER TABLE credit_transactions MODIFY COLUMN balance_after DECIMAL(14,1) NOT NULL",
            "ALTER TABLE generation_jobs MODIFY COLUMN credit_cost DECIMAL(14,1) NOT NULL DEFAULT 0",
            "ALTER TABLE generation_jobs MODIFY COLUMN upstream_credit_cost DECIMAL(14,1) NULL",
            "ALTER TABLE agent_sessions MODIFY COLUMN credit_cost DECIMAL(14,1) NOT NULL DEFAULT 0",
            "ALTER TABLE credit_admin_adjust_orders MODIFY COLUMN delta DECIMAL(14,1) NOT NULL",
            # Skill 技能包能力对齐：多文件包 / 执行路径 / 画布规则覆盖
            "ALTER TABLE skills ADD COLUMN package_files JSON NULL AFTER doc_markdown",
            "ALTER TABLE skills ADD COLUMN execution_mode VARCHAR(16) NOT NULL DEFAULT 'team' AFTER package_files",
            "ALTER TABLE skills ADD COLUMN canvas_rules_markdown TEXT NULL AFTER execution_mode",
            # 用户邀请码与归因
            "ALTER TABLE users ADD COLUMN invite_code CHAR(6) NULL AFTER is_active",
            "ALTER TABLE users ADD COLUMN referred_by_user_id BIGINT UNSIGNED NULL AFTER invite_code",
            # 提示词库：媒体预览 + 提示词正文
            "ALTER TABLE material_library_items ADD COLUMN prompt_text TEXT NULL AFTER oss_key",
            # 提示词库二级分类
            "ALTER TABLE material_library_items ADD COLUMN prompt_category_id BIGINT UNSIGNED NULL AFTER prompt_text",
        ]
        for ddl in extra_columns:
            try:
                await conn.execute(text(ddl))
            except Exception as exc:
                # MODIFY 幂等：列已是目标类型时部分版本仍成功；重复 ADD 用 Duplicate 跳过
                msg = str(exc)
                if "Duplicate column name" in msg:
                    continue
                logger.debug("Column DDL skipped (%s): %s", ddl[:60], exc)

        post_column_indexes = [
            "CREATE INDEX ix_generation_jobs_anomaly_reason ON generation_jobs (anomaly_reason, anomaly_detected_at)",
            "CREATE INDEX ix_generation_job_admin_actions_job ON generation_job_admin_actions (job_id, created_at)",
            "CREATE INDEX ix_skills_review_visibility ON skills (review_status, visibility)",
            "CREATE INDEX ix_workflow_publications_public_review ON workflow_publications (is_public, review_status, published_at)",
            "CREATE UNIQUE INDEX uq_users_invite_code ON users (invite_code)",
            "CREATE INDEX ix_users_referred_by ON users (referred_by_user_id)",
            "CREATE INDEX ix_material_library_items_prompt_category ON material_library_items (prompt_category_id)",
            "CREATE UNIQUE INDEX uq_register_bonus_grants_key_user ON register_bonus_grants (bonus_key, user_id)",
        ]
        for ddl in post_column_indexes:
            try:
                await conn.execute(text(ddl))
            except Exception as exc:
                if "Duplicate key name" in str(exc) or "already exists" in str(exc).lower():
                    continue
                logger.debug("Index DDL skipped (%s): %s", ddl[:60], exc)

        # 活动领取：每人每活动仅一条 claim（UNIQUE(activity_id, user_id)）
        # 1) 去掉旧的 (activity_id, user_id, claim_seq) 唯一索引
        # 2) 同 activity+user 多行时保留最早一条，删多余行
        # 3) 建 uq_credit_activity_claims_activity_user
        # 4) 活动 per_user_limit 统一固定为 1
        for obsolete_idx in (
            "ix_credit_activity_claims_activity_user_seq",
            "ix_credit_activity_claims_activity_user",
        ):
            try:
                await conn.execute(
                    text(f"ALTER TABLE credit_activity_claims DROP INDEX {obsolete_idx}")
                )
            except Exception as exc:
                logger.debug("drop %s skipped: %s", obsolete_idx, exc)

        try:
            await conn.execute(
                text(
                    """
                    DELETE c FROM credit_activity_claims c
                    INNER JOIN (
                        SELECT activity_id, user_id, MIN(id) AS keep_id
                        FROM credit_activity_claims
                        GROUP BY activity_id, user_id
                        HAVING COUNT(*) > 1
                    ) d ON c.activity_id = d.activity_id
                       AND c.user_id = d.user_id
                       AND c.id <> d.keep_id
                    """
                )
            )
        except Exception as exc:
            logger.debug("dedupe credit_activity_claims skipped: %s", exc)

        try:
            await conn.execute(
                text(
                    "UPDATE credit_activity_claims SET claim_seq = 1 "
                    "WHERE claim_seq <> 1 OR claim_seq IS NULL"
                )
            )
        except Exception as exc:
            logger.debug("normalize claim_seq skipped: %s", exc)

        try:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_credit_activity_claims_activity_user "
                    "ON credit_activity_claims (activity_id, user_id)"
                )
            )
        except Exception as exc:
            if "Duplicate key name" not in str(exc) and "already exists" not in str(exc).lower():
                logger.debug("uq_credit_activity_claims_activity_user skipped: %s", exc)

        try:
            await conn.execute(
                text("UPDATE credit_activities SET per_user_limit = 1 WHERE per_user_limit <> 1")
            )
        except Exception as exc:
            logger.debug("force per_user_limit=1 skipped: %s", exc)

        extra_credit_tx_indexes = [
            "CREATE INDEX ix_credit_transactions_job_id ON credit_transactions (job_id)",
        ]
        for ddl in extra_credit_tx_indexes:
            try:
                await conn.execute(text(ddl))
            except Exception as exc:
                if "Duplicate key name" in str(exc) or "already exists" in str(exc).lower():
                    continue
                logger.debug("Index DDL skipped (%s): %s", ddl[:60], exc)

        # 订阅发放：旧 (subscription_id, period_key) → 新 (user_id, period_key)
        for obsolete_idx in (
            "ix_subscription_grants_period",
            "uq_subscription_grants_period",
        ):
            try:
                await conn.execute(text(f"ALTER TABLE subscription_grants DROP INDEX {obsolete_idx}"))
            except Exception as exc:
                logger.debug("drop %s skipped: %s", obsolete_idx, exc)

        try:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_subscription_grants_user_period "
                    "ON subscription_grants (user_id, period_key)"
                )
            )
        except Exception as exc:
            if "Duplicate key name" not in str(exc) and "already exists" not in str(exc).lower():
                logger.debug("uq_subscription_grants_user_period skipped: %s", exc)

        # 流水：旧单列 reference_key UNIQUE → 三列 (job_id, user_id, reference_key)
        for obsolete_idx in (
            "ix_credit_transactions_reference_key",
            "reference_key",
        ):
            try:
                await conn.execute(text(f"ALTER TABLE credit_transactions DROP INDEX {obsolete_idx}"))
            except Exception as exc:
                logger.debug("drop credit_transactions %s skipped: %s", obsolete_idx, exc)

        try:
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_credit_transactions_job_user_ref "
                    "ON credit_transactions (job_id, user_id, reference_key)"
                )
            )
        except Exception as exc:
            if "Duplicate key name" not in str(exc) and "already exists" not in str(exc).lower():
                logger.debug("uq_credit_transactions_job_user_ref skipped: %s", exc)

        # subscription_grants.lot_id 允许先占位后入账
        try:
            await conn.execute(
                text("ALTER TABLE subscription_grants MODIFY COLUMN lot_id BIGINT UNSIGNED NULL")
            )
        except Exception as exc:
            logger.debug("subscription_grants lot_id nullable skipped: %s", exc)

        for seed_sql in (
            "UPDATE subscription_plans SET storage_gb = 20 WHERE code = 'monthly_basic' AND storage_gb = 0",
            "UPDATE subscription_plans SET storage_gb = 100 WHERE code = 'monthly_pro' AND storage_gb = 0",
        ):
            try:
                await conn.execute(text(seed_sql))
            except Exception as exc:
                logger.debug("subscription plan storage_gb seed skipped: %s", exc)

        await conn.execute(
            text("UPDATE users SET role = 'admin' WHERE id = 1 AND role = 'user'")
        )

    from ..models.database import async_session
    from ..models.project import Project
    from ..services.project_scope import new_storage_folder
    from sqlalchemy import select

    async with async_session() as db:
        result = await db.execute(
            select(Project).where(
                (Project.storage_folder.is_(None)) | (Project.storage_folder == "")
            )
        )
        projects = list(result.scalars().all())
        for project in projects:
            project.storage_folder = new_storage_folder()
        if projects:
            await db.commit()

    from ..services.model_concurrency import ensure_model_runtime_slots_table

    await ensure_model_runtime_slots_table()

    from ..models.database import async_session
    from ..services.credit_transaction_backfill import (
        backfill_missing_job_credit_transactions,
        repair_credit_transaction_balances,
    )

    from ..services.job_ids import ensure_job_id_sequence_seeded

    async with async_session() as db:
        try:
            await ensure_job_id_sequence_seeded(db)
            await backfill_missing_job_credit_transactions(db)
            await repair_credit_transaction_balances(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Credit transaction backfill failed")

    # 存储占用对账独立事务：即使上面的算力对账失败，也要保证用户云存储占用被正确重算
    #（排除已软删除/隐藏的项目），避免删除项目后占用不减少。
    async with async_session() as db:
        try:
            from ..services.storage_quota import backfill_all_users_storage_used_bytes

            await backfill_all_users_storage_used_bytes(db)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Storage usage backfill failed")

    logger.info("MySQL schema ready")
