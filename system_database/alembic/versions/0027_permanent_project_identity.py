# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Add a permanent UUID identity shared by creative and workflow rows."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0027_permanent_project_identity"
down_revision = "0026_video_params_zip_exports"
branch_labels = None
depends_on = None


_WORKFLOW_TABLES = (
    "project_members",
    "workflow_instances",
    "workflow_project_deletions",
    "workflow_tasks",
    "workflow_project_member_events",
)


def upgrade() -> None:
    # ``0025`` builds from live creative metadata. A fresh database therefore
    # already has this column/index, while an upgraded deployment does not.
    op.execute(
        sa.text(
            "ALTER TABLE creative_projects ADD COLUMN IF NOT EXISTS "
            "project_uid UUID DEFAULT gen_random_uuid()"
        )
    )
    op.execute(
        sa.text(
            "UPDATE creative_projects SET project_uid = gen_random_uuid() "
            "WHERE project_uid IS NULL"
        )
    )
    op.alter_column("creative_projects", "project_uid", nullable=False)
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_creative_projects_project_uid "
            "ON creative_projects(project_uid)"
        )
    )

    for table_name in _WORKFLOW_TABLES:
        op.add_column(
            table_name,
            sa.Column("project_uid", postgresql.UUID(as_uuid=True), nullable=True),
        )

    # Only canonical identities from the live creative store and unambiguous
    # numeric legacy IDs are backfilled. Other workflow domains stay nullable.
    store_expression = (
        "(SELECT value FROM creative_store_metadata WHERE key = 'store_id')"
    )
    for table_name in _WORKFLOW_TABLES:
        op.execute(
            sa.text(
                f'UPDATE "{table_name}" AS target '
                "SET project_uid = project.project_uid "
                "FROM creative_projects AS project "
                "WHERE target.project_uid IS NULL AND ("
                "target.project_id = project.id::text OR "
                "target.project_id = "
                f"('creative:' || {store_expression} || ':project:' || project.id::text)"
                ")"
            )
        )

    op.create_index(
        "idx_project_members_project_uid",
        "project_members",
        ["project_uid"],
    )
    op.create_index(
        "uq_project_members_uid_role",
        "project_members",
        ["project_uid", "user_id", "project_role"],
        unique=True,
        postgresql_where=sa.text("project_uid IS NOT NULL"),
    )
    op.create_index(
        "idx_workflow_instances_project_uid",
        "workflow_instances",
        ["project_uid"],
    )
    op.create_index(
        "idx_workflow_project_deletions_project_uid",
        "workflow_project_deletions",
        ["project_uid"],
    )
    op.create_index(
        "idx_workflow_tasks_project_uid",
        "workflow_tasks",
        ["project_uid"],
    )
    op.create_index(
        "idx_workflow_project_member_events_project_uid",
        "workflow_project_member_events",
        ["project_uid", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_workflow_project_member_events_project_uid",
        table_name="workflow_project_member_events",
    )
    op.drop_index("idx_workflow_tasks_project_uid", table_name="workflow_tasks")
    op.drop_index(
        "idx_workflow_project_deletions_project_uid",
        table_name="workflow_project_deletions",
    )
    op.drop_index(
        "idx_workflow_instances_project_uid",
        table_name="workflow_instances",
    )
    op.drop_index("uq_project_members_uid_role", table_name="project_members")
    op.drop_index("idx_project_members_project_uid", table_name="project_members")
    for table_name in reversed(_WORKFLOW_TABLES):
        op.drop_column(table_name, "project_uid")
    op.drop_index(
        "uq_creative_projects_project_uid", table_name="creative_projects"
    )
    op.drop_column("creative_projects", "project_uid")
