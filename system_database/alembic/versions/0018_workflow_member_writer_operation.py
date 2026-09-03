# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Track Writer ownership for automatic workflow membership changes."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018_member_writer_operation"
down_revision = "0017_workflow_project_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_project_member_events",
        sa.Column("writer_operation_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_project_member_events", "writer_operation_id")
