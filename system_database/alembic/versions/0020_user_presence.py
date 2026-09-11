# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Track current WebGUI presence without retaining page history."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0020_user_presence"
down_revision = "0019_audit_instance_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "user_presence",
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("tab_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("page_label", sa.Text(), nullable=True),
        sa.Column(
            "is_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "tab_id"),
    )
    op.create_index(
        "idx_user_presence_user_heartbeat",
        "user_presence",
        ["user_id", "last_heartbeat_at"],
        unique=False,
    )
    op.create_index(
        "idx_user_presence_heartbeat",
        "user_presence",
        ["last_heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_user_presence_heartbeat", table_name="user_presence")
    op.drop_index("idx_user_presence_user_heartbeat", table_name="user_presence")
    op.drop_table("user_presence")
    op.drop_column("users", "last_online_at")
