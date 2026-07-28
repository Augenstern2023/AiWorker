# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Use PostgreSQL-native timestamps and JSON for system runtime data."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_runtime_timestamptz_jsonb"
down_revision = "0003_merge_governance_heads"
branch_labels = None
depends_on = None


def _timestamp(table: str, column: str, *, epoch: bool = False, nullable: bool = True) -> None:
    expression = (
        f"to_timestamp({column})" if epoch else f"NULLIF({column}, '')::timestamptz"
    )
    op.alter_column(
        table,
        column,
        existing_type=sa.Float() if epoch else sa.Text(),
        type_=sa.DateTime(timezone=True),
        postgresql_using=expression,
        nullable=nullable,
        server_default=sa.text("now()") if not nullable else None,
    )


def _json(table: str, column: str, *, nullable: bool = True, default: str | None = None) -> None:
    # PostgreSQL will not cast a legacy TEXT default during TYPE conversion.
    op.alter_column(table, column, existing_type=sa.Text(), server_default=None)
    op.alter_column(
        table,
        column,
        existing_type=sa.Text(),
        type_=postgresql.JSONB(),
        postgresql_using=f"{column}::jsonb",
        nullable=nullable,
        server_default=sa.text(default) if default else None,
    )


def upgrade() -> None:
    for table, column, nullable in (
        ("sessions", "expires_at", False), ("sessions", "last_seen_at", False),
        ("sessions", "revoked_at", True), ("sessions", "created_at", False),
        ("workflow_instances", "created_at", False), ("workflow_instances", "updated_at", False),
        ("workflow_tasks", "due_at", True), ("workflow_tasks", "created_at", False),
        ("workflow_tasks", "updated_at", False), ("workflow_task_jobs", "created_at", False),
        ("workflow_task_events", "created_at", False), ("approval_records", "created_at", False),
        ("audit_logs", "created_at", False), ("workflow_project_member_events", "created_at", False),
    ):
        _timestamp(table, column, nullable=nullable)
    for column, nullable in (("submitted_at", True), ("reviewed_at", True), ("created_at", False), ("updated_at", False)):
        _timestamp("workflow_submissions", column, epoch=True, nullable=nullable)
    for table, column, nullable, default in (
        ("workflow_task_events", "details_json", False, "'{}'::jsonb"),
        ("workflow_submissions", "content_snapshot_json", False, None),
        ("audit_logs", "actor_role_snapshot_json", False, "'[]'::jsonb"),
        ("audit_logs", "details_json", False, "'{}'::jsonb"),
        ("audit_logs", "before_summary_json", True, None),
        ("audit_logs", "after_summary_json", True, None),
        ("workflow_project_member_events", "before_json", True, None),
        ("workflow_project_member_events", "after_json", True, None),
    ):
        _json(table, column, nullable=nullable, default=default)


def downgrade() -> None:
    raise RuntimeError("runtime PostgreSQL type migration is intentionally one-way")
