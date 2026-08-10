# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Add persisted local schedule governance tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_schedule_governance"
down_revision = "0001_system_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_definitions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("trigger_config_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "timezone",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'Asia/Shanghai'"),
        ),
        sa.Column("is_enabled", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column(
            "max_concurrent_runs", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "misfire_policy",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'skip'"),
        ),
        sa.Column("created_by", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "job_type IN ('crawl', 'ranking_update', 'asset_download')",
            name="ck_schedule_definitions_job_type",
        ),
        sa.CheckConstraint(
            "trigger_type IN ('cron', 'date')",
            name="ck_schedule_definitions_trigger_type",
        ),
        sa.CheckConstraint("is_enabled IN (0, 1)", name="ck_schedule_definitions_enabled"),
        sa.CheckConstraint("max_retries >= 0", name="ck_schedule_definitions_max_retries"),
        sa.CheckConstraint(
            "max_concurrent_runs >= 1",
            name="ck_schedule_definitions_max_concurrent_runs",
        ),
        sa.CheckConstraint(
            "misfire_policy = 'skip'", name="ck_schedule_definitions_misfire_policy"
        ),
    )
    op.create_table(
        "schedule_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Text(),
            sa.ForeignKey("schedule_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Text(), unique=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_summary", sa.Text()),
        sa.Column(
            "retry_of_run_id",
            sa.Text(),
            sa.ForeignKey("schedule_runs.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_schedule_runs_attempt"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')",
            name="ck_schedule_runs_status",
        ),
    )
    op.create_table(
        "schedule_alerts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Text(),
            sa.ForeignKey("schedule_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey("schedule_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "severity IN ('warning', 'error')", name="ck_schedule_alerts_severity"
        ),
    )
    op.create_index(
        "idx_schedule_runs_schedule_status",
        "schedule_runs",
        ["schedule_id", "status"],
    )
    op.create_index("idx_schedule_runs_scheduled_at", "schedule_runs", ["scheduled_at"])
    op.create_index(
        "idx_schedule_alerts_schedule_created",
        "schedule_alerts",
        ["schedule_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("schedule_alerts")
    op.drop_table("schedule_runs")
    op.drop_table("schedule_definitions")
