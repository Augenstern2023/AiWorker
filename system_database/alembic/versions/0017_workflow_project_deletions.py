# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Coordinate project deletion with concurrent workflow writes."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0017_workflow_project_deletions"
down_revision = "0016_rank_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_project_deletions",
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("owner_token", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column(
            "project_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'deleting'"),
        ),
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
            "status = 'deleting'",
            name="ck_workflow_project_deletions_status",
        ),
        sa.PrimaryKeyConstraint(
            "project_id", name="pk_workflow_project_deletions"
        ),
    )
def downgrade() -> None:
    op.drop_table("workflow_project_deletions")
